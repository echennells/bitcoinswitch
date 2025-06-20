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
        
        # MARKET MAKER IMPLEMENTATION: Fetch current RFQ rate
        # This implements the solution where bitcoinswitch acts as a market maker
        # by quoting exact sat amounts based on current RFQ rates
        
        # Get the asset amount from switch config
        asset_amount = int(current_switch.amount)
        asset_id = current_switch.accepted_asset_ids[0]  # Use first accepted asset
        
        # Get wallet info for RFQ access
        wallet = await get_wallet(switch.wallet)
        if wallet:
            # Fetch current RFQ rate
            sat_amount = await RateService.calculate_sat_amount(
                asset_id=asset_id,
                asset_amount=asset_amount,
                wallet_id=switch.wallet,
                user_id=wallet.user
            )
            
            if sat_amount:
                # Store the quoted rate in the payment record
                bitcoinswitch_payment.quoted_rate = sat_amount / asset_amount
                bitcoinswitch_payment.quoted_at = datetime.now(timezone.utc)
                bitcoinswitch_payment.asset_amount = asset_amount
                await update_bitcoinswitch_payment(bitcoinswitch_payment)
                
                # Set exact amount based on current RFQ rate
                resp["minSendable"] = sat_amount * 1000  # Convert to millisats
                resp["maxSendable"] = sat_amount * 1000  # Convert to millisats
                
                logger.info(f"Asset switch: {asset_amount} units = {sat_amount} sats at current RFQ rate")
            else:
                # Fallback if rate fetch fails - use wide range
                logger.warning(f"Could not fetch RFQ rate for asset {asset_id}, using wide range")
                resp["minSendable"] = 1000  # 1 sat
                resp["maxSendable"] = 10000000  # 10k sats
        else:
            # No wallet info - use wide range
            resp["minSendable"] = 1000
            resp["maxSendable"] = 10000000
    
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
    
    # IMPORTANT FIX: Check if this switch is configured for taproot assets
    # If the switch accepts assets, ALWAYS create an asset invoice, regardless of callback parameters
    # This prevents dual-mode invoices where users can pay with either sats or assets
    if (current_switch and
        current_switch.accepts_assets and 
        current_switch.accepted_asset_ids and
        len(current_switch.accepted_asset_ids) > 0 and
        await TaprootIntegration.is_taproot_available()):
        
        # If no asset_id provided in callback, use the first accepted asset
        # This ensures asset-configured switches ONLY create asset invoices
        if not asset_id or asset_id not in current_switch.accepted_asset_ids:
            asset_id = current_switch.accepted_asset_ids[0]
            logger.info(f"No valid asset_id in callback, using configured asset: {asset_id}")
    
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
        
        # MARKET MAKER: Validate rate if this was a quoted payment
        if (bitcoinswitch_payment.quoted_rate and 
            bitcoinswitch_payment.quoted_at and
            bitcoinswitch_payment.asset_amount):
            
            # Check if quote has expired
            if RateService.is_rate_expired(bitcoinswitch_payment.quoted_at):
                logger.warning(f"Rate quote expired for payment {payment_id}")
                return {
                    "status": "ERROR",
                    "reason": "Price quote has expired. Please scan the QR code again for current pricing."
                }
            
            # Get current rate and check tolerance
            # Use the same asset amount that was quoted
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
                    logger.warning(
                        f"Rate changed beyond tolerance. Quoted: {bitcoinswitch_payment.quoted_rate}, "
                        f"Current: {current_rate}"
                    )
                    return {
                        "status": "ERROR",
                        "reason": "Exchange rate has changed significantly. Please scan the QR code again for current pricing."
                    }
            else:
                logger.warning(f"Could not verify current rate for asset {asset_id}")
                # Continue anyway - let RFQ handle it
        
        # IMPORTANT FIX: Asset amount handling for proper RFQ invoices
        # The 'amount' parameter from LNURL is in millisats and represents the switch's configured amount
        # For asset invoices, we need to pass the ASSET UNITS, not the sat value
        # 
        # OLD BEHAVIOR (can be rolled back by uncommenting):
        # - Used amount // 1000 which treated the switch amount as both asset units AND sats
        # - This created "dual-purpose" invoices that accepted either X assets OR X sats
        # 
        # NEW BEHAVIOR:
        # - Uses the switch's configured amount as ASSET UNITS
        # - Creates proper asset-only invoices (with value=0 sats) that require RFQ conversion
        # - Example: 10 bepsi switch creates invoice for 10 bepsi units (not 10 sats)
        
        # MARKET MAKER: Use the asset amount that was quoted
        # This ensures we create an invoice for the exact asset amount
        # that corresponds to the sat amount we quoted
        
        if bitcoinswitch_payment.asset_amount:
            # Use the quoted asset amount
            asset_amount = bitcoinswitch_payment.asset_amount
            logger.info(f"Using quoted asset amount: {asset_amount}")
        else:
            # Fallback to switch configuration
            for s in switch.switches:
                if s.pin == bitcoinswitch_payment.pin:
                    asset_amount = int(s.amount)
                    break
            else:
                # Fallback if switch not found (shouldn't happen)
                asset_amount = 10
                logger.warning(f"Could not find switch config for pin {bitcoinswitch_payment.pin}, using default")
        
        logger.info(f"Creating RFQ invoice for asset {asset_id}, amount={asset_amount} asset units (not sats)")
        
        # Create Taproot Asset invoice using RFQ process
        # This creates an invoice for X units of the asset (with Lightning value=0)
        # The invoice can be paid with sats through RFQ conversion at market rate
        taproot_result = await TaprootIntegration.create_rfq_invoice(
            asset_id=asset_id,
            amount=asset_amount,  # Asset units, not sats! (e.g., 10 bepsi, not 10 sats)
            # OLD: amount=amount // 1000,  # This was wrong - treated as sats
            description=f"{switch.title} ({bitcoinswitch_payment.payload} ms)",
            wallet_id=switch.wallet,
            user_id=wallet.user,
            extra={
                "pin": str(bitcoinswitch_payment.pin),
                "amount": str(int(amount)),  # Keep original amount for payment tracking
                "comment": comment,
                "variable": variable,
                "id": payment_id,
                "switch_id": switch.id,
                "payload": bitcoinswitch_payment.payload,
                "asset_amount": str(asset_amount)  # Store asset amount for reference
            },
            expiry=3600  # 1 hour expiry
            # Don't pass peer_pubkey - let RFQ find any available peer
        )
        
        if taproot_result:
            logger.info(f"RFQ invoice created successfully: {taproot_result['payment_hash']}")
            # Update payment record
            bitcoinswitch_payment.payment_hash = taproot_result["payment_hash"]
            bitcoinswitch_payment.is_taproot = True
            bitcoinswitch_payment.asset_id = asset_id
            await update_bitcoinswitch_payment(bitcoinswitch_payment)
            
            # Update message to show asset amount instead of sat amount
            # OLD: message = f"{int(amount / 1000)}sats worth of {asset_id} requested"
            message = f"{asset_amount} units of {asset_id} requested"
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
    
    # IMPORTANT: Check if this switch is configured for assets
    # If it is, we should NEVER create a regular Lightning invoice
    # This prevents the dual-mode behavior where switches accept both sats and assets
    if (current_switch and 
        current_switch.accepts_assets and 
        current_switch.accepted_asset_ids and 
        len(current_switch.accepted_asset_ids) > 0):
        # This is an asset-configured switch but we're here because:
        # 1. Taproot integration is not available, OR
        # 2. No valid asset_id was provided
        # In either case, we should fail rather than create a regular invoice
        return {
            "status": "ERROR", 
            "reason": "This switch only accepts Taproot Asset payments. Please use a wallet that supports Taproot Assets."
        }
    
    # Fall back to regular Lightning invoice ONLY for non-asset switches
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
