from http import HTTPStatus
from typing import Optional

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

bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


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
            continue
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
    
    if (current_switch and 
        current_switch.accepts_assets and 
        current_switch.accepted_asset_ids and 
        await TaprootIntegration.is_taproot_available()):
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
    
    # Check if we should create a Taproot Asset invoice
    if (current_switch and
        current_switch.accepts_assets and 
        asset_id and 
        asset_id in current_switch.accepted_asset_ids and
        await TaprootIntegration.is_taproot_available()):
        
        # Get wallet info
        wallet = await get_wallet(switch.wallet)
        if not wallet:
            return {"status": "ERROR", "reason": "Wallet not found"}
        
        logger.info(f"Creating RFQ invoice for asset {asset_id}, amount={amount // 1000} sats")
        
        # Create Taproot Asset invoice using RFQ process
        # This creates an invoice that can be paid with either sats or the asset
        taproot_result = await TaprootIntegration.create_rfq_invoice(
            asset_id=asset_id,
            amount=amount // 1000,  # Convert from millisats to sats
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
                "payload": bitcoinswitch_payment.payload
            },
            expiry=3600  # 1 hour expiry
        )
        
        if taproot_result:
            logger.info(f"RFQ invoice created successfully: {taproot_result['payment_hash']}")
            # Update payment record
            bitcoinswitch_payment.payment_hash = taproot_result["payment_hash"]
            bitcoinswitch_payment.is_taproot = True
            bitcoinswitch_payment.asset_id = asset_id
            await update_bitcoinswitch_payment(bitcoinswitch_payment)
            
            message = f"{int(amount / 1000)}sats worth of {asset_id} requested"
            if switch.password and switch.password != comment:
                message = f"{message}, but password was incorrect! :("
            
            return {
                "pr": taproot_result["payment_request"],
                "successAction": {
                    "tag": "message",
                    "message": message,
                },
                "routes": [],
            }
        else:
            logger.error("Failed to create RFQ invoice - taproot_result is None")
            return {"status": "ERROR", "reason": "Failed to create taproot asset invoice"}
    
    # Fall back to regular Lightning invoice
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

    message = f"{int(amount / 1000)}sats sent"
    if switch.password and switch.password != comment:
        message = f"{message}, but password was incorrect! :("

    return {
        "pr": payment.bolt11,
        "successAction": {
            "tag": "message",
            "message": message,
        },
        "routes": [],
    }
