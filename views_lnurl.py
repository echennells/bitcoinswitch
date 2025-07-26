from http import HTTPStatus
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass

from fastapi import APIRouter, Query, Request, HTTPException
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
from .services.taproot_integration import TaprootIntegration, TaprootError
from .services.rate_service import RateService
from .models import Switch, BitcoinswitchPayment
from .services.config import config

bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


@dataclass
class PaymentValidationError(Exception):
    """Custom error for payment validation failures."""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_lnurl_response(self):
        """Convert to LNURL error response format."""
        return {
            "status": "ERROR",
            "reason": self.message,
            "code": self.code,
            "details": self.details
        }


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
) -> Tuple[bool, Optional[PaymentValidationError]]:
    """
    Validate taproot payment requirements.
    Returns (is_valid, error_if_not_valid)
    """
    try:
        # Check if this switch can accept taproot assets
        if not is_asset_enabled_switch(current_switch):
            return False, PaymentValidationError(
                code="ASSETS_NOT_ENABLED",
                message="This switch is not configured to accept asset payments"
            )

        # Verify taproot is available
        taproot_available, taproot_error = await TaprootIntegration.is_taproot_available()
        if not taproot_available:
            return False, PaymentValidationError(
                code="TAPROOT_UNAVAILABLE",
                message="Taproot Assets system is currently unavailable",
                details={"error": str(taproot_error) if taproot_error else None}
            )

        # Verify asset_id is accepted
        if not asset_id:
            return False, PaymentValidationError(
                code="MISSING_ASSET_ID",
                message="Asset ID is required for asset payments"
            )
            
        if asset_id not in current_switch.accepted_asset_ids:
            return False, PaymentValidationError(
                code="INVALID_ASSET_ID",
                message="This asset is not accepted by this switch",
                details={
                    "provided_asset": asset_id,
                    "accepted_assets": current_switch.accepted_asset_ids
                }
            )

        # Check rate quotes if they exist
        if (bitcoinswitch_payment.quoted_rate and 
            bitcoinswitch_payment.quoted_at and
            bitcoinswitch_payment.asset_amount):
            
            # Check if quote has expired
            if RateService.is_rate_expired(bitcoinswitch_payment.quoted_at):
                return False, PaymentValidationError(
                    code="QUOTE_EXPIRED",
                    message="Price quote has expired. Please scan the QR code again for current pricing.",
                    details={
                        "quoted_at": bitcoinswitch_payment.quoted_at.isoformat(),
                        "validity_minutes": config.rate_validity_minutes
                    }
                )
            
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
                    return False, PaymentValidationError(
                        code="RATE_CHANGED",
                        message="Exchange rate has changed significantly. Please scan the QR code again for current pricing.",
                        details={
                            "quoted_rate": bitcoinswitch_payment.quoted_rate,
                            "current_rate": current_rate,
                            "tolerance": config.rate_tolerance
                        }
                    )
            else:
                return False, PaymentValidationError(
                    code="RATE_UNAVAILABLE",
                    message="Unable to verify current exchange rate. Please try again."
                )
        
        return True, None
        
    except Exception as e:
        logger.error(f"Unexpected error in validate_taproot_payment: {e}")
        return False, PaymentValidationError(
            code="VALIDATION_ERROR",
            message="An unexpected error occurred during payment validation",
            details={"error": str(e)}
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
):
    try:
        switch = await get_bitcoinswitch(bitcoinswitch_id)
        if not switch:
            raise PaymentValidationError(
                code="SWITCH_NOT_FOUND",
                message=f"bitcoinswitch {bitcoinswitch_id} not found on this server"
            )

        try:
            price_msat = int(
                (
                    await fiat_amount_as_satoshis(float(amount), switch.currency)
                    if switch.currency != "sat"
                    else float(amount)
                )
                * 1000
            )
        except ValueError as e:
            raise PaymentValidationError(
                code="INVALID_AMOUNT",
                message="Invalid amount provided",
                details={"error": str(e)}
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
            raise PaymentValidationError(
                code="INVALID_PARAMS",
                message="Invalid switch parameters provided"
            )

        bitcoinswitch_payment = await create_bitcoinswitch_payment(
            bitcoinswitch_id=switch.id,
            payload=duration,
            amount_msat=price_msat,
            pin=int(pin),
            payment_hash="not yet set",
        )
        if not bitcoinswitch_payment:
            raise PaymentValidationError(
                code="PAYMENT_CREATION_FAILED",
                message="Could not create payment record"
            )

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
        
        # Handle asset acceptance logic
        if is_asset_enabled_switch(current_switch):
            taproot_available, taproot_error = await TaprootIntegration.is_taproot_available()
            if taproot_available:
                resp["acceptsAssets"] = True
                resp["acceptedAssetIds"] = current_switch.accepted_asset_ids
                resp["assetMetadata"] = {
                    "supportsRfq": True,
                    "message": "This switch accepts Taproot Assets via RFQ - pay with either sats or assets",
                    "rfqEnabled": True
                }
                
                if not current_switch.accepted_asset_ids:
                    raise PaymentValidationError(
                        code="ASSET_CONFIG_ERROR",
                        message="Switch is configured to accept assets but no asset IDs are specified"
                    )
                
                asset_id = current_switch.accepted_asset_ids[0]  # Use first accepted asset
                asset_amount = int(current_switch.amount)  # The switch's configured asset amount
                
                # Get wallet info for invoice creation
                wallet = await get_wallet(switch.wallet)
                if not wallet:
                    raise PaymentValidationError(
                        code="WALLET_NOT_FOUND",
                        message="Switch wallet not found"
                    )
                
                try:
                    # Create RFQ invoice
                    rfq_result, rfq_error = await TaprootIntegration.create_rfq_invoice(
                        asset_id=asset_id,
                        amount=asset_amount,
                        description=f"{switch.title} - LNURL rate check",
                        wallet_id=switch.wallet,
                        user_id=wallet.user,
                        extra={},
                        expiry=300  # 5 minutes
                    )
                    
                    if rfq_error:
                        logger.error(f"RFQ creation failed: {rfq_error}")
                        raise PaymentValidationError(
                            code="RFQ_FAILED",
                            message="Failed to create RFQ invoice",
                            details={"error": str(rfq_error)}
                        )
                        
                    # Decode invoice to get sat amount
                    from lnbits.bolt11 import decode as bolt11_decode
                    decoded = bolt11_decode(rfq_result["payment_request"])
                    
                    if decoded.amount_msat:
                        # Update LNURL response with RFQ amounts
                        resp["minSendable"] = decoded.amount_msat
                        resp["maxSendable"] = decoded.amount_msat
                        
                        # Store RFQ details
                        bitcoinswitch_payment.rfq_invoice_hash = rfq_result["payment_hash"]
                        bitcoinswitch_payment.rfq_asset_amount = asset_amount
                        bitcoinswitch_payment.rfq_sat_amount = decoded.amount_msat / 1000
                        await update_bitcoinswitch_payment(bitcoinswitch_payment)
                    else:
                        logger.warning("RFQ invoice has no amount, falling back to price_msat")
                        
                except Exception as e:
                    logger.error(f"Failed to handle RFQ process: {e}")
                    # Fall back to original price_msat if RFQ fails
            else:
                logger.warning(f"Taproot not available: {taproot_error}")
        
        if comment:
            resp["commentAllowed"] = 1500
        if variable is True:
            resp["maxSendable"] = price_msat * 360
            
        return resp
        
    except PaymentValidationError as e:
        return e.to_lnurl_response()
    except Exception as e:
        logger.error(f"Unexpected error in lnurl_params: {e}")
        return {
            "status": "ERROR",
            "reason": "An unexpected error occurred",
            "code": "INTERNAL_ERROR",
            "details": {"error": str(e)}
        }


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
    try:
        bitcoinswitch_payment = await get_bitcoinswitch_payment(payment_id)
        if not bitcoinswitch_payment:
            raise PaymentValidationError(
                code="PAYMENT_NOT_FOUND",
                message="Payment record not found"
            )
            
        switch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
        if not switch:
            await delete_bitcoinswitch_payment(payment_id)
            raise PaymentValidationError(
                code="SWITCH_NOT_FOUND", 
                message="Switch not found"
            )

        if not amount:
            raise PaymentValidationError(
                code="MISSING_AMOUNT",
                message="Payment amount is required"
            )
        
        # Get the switch configuration
        current_switch = None
        for s in switch.switches:
            if s.pin == bitcoinswitch_payment.pin:
                current_switch = s
                break
        
        if not current_switch:
            raise PaymentValidationError(
                code="INVALID_PIN",
                message="Invalid switch pin"
            )

        # Get wallet info
        wallet = await get_wallet(switch.wallet)
        if not wallet:
            raise PaymentValidationError(
                code="WALLET_NOT_FOUND",
                message="Switch wallet not found"
            )

        # Handle asset payment logic
        if asset_id or is_asset_enabled_switch(current_switch):
            # Use provided asset_id or default to first accepted
            if not asset_id and current_switch.accepted_asset_ids:
                asset_id = current_switch.accepted_asset_ids[0]
            
            # Validate taproot payment
            is_valid, validation_error = await validate_taproot_payment(
                current_switch=current_switch,
                asset_id=asset_id,
                switch_wallet=switch.wallet,
                user_id=wallet.user,
                bitcoinswitch_payment=bitcoinswitch_payment
            )

            if not is_valid:
                if validation_error:
                    return validation_error.to_lnurl_response()
                    
                if is_asset_enabled_switch(current_switch):
                    raise PaymentValidationError(
                        code="ASSET_ONLY",
                        message="This switch only accepts Taproot Asset payments"
                    )

            else:
                # Handle valid taproot payment
                requested_sats = amount / 1000
                
                # Calculate asset amount
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
                
                if taproot_error:
                    raise PaymentValidationError(
                        code="TAPROOT_INVOICE_FAILED",
                        message="Failed to create taproot asset invoice",
                        details={"error": str(taproot_error)}
                    )

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

    except PaymentValidationError as e:
        return e.to_lnurl_response()
    except Exception as e:
        logger.error(f"Unexpected error in lnurl_callback: {e}")
        return {
            "status": "ERROR",
            "reason": "An unexpected error occurred",
            "code": "INTERNAL_ERROR",
            "details": {"error": str(e)}
        }</file_text>