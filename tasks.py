import asyncio

from lnbits.core.models import Payment
from lnbits.core.services import websocket_updater
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import (
    get_bitcoinswitch,
    get_bitcoinswitch_payment,
    update_bitcoinswitch_payment,
)


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_bitcoinswitch")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:
    # Check if this is a taproot payment
    is_taproot = payment.extra.get("is_taproot", False)
    
    # Log payment details for debugging
    logger.debug(f"BitcoinSwitch received payment: hash={payment.payment_hash}, is_taproot={is_taproot}")
    
    # Handle both regular and taproot payments
    if is_taproot:
        # For taproot payments, check if it's a switch payment by id
        if "id" not in payment.extra:
            logger.debug(f"Taproot payment missing 'id' in extra data")
            return
    else:
        # For regular payments, check the tag
        if payment.extra.get("tag") != "Switch":
            return
    
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment.extra["id"])

    if not bitcoinswitch_payment or bitcoinswitch_payment.payment_hash == "paid":
        return

    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error("no bitcoinswitch found for payment.")
        return

    # Password check
    comment = payment.extra.get("comment")
    if bitcoinswitch.password and bitcoinswitch.password != comment:
        logger.warning(f"Wrong password entered for bitcoin switch: {bitcoinswitch.id}")
        return

    # Process payment
    bitcoinswitch_payment.payment_hash = bitcoinswitch_payment.payload
    bitcoinswitch_payment = await update_bitcoinswitch_payment(bitcoinswitch_payment)
    payload = bitcoinswitch_payment.payload

    variable = payment.extra.get("variable")
    if variable is True:
        # For taproot payments, use asset amount if available
        if is_taproot:
            amount = payment.extra.get("asset_amount", int(payment.extra.get("amount", 0)))
        else:
            amount = int(payment.extra["amount"])
        payload = str(
            (int(payload) / int(bitcoinswitch_payment.sats)) * amount
        )
    
    payload = f"{bitcoinswitch_payment.pin}-{payload}"
    
    if comment:
        payload = f"{payload}-{comment}"

    # Log payment type
    payment_type = "Taproot Asset" if is_taproot else "Lightning"
    asset_info = f" ({payment.extra.get('asset_id')})" if is_taproot else ""
    logger.debug(f"Processing {payment_type}{asset_info} payment for switch {bitcoinswitch.id}")

    return await websocket_updater(
        bitcoinswitch_payment.bitcoinswitch_id,
        payload,
    )
