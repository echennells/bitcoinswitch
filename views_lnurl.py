from http import HTTPStatus
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from lnbits.core.services import create_invoice
from lnbits.core.crud import get_wallet
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis
from loguru import logger

from .crud import (
    create_bitcoinswitch_payment,
    delete_bitcoinswitch_payment,
    get_bitcoinswitch,
    get_bitcoinswitch_payment,
    update_bitcoinswitch_payment,
)
from .services.taproot_integration import TaprootIntegration
from .services.rate_service import RateService
from .models import Switch
from .services.config import config

bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


def is_asset_enabled_switch(switch: Optional[Switch]) -> bool:
    """Check if a switch is configured to accept taproot assets."""
    return (
        switch is not None and
        switch.accepts_assets and
        switch.accepted_asset_ids and
        len(switch.accepted_asset_ids) > 0
    )


@bitcoinswitch_lnurl_router.get(
    "/{bitcoinswitch_id}",
    status_code=HTTPStatus.OK,
    name="bitcoinswitch.lnurl_params",
)
async def lnurl_params(
    request: Request,
    bitcoinswitch_id: str,
    pin: str,
    amount: str,
    duration: str,
    variable: bool = Query(None),
    comment: bool = Query(None),
):
    switch = await get_bitcoinswitch(bitcoinswitch_id)
    if not switch:
        return {
            "status": "ERROR",
            "reason": f"bitcoinswitch {bitcoinswitch_id} not found on this server",
        }

    price_msat = int(
        (
            await fiat_amount_as_satoshis(float(amount), switch.currency)
            if switch.currency != "sat"
            else float(amount)
        )
        * 1000
    )

    # Check they're not trying to trick the switch!
    check = False
    current_switch = None
    for _switch in switch.switches:
        if (
            _switch.pin == int(pin)
            and _switch.duration == int(duration)
            and bool(_switch.variable) == bool(variable)
            and bool(_switch.comment) == bool(comment)
        ):
            check = True
            current_switch = _switch
            break
            
    if not check:
        return {"status": "ERROR", "reason": "Extra params wrong"}

    bitcoinswitch_payment = await create_bitcoinswitch_payment(
        bitcoinswitch_id=switch.id,
        payload=duration,
        amount_msat=price_msat,
        pin=int(pin),
        payment_hash="not yet set",
    )
    if not bitcoinswitch_payment:
        return {"status": "ERROR", "reason": "Could not create payment."}

    url = str(
        request.url_for(
            "bitcoinswitch.lnurl_callback", payment_id=bitcoinswitch_payment.id
        )
    )
    resp = {
        "tag": "payRequest",
        "callback": f"{url}?variable={variable}",
        "minSendable": price_msat,
        "maxSendable": price_msat,
        "commentAllowed": 255,
        "metadata": switch.lnurlpay_metadata,
    }
    
    if is_asset_enabled_switch(current_switch) and await TaprootIntegration.is_taproot_available():
        resp["acceptsAssets"] = True
        resp["acceptedAssetIds"] = current_switch.accepted_asset_ids
        resp["assetMetadata"] = {
            "supportsRfq": True,
            "message": "This switch accepts Taproot Assets via RFQ - pay with either sats or assets",
            "rfqEnabled": True
        }
    
    if comment:
        resp["commentAllowed"] = 1500
    if variable is True:
        resp["maxSendable"] = price_msat * 360
    return resp


@bitcoinswitch_lnurl_router.get(
    "/cb/{payment_id}",
    status_code=HTTPStatus.OK,
    name="bitcoinswitch.lnurl_callback",
)
async def lnurl_callback(
    payment_id: str,
    variable: bool = Query(None),
    amount: int = Query(None),
    comment: str = Query(None),
    asset_id: Optional[str] = Query(None),
):
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment_id)
    if not bitcoinswitch_payment:
        return {"status": "ERROR", "reason": "Payment record not found"}
        
    switch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not switch:
        await delete_bitcoinswitch_payment(payment_id)
        return {"status": "ERROR", "reason": "Switch not found"}

    if not amount:
        return {"status": "ERROR", "reason": "No amount"}
    
    # Get the switch configuration
    current_switch = None
    for s in switch.switches:
        if s.pin == bitcoinswitch_payment.pin:
            current_switch = s
            break
    
    wallet = await get_wallet(switch.wallet)
    if not wallet:
        return {"status": "ERROR", "reason": "Wallet not found"}

    # Handle taproot asset payment if applicable
    if asset_id and is_asset_enabled_switch(current_switch):
        if asset_id not in current_switch.accepted_asset_ids:
            return {
                "status": "ERROR",
                "reason": "This asset is not accepted by this switch"
            }
            
        if not await TaprootIntegration.is_taproot_available():
            return {
                "status": "ERROR",
                "reason": "Taproot Assets system is currently unavailable"
            }

        # Check rate validity if quote exists
        if (bitcoinswitch_payment.quoted_rate and 
            bitcoinswitch_payment.quoted_at and
            bitcoinswitch_payment.asset_amount):
            
            # Check if quote has expired
            if RateService.is_rate_expired(bitcoinswitch_payment.quoted_at):
                return {
                    "status": "ERROR",
                    "reason": "Price quote has expired. Please scan the QR code again for current pricing."
                }
            
            # Get current rate and check tolerance
            current_rate = await RateService.get_current_rate(
                asset_id=asset_id,
                wallet_id=switch.wallet,
                user_id=wallet.user,
                asset_amount=bitcoinswitch_payment.asset_amount
            )
            
            if current_rate:
                if not RateService.is_rate_within_tolerance(
                    bitcoinswitch_payment.quoted_rate,
                    current_rate
                ):
                    return {
                        "status": "ERROR",
                        "reason": "Exchange rate has changed significantly. Please scan the QR code again for current pricing."
                    }
            else:
                return {
                    "status": "ERROR",
                    "reason": "Unable to verify current exchange rate. Please try again."
                }

        # Calculate asset amount from sats or use switch config
        requested_sats = amount / 1000
        
        if (hasattr(bitcoinswitch_payment, 'rfq_sat_amount') and 
            hasattr(bitcoinswitch_payment, 'rfq_asset_amount') and
            bitcoinswitch_payment.rfq_sat_amount is not None and
            bitcoinswitch_payment.rfq_asset_amount is not None and
            bitcoinswitch_payment.rfq_asset_amount > 0):
            # Calculate from RFQ
            rate_per_asset = bitcoinswitch_payment.rfq_sat_amount / bitcoinswitch_payment.rfq_asset_amount
            asset_amount = int(requested_sats / rate_per_asset)
            if asset_amount < 1:
                asset_amount = 1
        else:
            # Fallback to switch configuration
            asset_amount = int(current_switch.amount)
            logger.warning(f"No RFQ rate data, using switch config: {asset_amount} assets")
        
        # Create Taproot Asset invoice
        taproot_result = await TaprootIntegration.create_rfq_invoice(
            asset_id=asset_id,
            amount=asset_amount,
            description=f"{switch.title} ({bitcoinswitch_payment.payload} ms)",
            wallet_id=switch.wallet,
            user_id=wallet.user,
            extra={
                "pin": str(bitcoinswitch_payment.pin),
                "amount": str(int(amount)),
                "comment": comment,
                "variable": variable,
                "id": payment_id,
                "switch_id": switch.id,
                "payload": bitcoinswitch_payment.payload,
                "asset_amount": str(asset_amount)
            },
            expiry=3600  # 1 hour
        )
        
        if not taproot_result:
            return {
                "status": "ERROR",
                "reason": "Failed to create taproot asset invoice"
            }

        # Update payment record
        bitcoinswitch_payment.payment_hash = taproot_result["payment_hash"]
        bitcoinswitch_payment.is_taproot = True
        bitcoinswitch_payment.asset_id = asset_id
        await update_bitcoinswitch_payment(bitcoinswitch_payment)
        
        return {
            "pr": taproot_result["payment_request"],
            "successAction": {
                "tag": "message",
                "message": f"{asset_amount} units of {asset_id} requested",
            },
            "routes": [],
        }

    # Handle regular Lightning payment
    payment = await create_invoice(
        wallet_id=switch.wallet,
        amount=int(amount / 1000),
        memo=f"{switch.title} ({bitcoinswitch_payment.payload} ms)",
        unhashed_description=switch.lnurlpay_metadata.encode(),
        extra={
            "tag": "Switch",
            "pin": str(bitcoinswitch_payment.pin),
            "amount": str(int(amount)),
            "comment": comment,
            "variable": variable,
            "id": payment_id,
        },
    )
    
    bitcoinswitch_payment.payment_hash = payment.payment_hash
    await update_bitcoinswitch_payment(bitcoinswitch_payment)

    return {
        "pr": payment.bolt11,
        "successAction": {
            "tag": "message",
            "message": f"{int(amount / 1000)}sats sent",
        },
        "routes": [],
    }