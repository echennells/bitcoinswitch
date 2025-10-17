import asyncio

from lnbits.core.models import Payment
from lnbits.core.services import websocket_manager
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import (
    get_bitcoinswitch,
    get_switch_payment_by_payment_hash,
)


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_bitcoinswitch")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:
    logger.info(f"BitcoinSwitch: Processing payment {payment.payment_hash}")
    logger.info(f"BitcoinSwitch: Payment extra data: {payment.extra}")

    if payment.extra.get("tag") != "Switch":
        logger.info(
            f"BitcoinSwitch: Ignoring payment - tag is {payment.extra.get('tag')} not 'Switch'"
        )
        return

    switch_payment = await get_switch_payment_by_payment_hash(payment.payment_hash)
    if not switch_payment:
        logger.warning(
            f"Switch payment not found for payment hash: {payment.payment_hash}"
        )
        return

    logger.info(f"BitcoinSwitch: Found switch payment: {switch_payment}")
    bitcoinswitch = await get_bitcoinswitch(switch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error("no bitcoinswitch found for payment.")
        return

    _switch = next(
        (s for s in bitcoinswitch.switches if s.pin == switch_payment.pin),
        None,
    )

    if not _switch:
        logger.error(f"Switch with pin {switch_payment.pin} not found.")
        return

    duration = _switch.duration

    # Variable amounts only supported for Lightning payments
    if _switch.variable is True and not (
        hasattr(switch_payment, "is_taproot") and switch_payment.is_taproot
    ):
        duration = round(
            (switch_payment.sats / 1000) / _switch.amount * _switch.duration
        )

    payload = f"{_switch.pin}-{duration}"

    comment = payment.extra.get("comment")
    if comment:
        payload = f"{payload}-{comment}"

    # Wrong password in comment
    if bitcoinswitch.password and bitcoinswitch.password != comment:
        logger.info(f"Wrong password entered for bitcoin switch: {bitcoinswitch.id}")
        return

    logger.info(
        f"BitcoinSwitch: Sending websocket payload '{payload}' to switch {bitcoinswitch.id}"
    )
    await websocket_manager.send(bitcoinswitch.id, payload)
