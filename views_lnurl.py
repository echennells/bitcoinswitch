import json

from fastapi import APIRouter, Query, Request
from loguru import logger

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
from pydantic import parse_obj_as

from .crud import create_switch_payment, get_bitcoinswitch

bitcoinswitch_lnurl_router = APIRouter(prefix="/api/v1/lnurl")


async def calculate_switch_price_range(switch, _switch) -> tuple[int, int]:
    """
    Calculate the min and max sendable amounts for a switch in millisatoshis.

    Args:
        switch: The main switch object containing currency info
        _switch: The individual switch containing amount and variable pricing config

    Returns:
        tuple[int, int]: (min_sendable_msat, max_sendable_msat)
    """
    price_msat = int(
        (
            await fiat_amount_as_satoshis(float(_switch.amount), switch.currency)
            if switch.currency != "sat"
            else float(_switch.amount)
        )
        * 1000
    )
    # let the max be 100x the min if variable pricing is enabled
    max_sendable = price_msat * 100 if _switch.variable else price_msat
    return price_msat, max_sendable


@bitcoinswitch_lnurl_router.get("/{bitcoinswitch_id}")
async def lnurl_params(
    request: Request, bitcoinswitch_id: str, pin: str
) -> LnurlPayResponse | LnurlErrorResponse:
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

    price_msat, max_sendable = await calculate_switch_price_range(switch, _switch)

    logger.info(
        f"[BITCOINSWITCH-PARAMS] switch_id={bitcoinswitch_id}, pin={pin}, "
        f"configured_amount={_switch.amount} {switch.currency}, "
        f"minSendable={price_msat} msat ({price_msat/1000} sats), "
        f"maxSendable={max_sendable} msat ({max_sendable/1000} sats), "
        f"variable={_switch.variable}"
    )

    url = request.url_for("bitcoinswitch.lnurl_cb", switch_id=bitcoinswitch_id, pin=pin)
    try:
        callback_url = parse_obj_as(CallbackUrl, str(url))
    except InvalidLnurl:
        return LnurlErrorResponse(reason=f"Invalid LNURL callback URL: {url!s}")
    res = LnurlPayResponse(
        callback=callback_url,
        minSendable=MilliSatoshi(price_msat),
        maxSendable=MilliSatoshi(max_sendable),
        metadata=LnurlPayMetadata(json.dumps([["text/plain", switch.title]])),
    )
    if _switch.comment is True:
        res.commentAllowed = 255
    return res


@bitcoinswitch_lnurl_router.get("/cb/{switch_id}/{pin}", name="bitcoinswitch.lnurl_cb")
async def lnurl_callback(
    switch_id: str,
    pin: int,
    amount: int | None = Query(None),
    comment: str | None = Query(None),
) -> LnurlPayActionResponse | LnurlErrorResponse:
    logger.info(
        f"[BITCOINSWITCH-CALLBACK] Received: switch_id={switch_id}, pin={pin}, "
        f"amount={amount} msat ({amount/1000 if amount else 0} sats)"
    )

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

    if not websocket_manager.has_connection(switch_id):
        return LnurlErrorResponse(reason="No active bitcoinswitch connections.")

    # Calculate what the expected min/max should be (for validation)
    expected_price_msat, expected_max_sendable = await calculate_switch_price_range(
        switch, _switch
    )

    logger.info(
        f"[BITCOINSWITCH-CALLBACK] Expected: min={expected_price_msat} msat ({expected_price_msat/1000} sats), "
        f"max={expected_max_sendable} msat ({expected_max_sendable/1000} sats) | "
        f"Received: {amount} msat ({amount/1000} sats)"
    )

    # SECURITY FIX: Validate amount is within advertised constraints
    if amount < expected_price_msat:
        logger.warning(
            f"[BITCOINSWITCH-SECURITY] ⚠️ REJECTED underpayment attempt! "
            f"switch_id={switch_id}, pin={pin}, "
            f"required_min={expected_price_msat} msat ({expected_price_msat/1000} sats), "
            f"received={amount} msat ({amount/1000} sats), "
            f"underpaid_by={expected_price_msat - amount} msat ({(expected_price_msat - amount)/1000} sats)"
        )
        return LnurlErrorResponse(
            reason=f"Amount too low. Minimum: {int(expected_price_msat / 1000)} sats"
        )

    if amount > expected_max_sendable:
        logger.warning(
            f"[BITCOINSWITCH-SECURITY] ⚠️ REJECTED overpayment attempt! "
            f"switch_id={switch_id}, pin={pin}, "
            f"allowed_max={expected_max_sendable} msat ({expected_max_sendable/1000} sats), "
            f"received={amount} msat ({amount/1000} sats), "
            f"overpaid_by={amount - expected_max_sendable} msat ({(amount - expected_max_sendable)/1000} sats)"
        )
        return LnurlErrorResponse(
            reason=f"Amount too high. Maximum: {int(expected_max_sendable / 1000)} sats"
        )

    logger.info(
        f"[BITCOINSWITCH-CALLBACK] ✓ Amount validated successfully: {amount} msat ({amount/1000} sats)"
    )

    memo = f"{switch.title} (pin: {pin})"
    if comment:
        memo += f" - {comment}"

    metadata = LnurlPayMetadata(json.dumps([["text/plain", switch.title]]))

    logger.info(
        f"[BITCOINSWITCH-CALLBACK] Creating invoice for {int(amount / 1000)} sats"
    )

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
