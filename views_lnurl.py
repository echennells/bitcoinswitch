import json

from fastapi import APIRouter, Query, Request
from lnbits.core.crud import get_wallet
from lnbits.core.services import create_invoice, websocket_manager
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis
from lnurl import (
    CallbackUrl,
    InvalidLnurl,
    LightningInvoice,
    LnurlErrorResponse,
    LnurlPayActionResponse,
    LnurlPayMetadata,
    LnurlPayResponse,
    Max144Str,
    MessageAction,
    MilliSatoshi,
)
from loguru import logger
from pydantic import parse_obj_as

from .crud import create_switch_payment, get_bitcoinswitch
from .services.config import config
from .services.rate_service import RateService
from .services.taproot_integration import (
    TAPROOT_AVAILABLE,
    create_taproot_invoice,
    get_asset_name,
)

if not TAPROOT_AVAILABLE:
    logger.info("Taproot services not available - running in Lightning-only mode")


bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


@bitcoinswitch_lnurl_router.get("/{bitcoinswitch_id}")
async def lnurl_params(request: Request, bitcoinswitch_id: str, pin: str):
    switch = await get_bitcoinswitch(bitcoinswitch_id)
    if not switch:
        return LnurlErrorResponse(
            reason=f"bitcoinswitch {bitcoinswitch_id} not found on this server"
        )
    if switch.disabled:
        return LnurlErrorResponse(
            reason=f"bitcoinswitch {bitcoinswitch_id} is disabled"
        )

    _switch = next((_s for _s in switch.switches if _s.pin == int(pin)), None)
    if not _switch:
        return LnurlErrorResponse(reason=f"Switch with pin {pin} not found.")

    # Calculate price in millisats
    base_amount_sats = (
        await fiat_amount_as_satoshis(float(_switch.amount), switch.currency)
        if switch.currency != "sat"
        else float(_switch.amount)
    )

    # TIMING CORRELATION: Generate request ID to track params → callback flow
    import time
    import secrets
    request_id = secrets.token_hex(4)
    request_timestamp = time.time()

    logger.info(f"[LNURL-PARAMS-{request_id}] START at {request_timestamp:.3f}")
    logger.info(f"[LNURL-PARAMS-{request_id}] switch_id={switch_id}, pin={pin}")

    # DEBUG-0: Track object identities and initial state
    logger.info(f"[LNURL-PARAMS-{request_id}] DEBUG-0: switch object id={id(switch)}, _switch id={id(_switch)}")
    logger.info(f"[LNURL-PARAMS-{request_id}] DEBUG-0: _switch.amount={_switch.amount}, switch.currency={switch.currency}")
    logger.info(f"[LNURL-PARAMS-{request_id}] DEBUG-0: Initial base_amount_sats={base_amount_sats}, type={type(base_amount_sats)}")

    # Convert asset amount to sats using RFQ rate if switch accepts assets
    if (
        TAPROOT_AVAILABLE
        and hasattr(_switch, "accepts_assets")
        and _switch.accepts_assets
        and _switch.accepted_asset_ids
    ):
        try:
            # The _switch.amount represents asset units, need to convert to sats
            # Use the first accepted asset ID for rate lookup
            asset_id = _switch.accepted_asset_ids[0]

            # Get the current RFQ rate for this asset
            from lnbits.core.crud import get_wallet

            from .services.rate_service import RateService

            # Get wallet for rate lookup
            wallet = await get_wallet(switch.wallet)
            if wallet:
                # DEBUG-TIMING: Track RFQ call duration
                import time
                rfq_start = time.time()

                current_rate = await RateService.get_current_rate(
                    asset_id=asset_id,
                    wallet_id=switch.wallet,
                    user_id=wallet.user,
                    asset_amount=int(_switch.amount),
                )

                rfq_duration = time.time() - rfq_start
                logger.info(f"DEBUG-TIMING: RFQ took {rfq_duration:.3f}s")

                if current_rate and current_rate > 0:
                    # Convert asset amount to sats using RFQ rate
                    asset_amount_display_units = float(_switch.amount)
                    sats_required = asset_amount_display_units * current_rate
                    logger.info(
                        f"Asset switch pricing: {asset_amount_display_units} {asset_id[:8]}... = {sats_required} sats (rate: {current_rate} sats/display_unit)"
                    )
                    base_amount_sats = sats_required

                    # DEBUG-RFQ: Snapshot the value immediately after assignment
                    base_amount_snapshot = base_amount_sats
                    logger.info(f"DEBUG-RFQ: Snapshot taken - base_amount_snapshot={base_amount_snapshot}")
                else:
                    logger.warning(
                        f"No valid RFQ rate for asset {asset_id}, using configured amount as sats"
                    )
            else:
                logger.warning(
                    f"No wallet found for switch {bitcoinswitch_id}, using configured amount as sats"
                )

        except Exception as e:
            logger.error(
                f"Failed to get RFQ rate for asset switch pricing: {e}, using configured amount as sats"
            )

    # DEBUG-1.5: Check if objects were replaced after RFQ
    logger.info(f"DEBUG-1.5: switch object id={id(switch)}, _switch id={id(_switch)} (checking for object replacement)")

    # DEBUG-1: Log state before price_msat calculation
    logger.info(f"DEBUG-1: base_amount_sats={base_amount_sats}, type={type(base_amount_sats)}")
    logger.info(f"DEBUG-1: _switch.amount={_switch.amount}, switch.currency={switch.currency}")

    # DEBUG-COMPARE: Check if snapshot matches (only if we have a snapshot from RFQ path)
    if 'base_amount_snapshot' in locals():
        logger.info(f"DEBUG-COMPARE: base_amount_sats={base_amount_sats}, snapshot={base_amount_snapshot}, match={base_amount_sats == base_amount_snapshot}")

    price_msat = int(base_amount_sats * 1000)

    # DEBUG-2: Log immediately after price_msat calculation
    logger.info(f"DEBUG-2: price_msat={price_msat}")
    # let the max be 100x the min if variable pricing is enabled
    # Variable amounts not supported for taproot assets
    variable_enabled = _switch.variable and not (
        hasattr(_switch, "accepts_assets") and _switch.accepts_assets
    )
    max_sendable = price_msat * 100 if variable_enabled else price_msat

    # Build callback URL with asset support information if applicable
    base_url = request.url_for(
        "bitcoinswitch.lnurl_cb", switch_id=bitcoinswitch_id, pin=pin
    )
    callback_url_str = str(base_url)

    # Add request_id to callback URL for timing correlation
    callback_url_str += f"?req_id={request_id}&ts={int(request_timestamp * 1000)}"

    # Encode Taproot Asset support in callback URL parameters
    if (
        TAPROOT_AVAILABLE
        and hasattr(_switch, "accepts_assets")
        and _switch.accepts_assets
    ):
        if _switch.accepted_asset_ids:
            # Encode asset support in URL parameters (use & since we already have ?req_id)
            asset_ids_param = "|".join(_switch.accepted_asset_ids)
            callback_url_str += f"&supports_assets=true&asset_ids={asset_ids_param}"
            logger.info(
                f"[LNURL-PARAMS-{request_id}] Switch {bitcoinswitch_id} callback URL encoded with taproot assets: {_switch.accepted_asset_ids}"
            )

    try:
        callback_url = parse_obj_as(CallbackUrl, callback_url_str)
    except InvalidLnurl:
        return LnurlErrorResponse(
            reason=f"Invalid LNURL callback URL: {callback_url_str!s}"
        )

    res = LnurlPayResponse(
        callback=callback_url,
        minSendable=MilliSatoshi(price_msat),
        maxSendable=MilliSatoshi(max_sendable),
        metadata=LnurlPayMetadata(json.dumps([["text/plain", switch.title]])),
    )

    # DEBUG-3: Log response creation with timing
    logger.info(f"[LNURL-PARAMS-{request_id}] DEBUG-3: Response created - minSendable={res.minSendable}, maxSendable={res.maxSendable}")
    logger.info(f"[LNURL-PARAMS-{request_id}] DEBUG-3: Raw price_msat={price_msat}, MilliSatoshi conversion={MilliSatoshi(price_msat)}")
    logger.info(f"[LNURL-PARAMS-{request_id}] EXPECT CALLBACK WITH: amount={price_msat} msat")

    if _switch.comment is True:
        res.commentAllowed = 255

    # DEBUG-4: Final check before return
    logger.info(f"DEBUG-4: About to return response - minSendable={res.minSendable}")

    # CRITICAL VALIDATION: Prevent 1000x underpricing bug
    # For asset switches, minSendable should typically be >= 100,000 msat (100 sats)
    # If it's suspiciously low, log critical error
    if (
        TAPROOT_AVAILABLE
        and hasattr(_switch, "accepts_assets")
        and _switch.accepts_assets
        and res.minSendable < 100000
    ):
        logger.error(
            f"CRITICAL: Suspiciously low minSendable={res.minSendable} for asset switch! "
            f"Expected ~1,000,000 msat. This may be the 1000x underpricing bug. "
            f"Switch ID: {bitcoinswitch_id}, Pin: {pin}"
        )
        # Optional: Uncomment to prevent undercharging by raising an exception
        # raise ValueError(f"Preventing potential 1000x underpricing: minSendable={res.minSendable} is too low")

    return res


@bitcoinswitch_lnurl_router.get("/cb/{switch_id}/{pin}", name="bitcoinswitch.lnurl_cb")
async def lnurl_callback(
    switch_id: str,
    pin: int,
    amount: int | None = Query(None),
    comment: str | None = Query(None),
    asset_id: str | None = Query(None),
    req_id: str | None = Query(None),  # Correlation ID from lnurl_params
    ts: int | None = Query(None),  # Timestamp from lnurl_params (milliseconds)
) -> LnurlPayActionResponse | LnurlErrorResponse:
    # TIMING CORRELATION: Track timing between params and callback
    import time
    callback_timestamp = time.time()
    time_since_params = None
    if ts:
        params_timestamp_sec = ts / 1000.0
        time_since_params = callback_timestamp - params_timestamp_sec

    correlation_id = req_id or "UNKNOWN"
    logger.info(f"[LNURL-CALLBACK-{correlation_id}] START at {callback_timestamp:.3f}")
    logger.info(f"[LNURL-CALLBACK-{correlation_id}] switch_id={switch_id}, pin={pin}")
    logger.info(f"[LNURL-CALLBACK-{correlation_id}] RECEIVED: amount={amount} msat, asset_id={asset_id}")
    if time_since_params is not None:
        logger.info(f"[LNURL-CALLBACK-{correlation_id}] TIME SINCE PARAMS: {time_since_params:.3f}s")

    if comment and len(comment) > 255:
        return LnurlErrorResponse(reason="Comment too long, max 255 characters.")
    if not amount:
        return LnurlErrorResponse(reason="No amount specified.")

    switch = await get_bitcoinswitch(switch_id)
    if not switch:
        return LnurlErrorResponse(reason="Switch not found.")
    if switch.disabled:
        return LnurlErrorResponse(reason=f"bitcoinswitch {switch_id} is disabled")
    _switch = next((_s for _s in switch.switches if _s.pin == int(pin)), None)
    if not _switch:
        return LnurlErrorResponse(reason=f"Switch with pin {pin} not found.")

    if not switch.disposable and not websocket_manager.has_connection(switch_id):
        return LnurlErrorResponse(reason="No active bitcoinswitch connections.")

    # CRITICAL: Validate amount against expected min/max to prevent undercharging
    # Recalculate expected amount using same logic as lnurl_params
    expected_base_sats = (
        await fiat_amount_as_satoshis(float(_switch.amount), switch.currency)
        if switch.currency != "sat"
        else float(_switch.amount)
    )

    # If switch accepts assets, convert using RFQ rate
    if (
        TAPROOT_AVAILABLE
        and hasattr(_switch, "accepts_assets")
        and _switch.accepts_assets
        and _switch.accepted_asset_ids
    ):
        try:
            from .services.rate_service import RateService
            wallet = await get_wallet(switch.wallet)
            if wallet:
                asset_id_for_rate = _switch.accepted_asset_ids[0]
                current_rate = await RateService.get_current_rate(
                    asset_id=asset_id_for_rate,
                    wallet_id=switch.wallet,
                    user_id=wallet.user,
                    asset_amount=int(_switch.amount),
                )
                if current_rate and current_rate > 0:
                    expected_base_sats = float(_switch.amount) * current_rate
                    logger.info(f"[LNURL-CALLBACK-{correlation_id}] VALIDATION: Expected {expected_base_sats} sats based on RFQ rate {current_rate}")
        except Exception as e:
            logger.warning(f"[LNURL-CALLBACK-{correlation_id}] Failed to get RFQ rate for callback validation: {e}")

    expected_msat = int(expected_base_sats * 1000)
    logger.info(f"[LNURL-CALLBACK-{correlation_id}] VALIDATION: Received amount={amount} msat, expected={expected_msat} msat, asset_id={asset_id}")

    # TIMING DIAGNOSIS: Log the difference to help identify timing issues
    if amount != expected_msat:
        msat_diff = amount - expected_msat
        msat_ratio = amount / expected_msat if expected_msat > 0 else 0
        logger.warning(
            f"[LNURL-CALLBACK-{correlation_id}] AMOUNT MISMATCH! "
            f"Received={amount}, Expected={expected_msat}, "
            f"Diff={msat_diff}, Ratio={msat_ratio:.6f}"
        )

    # Validate amount (allow 1% tolerance for rounding)
    # SKIP validation for Taproot asset payments as amount semantics differ
    # (Assets have their own units - 1 asset could be worth 1000 sats or 0.001 sats)
    if asset_id:
        logger.info(f"[LNURL-CALLBACK-{correlation_id}] VALIDATION: Skipping for Taproot asset payment (asset_id={asset_id})")
    else:
        if amount < expected_msat * 0.99:
            logger.error(
                f"[LNURL-CALLBACK-{correlation_id}] CRITICAL: Amount {amount} is below expected {expected_msat}! "
                f"This is the 1000x undercharging bug. REJECTING PAYMENT."
            )
            return LnurlErrorResponse(
                reason=f"Amount {amount} msat is below minimum {expected_msat} msat"
            )

    # Check for Taproot Asset payment
    logger.info(
        f"TAPROOT CHECK: TAPROOT_AVAILABLE={TAPROOT_AVAILABLE}, asset_id={asset_id}"
    )
    if hasattr(_switch, "accepts_assets"):
        logger.info(f"Switch accepts_assets: {_switch.accepts_assets}")
    else:
        logger.info("Switch has no accepts_assets attribute")

    if (
        TAPROOT_AVAILABLE
        and asset_id
        and hasattr(_switch, "accepts_assets")
        and _switch.accepts_assets
    ):
        logger.info(f"Switch accepted_asset_ids: {_switch.accepted_asset_ids}")
        try:
            if asset_id in _switch.accepted_asset_ids:
                logger.info(f"Processing taproot asset payment for {asset_id}")
                return await handle_taproot_payment(
                    switch, _switch, switch_id, pin, amount, comment, asset_id
                )
            else:
                logger.warning(
                    f"Asset {asset_id} not in accepted list: {_switch.accepted_asset_ids}"
                )
        except Exception as e:
            logger.error(f"Taproot payment failed, falling back to Lightning: {e}")
    else:
        logger.info("Taproot conditions not met, using Lightning payment")

    # Standard Lightning payment (original logic)
    memo = f"{switch.title} (pin: {pin})"
    if comment:
        memo += f" - {comment}"

    metadata = LnurlPayMetadata(json.dumps([["text/plain", switch.title]]))

    payment = await create_invoice(
        wallet_id=switch.wallet,
        amount=int(amount / 1000),
        unhashed_description=metadata.encode(),
        memo=memo,
        extra={
            "tag": "Switch",
            "pin": pin,
            "comment": comment,
        },
    )

    await create_switch_payment(
        payment_hash=payment.payment_hash,
        switch_id=switch.id,
        pin=pin,
        amount_msat=amount,
    )

    message = f"{int(amount / 1000)}sats sent"
    if switch.password and switch.password != comment:
        message = f"{message}, but password was incorrect! :("

    return LnurlPayActionResponse(
        pr=parse_obj_as(LightningInvoice, payment.bolt11),
        successAction=MessageAction(message=parse_obj_as(Max144Str, message)),
        disposable=switch.disposable,
    )


async def handle_taproot_payment(
    switch, _switch, switch_id, pin, amount, comment, asset_id
):
    """Handle Taproot Asset payment - only called if taproot services available."""
    if not TAPROOT_AVAILABLE:
        raise Exception("Taproot services not available")

    # Get wallet for user ID
    wallet = await get_wallet(switch.wallet)
    if not wallet:
        return LnurlErrorResponse(reason="Wallet not found")

    # For direct asset payments, use switch config amount directly
    # For Lightning payments, convert sats to asset amount using RFQ

    # Check if this is a direct asset payment or Lightning payment needing conversion
    # Direct asset payments should use the switch's configured asset amount
    # Lightning payments need sats→asset conversion via RFQ

    # For now, use switch config directly for direct asset payments
    # The switch is configured for the correct asset amount
    asset_amount = int(_switch.amount)

    # TODO: detect Lightning vs direct asset payments, use RFQ for Lightning only

    logger.info("TAPROOT PAYMENT:")
    logger.info(f"  - Amount parameter: {amount} msat")
    logger.info(f"  - Using asset_amount: {asset_amount}")
    logger.info(f"  - Asset ID: {asset_id}")

    # Get peer_pubkey from asset channel info (like the direct UI does)
    peer_pubkey = None
    try:
        from lnbits.core.models import WalletTypeInfo
        from lnbits.core.models.wallets import KeyType
        from lnbits.extensions.taproot_assets.services.asset_service import (  # type: ignore
            AssetService,
        )

        wallet_info = WalletTypeInfo(key_type=KeyType.admin, wallet=wallet)
        assets = await AssetService.list_assets(wallet_info)

        # Find the asset and get its peer_pubkey
        for asset in assets:
            if (
                asset.get("asset_id") == asset_id
                and asset.get("channel_info")
                and asset["channel_info"].get("peer_pubkey")
            ):
                peer_pubkey = asset["channel_info"]["peer_pubkey"]
                logger.info(f"  - Found peer_pubkey: {peer_pubkey[:16]}...")
                break

        if not peer_pubkey:
            logger.warning(f"  - No peer_pubkey found for asset {asset_id}")

    except Exception as e:
        logger.error(f"Failed to get peer_pubkey: {e}")

    # Create Taproot Asset invoice using the updated API
    taproot_result = await create_taproot_invoice(
        asset_id=asset_id,
        amount=asset_amount,
        description=f"{switch.title} (pin: {pin})",
        wallet_id=switch.wallet,
        user_id=wallet.user,
        expiry=config.taproot_payment_expiry,
        peer_pubkey=peer_pubkey,
        extra={
            "tag": "Switch",
            "pin": pin,
            "comment": comment,
        },
    )

    if not taproot_result:
        raise Exception("Failed to create taproot invoice")

    # Create payment record with taproot fields
    payment_record = await create_switch_payment(
        payment_hash=taproot_result["payment_hash"],
        switch_id=switch.id,
        pin=pin,
        amount_msat=amount,
    )

    # Update with taproot-specific fields if available
    if hasattr(payment_record, "is_taproot"):
        payment_record.is_taproot = True
        payment_record.asset_id = asset_id
        payment_record.asset_amount = asset_amount
        from .crud import update_switch_payment

        await update_switch_payment(payment_record)

    # Get asset name for user-friendly message
    from lnbits.core.models import WalletTypeInfo
    from lnbits.core.models.wallets import KeyType

    wallet_info = WalletTypeInfo(key_type=KeyType.admin, wallet=wallet)
    asset_name = await get_asset_name(asset_id, wallet_info)

    # Clean success message without redundant "units requested" text
    if switch.password and switch.password != comment:
        message = "Password was incorrect! :("
    else:
        message = f"{asset_amount} {asset_name} sent"

    return LnurlPayActionResponse(
        pr=parse_obj_as(LightningInvoice, taproot_result["payment_request"]),
        successAction=MessageAction(message=parse_obj_as(Max144Str, message)),
        disposable=switch.disposable,
    )


async def calculate_asset_amount_with_rfq(
    asset_id: str,
    requested_sats: float,
    switch_amount: int,
    wallet_id: str,
    user_id: str,
) -> int:
    """Calculate asset amount using RFQ rate or fallback to switch configuration."""
    try:
        # Try to get current rate via RFQ
        current_rate = await RateService.get_current_rate(
            asset_id=asset_id,
            wallet_id=wallet_id,
            user_id=user_id,
            asset_amount=switch_amount,
        )

        if current_rate and current_rate > 0:
            # Calculate asset amount based on real market rate
            # current_rate is sats per display unit, we need to convert to base units
            display_units = int(requested_sats / current_rate)
            logger.info(
                f"RFQ rate calculation: {requested_sats} sats / {current_rate} sats/display_unit = {display_units} display_units"
            )

            # Get asset decimal places from channel data (more reliable)
            try:
                from lnbits.extensions.taproot_assets.tapd.taproot_factory import (  # type: ignore
                    TaprootAssetsFactory,
                )

                # Get a taproot wallet instance to access channel data
                taproot_wallet = await TaprootAssetsFactory.create_wallet(
                    user_id=user_id, wallet_id=wallet_id
                )

                # Get channel assets which has reliable decimal info
                channel_assets = await taproot_wallet.node.list_channel_assets(
                    force_refresh=True
                )

                # Find the specific asset in channel data
                asset_decimals = 0
                for asset_data in channel_assets:
                    if asset_data.get("asset_id") == asset_id:
                        asset_decimals = asset_data.get("decimal_display", 0)
                        logger.info(
                            f"Found asset {asset_id[:8]}... with {asset_decimals} decimals from channel data"
                        )
                        break

                # Return display units - taproot assets invoice expects them
                logger.info(
                    f"Using {display_units} display_units for invoice (asset has {asset_decimals} decimals)"
                )
                return max(1, display_units)

            except Exception as e:
                logger.warning(f"Could not get asset decimals from channel data: {e}")
                # Return display units - taproot assets invoice expects them
                logger.info(
                    f"Using {display_units} display_units for invoice (fallback)"
                )
                return max(1, display_units)
        else:
            logger.warning(
                f"No valid RFQ rate available for asset {asset_id[:8]}..., using switch config: {switch_amount} assets"
            )
            return switch_amount

    except Exception as e:
        logger.error(
            f"RFQ rate lookup failed for asset {asset_id[:8]}...: {e}, using switch config: {switch_amount} assets"
        )
        return switch_amount
