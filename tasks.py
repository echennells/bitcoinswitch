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
    logger.info("[WEBSOCKET DEBUG] BitcoinSwitch invoice listener registered")

    while True:
        try:
            payment = await invoice_queue.get()
            logger.info("[WEBSOCKET DEBUG] Received payment from queue")
            await on_invoice_paid(payment)
        except Exception as e:
            logger.error(f"[WEBSOCKET DEBUG] Error processing payment in wait_for_paid_invoices: {e}", exc_info=True)


async def on_invoice_paid(payment: Payment) -> None:
    logger.info("[WEBSOCKET DEBUG] on_invoice_paid called")
    
    # Check if this is a taproot payment
    is_taproot = payment.extra.get("is_taproot", False)
    
    # Log payment details for debugging with safe extra data logging
    try:
        logger.info(f"BitcoinSwitch received payment: hash={payment.payment_hash}, is_taproot={is_taproot}, extra={payment.extra}")
    except Exception as e:
        logger.error(f"[WEBSOCKET DEBUG] Error logging payment.extra: {e}")
        logger.info(f"BitcoinSwitch received payment: hash={payment.payment_hash}, is_taproot={is_taproot}, extra=<error serializing>")
    
    # Handle both regular and taproot payments
    if is_taproot:
        # For taproot payments, check if it's a switch payment by id
        if "id" not in payment.extra:
            logger.info(f"Taproot payment missing 'id' in extra data: {payment.extra}")
            logger.info("[WEBSOCKET DEBUG] Exiting early - no 'id' in taproot payment")
            return
    else:
        # For regular payments, check the tag
        if payment.extra.get("tag") != "Switch":
            logger.info(f"[WEBSOCKET DEBUG] Exiting early - tag is '{payment.extra.get('tag')}', not 'Switch'")
            return
    
    logger.info(f"[WEBSOCKET DEBUG] Looking up payment with id: {payment.extra['id']}")
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment.extra["id"])

    if not bitcoinswitch_payment or bitcoinswitch_payment.payment_hash == "paid":
        logger.info(f"[WEBSOCKET DEBUG] Exiting early - payment not found or already paid: {bitcoinswitch_payment}")
        return

    logger.info(f"[WEBSOCKET DEBUG] Found payment, looking up switch: {bitcoinswitch_payment.bitcoinswitch_id}")
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error("no bitcoinswitch found for payment.")
        logger.info("[WEBSOCKET DEBUG] Exiting early - no bitcoinswitch found")
        return

    # Get comment for payload construction
    comment = payment.extra.get("comment")

    # Process payment
    logger.info("[WEBSOCKET DEBUG] Processing payment - updating payment hash")
    bitcoinswitch_payment.payment_hash = bitcoinswitch_payment.payload
    bitcoinswitch_payment = await update_bitcoinswitch_payment(bitcoinswitch_payment)
    payload = bitcoinswitch_payment.payload

    variable = payment.extra.get("variable")
    if variable is True:
        logger.info(f"[WEBSOCKET DEBUG] Variable payment detected: {variable}")
        # For taproot payments, use asset amount if available
        if is_taproot:
            amount = payment.extra.get("asset_amount", int(payment.extra.get("amount", 0)))
        else:
            amount = int(payment.extra["amount"])
        
        # Check for division by zero
        if int(bitcoinswitch_payment.sats) == 0:
            logger.error(f"[WEBSOCKET DEBUG] Cannot calculate variable payload - sats is 0, using amount directly")
            payload = str(amount)
        else:
            payload = str(
                (int(payload) / int(bitcoinswitch_payment.sats)) * amount
            )
        logger.info(f"[WEBSOCKET DEBUG] Calculated variable payload: {payload}")
    
    payload = f"{bitcoinswitch_payment.pin}-{payload}"
    
    if comment:
        payload = f"{payload}-{comment}"

    # Log payment type
    payment_type = "Taproot Asset" if is_taproot else "Lightning"
    asset_info = f" ({payment.extra.get('asset_id')})" if is_taproot else ""
    logger.info(f"Processing {payment_type}{asset_info} payment for switch {bitcoinswitch.id}")

    logger.info(f"[WEBSOCKET DEBUG] Calling websocket_updater with switch_id={bitcoinswitch_payment.bitcoinswitch_id}, payload={payload}")
    
    try:
        result = await websocket_updater(
            bitcoinswitch_payment.bitcoinswitch_id,
            payload,
        )
        logger.info(f"[WEBSOCKET DEBUG] websocket_updater completed successfully, result: {result}")
        return result
    except Exception as e:
        logger.error(f"[WEBSOCKET DEBUG] websocket_updater failed with error: {e}")
        raise
