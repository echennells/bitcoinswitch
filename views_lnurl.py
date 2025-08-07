from http import HTTPStatus
from typing import Optional, Tuple
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
from .models import Switch, BitcoinswitchPayment
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


async def validate_taproot_payment(
    current_switch: Switch,
    asset_id: str,
    switch_wallet: str,
    user_id: str,
    bitcoinswitch_payment: BitcoinswitchPayment,
) -> Tuple[bool, Optional[str]]:
    """
    Validate taproot payment requirements.
    Returns (is_valid, error_message if invalid)
    """
    # Check if this switch can accept taproot assets
    if not is_asset_enabled_switch(current_switch):
        return False, None

    # Verify taproot is available
    taproot_available, _ = await TaprootIntegration.is_taproot_available()
    if not taproot_available:
        return False, None

    # Verify asset_id is accepted
    if not asset_id or asset_id not in current_switch.accepted_asset_ids:
        return False, "Invalid asset ID for this switch"

    # Check rate quotes if they exist
    if (bitcoinswitch_payment.quoted_rate and 
        bitcoinswitch_payment.quoted_at and
        bitcoinswitch_payment.asset_amount):
        
        # Check if quote has expired
        if RateService.is_rate_expired(bitcoinswitch_payment.quoted_at):
            return False, "Price quote has expired. Please scan the QR code again for current pricing."
        
        # Get current rate and check tolerance
        current_rate = await RateService.get_current_rate(
            asset_id=asset_id,
            wallet_id=switch_wallet,
            user_id=user_id,
            asset_amount=bitcoinswitch_payment.asset_amount
        )
        
        if current_rate:
            if not RateService.is_rate_within_tolerance(
                bitcoinswitch_payment.quoted_rate,
                current_rate
            ):
                return False, "Exchange rate has changed significantly. Please scan the QR code again for current pricing."
    
    return True, None


@bitcoinswitch_lnurl_router.get(
    "{bitcoinswitch_id}",
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
    for _switch in switch.switches:
        if (
            _switch.pin == int(pin)
            and _switch.duration == int(duration)
            and bool(_switch.variable) == bool(variable)
            and bool(_switch.comment) == bool(comment)
        ):
            check = True
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
    
    # Check if switch accepts assets and taproot is available
    current_switch = None
    for s in switch.switches:
        if s.pin == int(pin):
            current_switch = s
            break
    
    taproot_available, _ = await TaprootIntegration.is_taproot_available()
    if is_asset_enabled_switch(current_switch) and taproot_available:
        resp["acceptsAssets"] = True
        resp["acceptedAssetIds"] = current_switch.accepted_asset_ids
        resp["assetMetadata"] = {
            "supportsRfq": True,
            "message": "This switch accepts Taproot Assets via RFQ - pay with either sats or assets",
            "rfqEnabled": True
        }
        
        # For asset-accepting switches, we need to use RFQ to determine the sat amount
        if not current_switch.accepted_asset_ids:
            logger.error("Asset-accepting switch has no accepted_asset_ids configured")
            return {"status": "ERROR", "reason": "Switch is configured to accept assets but no asset IDs are specified"}
        
        asset_id = current_switch.accepted_asset_ids[0]  # Use first accepted asset
        asset_amount = int(current_switch.amount)  # The switch's configured asset amount
        
        # Get wallet info for invoice creation
        wallet = await get_wallet(switch.wallet)
        if wallet:
            try:
                # Create an invoice for the switch's asset amount
                from lnbits.extensions.taproot_assets.services.invoice_service import InvoiceService
                from lnbits.extensions.taproot_assets.models import TaprootInvoiceRequest
                
                rfq_request = TaprootInvoiceRequest(
                    asset_id=asset_id,
                    amount=asset_amount,
                    description=f"{switch.title} - LNURL rate check",
                    expiry=300  # 5 minutes
                )
                
                rfq_invoice = await InvoiceService.create_invoice(
                    data=rfq_request,
                    user_id=wallet.user,
                    wallet_id=switch.wallet
                )
                
                # Decode the invoice to get the sat amount that RFQ determined
                from lnbits.bolt11 import decode as bolt11_decode
                decoded = bolt11_decode(rfq_invoice.payment_request)
                
                if decoded.amount_msat:
                    # Set LNURL amounts based on what RFQ returned
                    resp["minSendable"] = decoded.amount_msat
                    resp["maxSendable"] = decoded.amount_msat
                    
                    # Store the RFQ details for validation in callback
                    bitcoinswitch_payment.rfq_invoice_hash = rfq_invoice.payment_hash
                    bitcoinswitch_payment.rfq_asset_amount = asset_amount
                    bitcoinswitch_payment.rfq_sat_amount = decoded.amount_msat / 1000
                    await update_bitcoinswitch_payment(bitcoinswitch_payment)
                else:
                    logger.warning("RFQ invoice has no amount, falling back to price_msat")
                    
            except Exception as e:
                logger.error(f"Failed to create RFQ invoice for LNURL: {e}")
                # Fall back to original price_msat if RFQ fails
    
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
        return {"status": "ERROR", "reason": "bitcoinswitchpayment not found."}
    switch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not switch:
        await delete_bitcoinswitch_payment(payment_id)
        return {"status": "ERROR", "reason": "bitcoinswitch not found."}

    if not amount:
        return {"status": "ERROR", "reason": "No amount"}
    
    # Get the switch configuration to check asset support
    current_switch = None
    for s in switch.switches:
        if s.pin == bitcoinswitch_payment.pin:
            current_switch = s
            break
    
    # Get wallet info early as we need it for both paths
    wallet = await get_wallet(switch.wallet)
    if not wallet:
        return {"status": "ERROR", "reason": "Wallet not found"}

    # If no asset_id provided but switch accepts assets, use first accepted asset
    taproot_available, _ = await TaprootIntegration.is_taproot_available()
    if (is_asset_enabled_switch(current_switch) and 
        taproot_available and
        not asset_id):
        asset_id = current_switch.accepted_asset_ids[0]
    
    # Validate taproot payment requirements
    is_valid, error_message = await validate_taproot_payment(
        current_switch=current_switch,
        asset_id=asset_id,
        switch_wallet=switch.wallet,
        user_id=wallet.user,
        bitcoinswitch_payment=bitcoinswitch_payment
    )

    # Handle non-valid taproot cases
    if not is_valid:
        if error_message:
            return {"status": "ERROR", "reason": error_message}
            
        # If switch only accepts assets, error out
        if is_asset_enabled_switch(current_switch):
            return {
                "status": "ERROR", 
                "reason": "This switch only accepts Taproot Asset payments. Please use a wallet that supports Taproot Assets."
            }
            
        # Otherwise fall through to regular Lightning payment
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

    # Handle valid taproot payment
    requested_sats = amount / 1000
    
    # Calculate asset amount - use RFQ rate if available
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
    taproot_result, taproot_error = await TaprootIntegration.create_rfq_invoice(
        asset_id=asset_id,
        amount=asset_amount,  # Asset units, not sats
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
        expiry=3600  # 1 hour expiry
    )
    
    if not taproot_result or taproot_error:
        logger.error(f"Failed to create RFQ invoice: {taproot_error}")
        return {"status": "ERROR", "reason": "Failed to create taproot asset invoice"}

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