"""
LNURL endpoints for Bitcoin Switch with Taproot Assets support.

This module handles LNURL payment flows for both standard Lightning Network
payments and Taproot Asset payments. It includes RFQ (Request for Quote)
functionality for asset pricing and payment processing.
"""
from http import HTTPStatus
from typing import Dict, Optional, Tuple, Union, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import JSONResponse
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

# Router for LNURL endpoints
bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


def is_asset_enabled_switch(switch: Optional[Switch]) -> bool:
    """
    Check if a switch is configured to accept Taproot Assets.

    Args:
        switch: The switch configuration to check

    Returns:
        bool: True if the switch accepts Taproot Assets and has asset IDs configured
    """
    return bool(
        switch and
        switch.accepts_assets and
        switch.accepted_asset_ids
    )


async def validate_switch_params(
    switch: Any,
    pin: int,
    duration: int,
    variable: bool,
    comment: bool
) -> bool:
    """
    Validate switch parameters match configuration.

    Args:
        switch: Switch configuration
        pin: GPIO pin number
        duration: Activation duration
        variable: Variable time flag
        comment: Comment enabled flag

    Returns:
        bool: True if parameters are valid
    """
    for _switch in switch.switches:
        if (
            _switch.pin == pin and
            _switch.duration == duration and
            bool(_switch.variable) == bool(variable) and
            bool(_switch.comment) == bool(comment)
        ):
            return True
    return False


async def validate_taproot_payment(
    current_switch: Switch,
    asset_id: str,
    switch_wallet: str,
    user_id: str,
    bitcoinswitch_payment: BitcoinswitchPayment,
) -> Tuple[bool, Optional[str]]:
    """
    Validate Taproot Asset payment requirements.

    Checks if:
    - Switch accepts assets
    - Taproot is available
    - Asset ID is valid
    - Rate quote is valid and within tolerance

    Args:
        current_switch: Switch configuration
        asset_id: Taproot Asset ID
        switch_wallet: Wallet ID
        user_id: User ID
        bitcoinswitch_payment: Payment record

    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message if invalid)
    """
    # Verify basic requirements
    if not is_asset_enabled_switch(current_switch):
        return False, None

    if not await TaprootIntegration.is_taproot_available():
        return False, None

    if not asset_id or asset_id not in current_switch.accepted_asset_ids:
        return False, "Invalid asset ID for this switch"

    # Check rate quote validity
    if all([
        bitcoinswitch_payment.quoted_rate,
        bitcoinswitch_payment.quoted_at,
        bitcoinswitch_payment.asset_amount
    ]):
        if RateService.is_rate_expired(bitcoinswitch_payment.quoted_at):
            return False, "Price quote has expired. Please scan the QR code again for current pricing."
        
        current_rate = await RateService.get_current_rate(
            asset_id=asset_id,
            wallet_id=switch_wallet,
            user_id=user_id,
            asset_amount=bitcoinswitch_payment.asset_amount
        )
        
        if current_rate and not RateService.is_rate_within_tolerance(
            bitcoinswitch_payment.quoted_rate,
            current_rate
        ):
            return False, "Exchange rate has changed significantly. Please scan the QR code again for current pricing."
    
    return True, None


def create_error_response(message: str, status_code: int = HTTPStatus.BAD_REQUEST) -> JSONResponse:
    """Create standardized error response."""
    return JSONResponse(
        status_code=status_code,
        content={"status": "ERROR", "reason": message}
    )


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
) -> JSONResponse:
    """
    Handle initial LNURL parameter request.
    
    Creates payment record and returns LNURL response with payment options.
    Supports both standard Lightning and Taproot Asset payments.
    """
    # Validate switch exists
    switch = await get_bitcoinswitch(bitcoinswitch_id)
    if not switch:
        return create_error_response(
            f"Bitcoin Switch {bitcoinswitch_id} not found",
            HTTPStatus.NOT_FOUND
        )

    try:
        # Calculate payment amount
        price_msat = int(
            (
                await fiat_amount_as_satoshis(float(amount), switch.currency)
                if switch.currency != "sat"
                else float(amount)
            )
            * 1000
        )
    except ValueError as e:
        logger.error(f"Invalid amount format: {amount}", error=str(e))
        return create_error_response("Invalid amount format")

    # Validate parameters
    try:
        pin_int = int(pin)
        duration_int = int(duration)
    except ValueError:
        return create_error_response("Invalid pin or duration format")

    if not await validate_switch_params(switch, pin_int, duration_int, variable, comment):
        return create_error_response("Invalid switch parameters")

    # Create payment record
    bitcoinswitch_payment = await create_bitcoinswitch_payment(
        bitcoinswitch_id=switch.id,
        payload=duration,
        amount_msat=price_msat,
        pin=pin_int,
        payment_hash="not yet set",
    )
    if not bitcoinswitch_payment:
        return create_error_response("Failed to create payment record")

    # Build basic LNURL response
    callback_url = str(
        request.url_for(
            "bitcoinswitch.lnurl_callback",
            payment_id=bitcoinswitch_payment.id
        )
    )
    
    resp = {
        "tag": "payRequest",
        "callback": f"{callback_url}?variable={variable}",
        "minSendable": price_msat,
        "maxSendable": price_msat,
        "commentAllowed": config.max_comment_length,
        "metadata": switch.lnurlpay_metadata,
    }

    # Handle Taproot Asset support
    current_switch = next(
        (s for s in switch.switches if s.pin == pin_int),
        None
    )
    
    if is_asset_enabled_switch(current_switch) and await TaprootIntegration.is_taproot_available():
        await handle_taproot_params(
            switch=switch,
            current_switch=current_switch,
            bitcoinswitch_payment=bitcoinswitch_payment,
            resp=resp
        )

    # Add optional parameters
    if comment:
        resp["commentAllowed"] = config.max_comment_length
    if variable is True:
        resp["maxSendable"] = price_msat * 360

    return JSONResponse(content=resp)


async def handle_taproot_params(
    switch: Any,
    current_switch: Switch,
    bitcoinswitch_payment: BitcoinswitchPayment,
    resp: Dict[str, Any]
) -> None:
    """Handle Taproot Asset specific LNURL parameters."""
    resp["acceptsAssets"] = True
    resp["acceptedAssetIds"] = current_switch.accepted_asset_ids
    resp["assetMetadata"] = {
        "supportsRfq": True,
        "message": "This switch accepts Taproot Assets via RFQ - pay with either sats or assets",
        "rfqEnabled": True
    }

    # Verify asset configuration
    if not current_switch.accepted_asset_ids:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Switch is configured to accept assets but no asset IDs are specified"
        )

    wallet = await get_wallet(switch.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Wallet not found"
        )

    try:
        await handle_rfq_quote(
            switch=switch,
            current_switch=current_switch,
            wallet=wallet,
            bitcoinswitch_payment=bitcoinswitch_payment,
            resp=resp
        )
    except Exception as e:
        logger.error("Failed to create RFQ quote", error=str(e))
        # Continue with original price_msat


async def handle_rfq_quote(
    switch: Any,
    current_switch: Switch,
    wallet: Any,
    bitcoinswitch_payment: BitcoinswitchPayment,
    resp: Dict[str, Any]
) -> None:
    """Handle RFQ quote creation for Taproot Asset payments."""
    from lnbits.extensions.taproot_assets.services.invoice_service import InvoiceService
    from lnbits.extensions.taproot_assets.models import TaprootInvoiceRequest
    from lnbits.bolt11 import decode as bolt11_decode

    asset_id = current_switch.accepted_asset_ids[0]
    asset_amount = int(current_switch.amount)

    rfq_request = TaprootInvoiceRequest(
        asset_id=asset_id,
        amount=asset_amount,
        description=f"{switch.title} - LNURL rate check",
        expiry=config.taproot_quote_expiry
    )

    rfq_invoice = await InvoiceService.create_invoice(
        data=rfq_request,
        user_id=wallet.user,
        wallet_id=switch.wallet
    )

    decoded = bolt11_decode(rfq_invoice.payment_request)
    if decoded.amount_msat:
        resp["minSendable"] = decoded.amount_msat
        resp["maxSendable"] = decoded.amount_msat

        bitcoinswitch_payment.rfq_invoice_hash = rfq_invoice.payment_hash
        bitcoinswitch_payment.rfq_asset_amount = asset_amount
        bitcoinswitch_payment.rfq_sat_amount = decoded.amount_msat / 1000
        await update_bitcoinswitch_payment(bitcoinswitch_payment)


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
) -> JSONResponse:
    """
    Handle LNURL callback request.
    
    Creates Lightning invoice or Taproot Asset invoice based on payment type.
    Supports variable time calculations and comments.
    """
    # Validate payment exists
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment_id)
    if not bitcoinswitch_payment:
        return create_error_response("Payment not found", HTTPStatus.NOT_FOUND)

    # Validate switch exists
    switch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not switch:
        await delete_bitcoinswitch_payment(payment_id)
        return create_error_response("Bitcoin Switch not found", HTTPStatus.NOT_FOUND)

    if not amount:
        return create_error_response("No amount provided")

    # Get switch configuration
    current_switch = next(
        (s for s in switch.switches if s.pin == bitcoinswitch_payment.pin),
        None
    )

    # Get wallet
    wallet = await get_wallet(switch.wallet)
    if not wallet:
        return create_error_response("Wallet not found", HTTPStatus.NOT_FOUND)

    # Handle Taproot Asset payments
    if (is_asset_enabled_switch(current_switch) and 
        await TaprootIntegration.is_taproot_available()):
        
        # Use first accepted asset if none specified
        if not asset_id or asset_id not in current_switch.accepted_asset_ids:
            asset_id = current_switch.accepted_asset_ids[0]

        # Validate Taproot payment
        is_valid, error_message = await validate_taproot_payment(
            current_switch=current_switch,
            asset_id=asset_id,
            switch_wallet=switch.wallet,
            user_id=wallet.user,
            bitcoinswitch_payment=bitcoinswitch_payment
        )

        if not is_valid:
            if error_message:
                return create_error_response(error_message)
            
            if is_asset_enabled_switch(current_switch):
                return create_error_response(
                    "This switch only accepts Taproot Asset payments. "
                    "Please use a wallet that supports Taproot Assets."
                )
        else:
            return await handle_taproot_payment(
                switch=switch,
                current_switch=current_switch,
                bitcoinswitch_payment=bitcoinswitch_payment,
                wallet=wallet,
                amount=amount,
                asset_id=asset_id,
                comment=comment,
                variable=variable,
                payment_id=payment_id
            )

    # Handle standard Lightning payment
    return await handle_lightning_payment(
        switch=switch,
        bitcoinswitch_payment=bitcoinswitch_payment,
        amount=amount,
        comment=comment,
        variable=variable,
        payment_id=payment_id
    )


async def handle_lightning_payment(
    switch: Any,
    bitcoinswitch_payment: BitcoinswitchPayment,
    amount: int,
    comment: Optional[str],
    variable: bool,
    payment_id: str
) -> JSONResponse:
    """Handle standard Lightning Network payment."""
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

    return JSONResponse(content={
        "pr": payment.bolt11,
        "successAction": {
            "tag": "message",
            "message": f"{int(amount / 1000)}sats sent",
        },
        "routes": [],
    })


async def handle_taproot_payment(
    switch: Any,
    current_switch: Switch,
    bitcoinswitch_payment: BitcoinswitchPayment,
    wallet: Any,
    amount: int,
    asset_id: str,
    comment: Optional[str],
    variable: bool,
    payment_id: str
) -> JSONResponse:
    """Handle Taproot Asset payment."""
    requested_sats = amount / 1000
    
    # Calculate asset amount
    asset_amount = calculate_asset_amount(
        bitcoinswitch_payment=bitcoinswitch_payment,
        requested_sats=requested_sats,
        current_switch=current_switch
    )

    # Determine invoice type based on payment method
    if asset_id:
        # Direct asset payment: User is paying WITH assets
        logger.info(f"Creating direct asset invoice for asset_id={asset_id}, amount={asset_amount}")
        taproot_result, taproot_error = await TaprootIntegration.create_direct_asset_invoice(
            asset_id=asset_id,
            amount=asset_amount,
            description=f"{switch.title} ({bitcoinswitch_payment.payload} ms)",
            wallet_id=switch.wallet,
            user_id=wallet.user,
            expiry=config.taproot_payment_expiry
        )
        
        # Update payment record for direct asset payment
        if taproot_result and not taproot_error:
            bitcoinswitch_payment.payment_hash = taproot_result["payment_hash"]
            bitcoinswitch_payment.is_taproot = True
            bitcoinswitch_payment.asset_id = asset_id
            bitcoinswitch_payment.is_direct_asset = True  # New field to track direct payments
            await update_bitcoinswitch_payment(bitcoinswitch_payment)

            return JSONResponse(content={
                "pr": taproot_result["payment_request"],
                "successAction": {
                    "tag": "message",
                    "message": f"Pay {asset_amount} units of {asset_id} directly",
                },
                "routes": [],
            })
    else:
        # RFQ payment: User pays sats, vendor gets assets (existing logic)
        logger.info(f"Creating RFQ invoice for asset conversion")
        taproot_result, taproot_error = await TaprootIntegration.create_rfq_invoice(
            asset_id=current_switch.accepted_asset_ids[0],  # Use switch config for RFQ
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
            expiry=config.taproot_payment_expiry
        )

    # Common error handling for both paths
    if not taproot_result or taproot_error:
        logger.error("Failed to create Taproot Asset invoice", error=taproot_error)
        return create_error_response("Failed to create Taproot Asset invoice")

    # Update payment record
    bitcoinswitch_payment.payment_hash = taproot_result["payment_hash"]
    bitcoinswitch_payment.is_taproot = True
    bitcoinswitch_payment.asset_id = asset_id if asset_id else current_switch.accepted_asset_ids[0]
    if not hasattr(bitcoinswitch_payment, 'is_direct_asset'):
        bitcoinswitch_payment.is_direct_asset = False
    await update_bitcoinswitch_payment(bitcoinswitch_payment)

    return JSONResponse(content={
        "pr": taproot_result["payment_request"],
        "successAction": {
            "tag": "message",
            "message": f"{asset_amount} units of {bitcoinswitch_payment.asset_id} requested",
        },
        "routes": [],
    })


def calculate_asset_amount(
    bitcoinswitch_payment: BitcoinswitchPayment,
    requested_sats: float,
    current_switch: Switch
) -> int:
    """Calculate asset amount based on RFQ rate or switch configuration."""
    if all([
        hasattr(bitcoinswitch_payment, 'rfq_sat_amount'),
        hasattr(bitcoinswitch_payment, 'rfq_asset_amount'),
        bitcoinswitch_payment.rfq_sat_amount is not None,
        bitcoinswitch_payment.rfq_asset_amount is not None,
        bitcoinswitch_payment.rfq_asset_amount > 0
    ]):
        rate_per_asset = bitcoinswitch_payment.rfq_sat_amount / bitcoinswitch_payment.rfq_asset_amount
        asset_amount = int(requested_sats / rate_per_asset)
        return max(1, asset_amount)
    else:
        asset_amount = int(current_switch.amount)
        logger.warning(f"No RFQ rate data, using switch config: {asset_amount} assets")
        return asset_amount