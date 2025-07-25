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
    logger.info(f"BitcoinSwitch invoice listener registered")

    while True:
        try:
            payment = await invoice_queue.get()
            await on_invoice_paid(payment)
        except Exception as e:
            logger.error(f"Error processing payment: {e}", exc_info=True)


async def on_invoice_paid(payment: Payment) -> None:
    is_taproot = payment.extra.get("is_taproot", False)
    payment_id = payment.extra.get("id")
    
    # Handle both regular and taproot payments
    if is_taproot:
        if "id" not in payment.extra:
            logger.warning(f"Taproot payment missing 'id' in extra data")
            return
    else:
        if payment.extra.get("tag") != "Switch":
            return
    
    # Get payment and switch details
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment_id)
    if not bitcoinswitch_payment:
        logger.error(f"Payment {payment_id} not found")
        return
    if bitcoinswitch_payment.payment_hash == "paid":
        logger.info(f"Payment {payment_id} already processed")
        return

    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error(f"No bitcoinswitch found for payment {payment_id}")
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
            logger.error(f"Cannot calculate variable payload for payment {payment_id} - sats is 0")
            payload = str(amount)
        else:
            payload = str((int(payload) / int(bitcoinswitch_payment.sats)) * amount)
    
    # Construct final payload
    payload = f"{bitcoinswitch_payment.pin}-{payload}"
    if comment := payment.extra.get("comment"):
        payload = f"{payload}-{comment}"

    # Process payment and notify
    try:
        await websocket_updater(
            bitcoinswitch_payment.bitcoinswitch_id,
            payload,
        )
        logger.info(f"Successfully sent switch command for payment {payment_id}")
    except Exception as e:
        logger.error(f"Failed to update websocket for payment {payment_id}: {e}")
        raise