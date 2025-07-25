import asyncio
from typing import Optional
from lnbits.core.models import Payment
from lnbits.core.services import websocket_updater
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import (
    get_bitcoinswitch,
    get_bitcoinswitch_payment,
    update_bitcoinswitch_payment,
)

# Maximum retries for switch activation
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

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

async def activate_switch(switch_id: str, payload: str, max_retries: int = MAX_RETRIES) -> bool:
    """Attempt to activate switch with retries. Returns True if successful."""
    for attempt in range(max_retries):
        try:
            await websocket_updater(switch_id, payload)
            logger.info(f"Switch {switch_id} activated successfully with payload {payload}")
            return True
        except Exception as e:
            logger.warning(f"Switch activation attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(RETRY_DELAY)
            continue
    return False

async def on_invoice_paid(payment: Payment) -> None:
    is_taproot = payment.extra.get("is_taproot", False)
    payment_id = payment.extra.get("id")
    
    # Validate payment data
    if is_taproot and not payment_id:
        logger.warning("Taproot payment missing 'id' in extra data")
        return
    elif not is_taproot and payment.extra.get("tag") != "Switch":
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

    # Calculate payload
    try:
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

    except Exception as e:
        logger.error(f"Failed to calculate payload for payment {payment_id}: {e}")
        return

    # Attempt switch activation
    success = await activate_switch(bitcoinswitch_payment.bitcoinswitch_id, payload)
    
    if success:
        # Only mark as paid if switch activated successfully
        bitcoinswitch_payment.payment_hash = "paid"
        await update_bitcoinswitch_payment(bitcoinswitch_payment)
        logger.info(f"Payment {payment_id} processed and switch activated successfully")
    else:
        logger.error(f"Failed to activate switch for payment {payment_id} after {MAX_RETRIES} attempts")
        # Payment remains unpaid in database for potential retry/recovery