"""
Background tasks for Bitcoin Switch payment processing.

This module handles the processing of both standard Lightning Network payments
and Taproot Asset payments, managing the payment queue and switch activation.

Key features:
- Asynchronous payment processing
- Support for variable-time payments
- Websocket notifications
- Both Lightning and Taproot Asset payment handling
- Password protection support
"""
import asyncio

from lnbits.core.models import Payment
from lnbits.core.services import websocket_manager
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import (
    get_bitcoinswitch,
    get_bitcoinswitch_payment,
    get_switch_payment_by_payment_hash,
    update_bitcoinswitch_payment,
)


async def wait_for_paid_invoices() -> None:
    """
    Main payment processing loop.

    Registers a listener for incoming payments and processes them as they arrive.
    Handles both standard Lightning payments and Taproot Asset payments.
    """
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_bitcoinswitch")
    logger.info("BitcoinSwitch invoice listener registered")

    while True:
        try:
            payment = await invoice_queue.get()
            await on_invoice_paid(payment)
        except Exception as e:
            logger.error(
                "Error processing payment",
                exc_info=True,
                payment_id=getattr(payment, 'payment_hash', None),
                error=str(e)
            )


async def on_invoice_paid(payment: Payment) -> None:
    """
    Process a paid invoice and activate the corresponding switch.

    Handles both standard Lightning Network payments and Taproot Asset payments.
    Supports variable time calculations, comment handling, and password protection.

    Args:
        payment: The completed payment to process
    """
    # Check if this is a Taproot Asset payment
    is_taproot = payment.extra.get("is_taproot", False)

    # Handle Taproot Asset payments differently
    if is_taproot:
        payment_id = payment.extra.get("id")
        if not payment_id:
            logger.warning("Taproot payment missing 'id' in extra data")
            return

        # Get payment by ID for Taproot
        switch_payment = await get_bitcoinswitch_payment(payment_id)
        if not switch_payment:
            logger.error("Taproot payment not found", payment_id=payment_id)
            return
    else:
        # Standard Lightning payment
        if payment.extra.get("tag") != "Switch":
            return

        # Get payment by payment hash for standard Lightning
        switch_payment = await get_switch_payment_by_payment_hash(payment.payment_hash)
        if not switch_payment:
            logger.warning(
                f"Switch payment not found for payment hash: {payment.payment_hash}"
            )
            return

    # Get the switch configuration
    bitcoinswitch = await get_bitcoinswitch(switch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error("No bitcoinswitch found for payment.")
        return

    # Find the specific switch pin configuration
    _switch = next(
        (s for s in bitcoinswitch.switches if s.pin == switch_payment.pin),
        None,
    )
    if not _switch:
        logger.error(f"Switch with pin {switch_payment.pin} not found.")
        return

    # Calculate duration
    duration = _switch.duration

    # Handle variable time payments
    if _switch.variable is True:
        if is_taproot:
            # For Taproot, use asset amount if available
            amount = payment.extra.get("asset_amount", switch_payment.sats)
            if _switch.amount > 0:
                duration = round(amount / _switch.amount * _switch.duration)
        else:
            # For standard Lightning, use sats
            if _switch.amount > 0:
                duration = round(
                    (switch_payment.sats / 1000) / _switch.amount * _switch.duration
                )

    # Build the payload
    payload = f"{_switch.pin}-{duration}"

    # Add comment if present
    comment = payment.extra.get("comment")
    if comment:
        payload = f"{payload}-{comment}"

    # Check password if configured
    if bitcoinswitch.password and bitcoinswitch.password != comment:
        logger.info(f"Wrong password entered for bitcoin switch: {bitcoinswitch.id}")
        return

    # Update payment status for Taproot payments
    if is_taproot and hasattr(switch_payment, 'payment_hash'):
        switch_payment.payment_hash = "paid"
        await update_bitcoinswitch_payment(switch_payment)

    # Send command to switch via websocket
    try:
        await websocket_manager.send(bitcoinswitch.id, payload)
        logger.info(
            "Successfully sent switch command",
            switch_id=bitcoinswitch.id,
            pin=_switch.pin,
            duration=duration,
            is_taproot=is_taproot
        )
    except Exception as e:
        logger.error(
            f"Failed to send websocket update: {e}",
            switch_id=bitcoinswitch.id,
            payload=payload
        )