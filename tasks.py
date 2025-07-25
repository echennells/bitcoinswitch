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
    logger.info("BitcoinSwitch invoice listener registered")

    while True:
        try:
            payment = await invoice_queue.get()
            await on_invoice_paid(payment)
        except Exception as e:
            logger.error(f"Error processing payment: {e}", exc_info=True)


async def on_invoice_paid(payment: Payment) -> None:
    is_taproot = payment.extra.get("is_taproot", False)
    
    # Handle both regular and taproot payments
    if is_taproot:
        if "id" not in payment.extra:
            logger.warning("Taproot payment missing 'id' in extra data")
            return
    else:
        if payment.extra.get("tag") != "Switch":
            return
    
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment.extra["id"])
    if not bitcoinswitch_payment or bitcoinswitch_payment.payment_hash == "paid":
        return

    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error("No bitcoinswitch found for payment")
        return

    # Process payment
    bitcoinswitch_payment.payment_hash = bitcoinswitch_payment.payload
    bitcoinswitch_payment = await update_bitcoinswitch_payment(bitcoinswitch_payment)
    payload = bitcoinswitch_payment.payload

    # Handle variable payments
    if payment.extra.get("variable") is True:
        if is_taproot:
            amount = payment.extra.get("asset_amount", int(payment.extra.get("amount", 0)))
        else:
            amount = int(payment.extra["amount"])
        
        if int(bitcoinswitch_payment.sats) == 0:
            logger.error("Cannot calculate variable payload - sats is 0")
            payload = str(amount)
        else:
            payload = str((int(payload) / int(bitcoinswitch_payment.sats)) * amount)
    
    # Construct final payload
    payload = f"{bitcoinswitch_payment.pin}-{payload}"
    if comment := payment.extra.get("comment"):
        payload = f"{payload}-{comment}"

    # Process payment and notify
    try:
        return await websocket_updater(
            bitcoinswitch_payment.bitcoinswitch_id,
            payload,
        )
    except Exception as e:
        logger.error(f"Failed to update websocket: {e}")
        raise
