"""
Background tasks for Bitcoin Switch payment processing.

This module handles the processing of both standard Lightning Network payments
and Taproot Asset payments, managing the payment queue and switch activation.

Key features:
- Asynchronous payment processing
- Support for variable-time payments
- Websocket notifications
- Both Lightning and Taproot Asset payment handling
"""
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
                payment_id=payment.payment_hash if payment else None,
                error=str(e)
            )


def calculate_variable_payload(
    base_payload: str,
    base_sats: int,
    payment_amount: int
) -> Optional[str]:
    """
    Calculate payload for variable-time payments.
    
    Args:
        base_payload: Original duration in milliseconds
        base_sats: Original payment amount in sats
        payment_amount: Actual payment amount received
        
    Returns:
        str: Adjusted duration, or None if calculation fails
    """
    try:
        if base_sats <= 0:
            logger.error(
                "Cannot calculate variable payload - base amount is 0",
                base_payload=base_payload,
                payment_amount=payment_amount
            )
            return None
        return str((int(base_payload) / base_sats) * payment_amount)
    except (ValueError, ZeroDivisionError) as e:
        logger.error(
            "Failed to calculate variable payload",
            error=str(e),
            base_payload=base_payload,
            base_sats=base_sats,
            payment_amount=payment_amount
        )
        return None


async def on_invoice_paid(payment: Payment) -> None:
    """
    Process a paid invoice and activate the corresponding switch.
    
    Handles both standard Lightning Network payments and Taproot Asset payments.
    Supports variable time calculations and comment handling.
    
    Args:
        payment: The completed payment to process
        
    Note:
        For Taproot payments, asset amounts are used for variable time calculation
        instead of satoshi amounts when available.
    """
    # Validate payment type
    is_taproot = payment.extra.get("is_taproot", False)
    payment_id = payment.extra.get("id")
    
    if is_taproot:
        if not payment_id:
            logger.warning("Taproot payment missing 'id' in extra data")
            return
    else:
        if payment.extra.get("tag") != "Switch":
            return

    # Get and validate payment record
    bitcoinswitch_payment = await get_bitcoinswitch_payment(payment_id)
    if not bitcoinswitch_payment:
        logger.error("Payment not found", payment_id=payment_id)
        return
        
    if bitcoinswitch_payment.payment_hash == "paid":
        logger.info("Payment already processed", payment_id=payment_id)
        return

    # Get and validate switch
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_payment.bitcoinswitch_id)
    if not bitcoinswitch:
        logger.error(
            "No bitcoinswitch found for payment",
            payment_id=payment_id,
            switch_id=bitcoinswitch_payment.bitcoinswitch_id
        )
        return

    # Update payment status
    bitcoinswitch_payment.payment_hash = bitcoinswitch_payment.payload
    bitcoinswitch_payment = await update_bitcoinswitch_payment(bitcoinswitch_payment)
    payload = bitcoinswitch_payment.payload

    # Handle variable time payments
    if payment.extra.get("variable") is True:
        if is_taproot:
            # For Taproot, prefer asset amount over sat amount
            amount = payment.extra.get(
                "asset_amount", 
                int(payment.extra.get("amount", 0))
            )
        else:
            amount = int(payment.extra["amount"])
        
        payload = calculate_variable_payload(
            base_payload=payload,
            base_sats=bitcoinswitch_payment.sats,
            payment_amount=amount
        )
        if not payload:
            # Use original amount if calculation fails
            payload = str(amount)
    
    # Build final command payload
    payload = f"{bitcoinswitch_payment.pin}-{payload}"
    if comment := payment.extra.get("comment"):
        payload = f"{payload}-{comment}"

    # Send command to switch
    try:
        await websocket_updater(
            bitcoinswitch_payment.bitcoinswitch_id,
            payload,
        )
        logger.info(
            "Successfully sent switch command",
            payment_id=payment_id,
            switch_id=bitcoinswitch_payment.bitcoinswitch_id,
            pin=bitcoinswitch_payment.pin
        )
    except Exception as e:
        logger.error(
            "Failed to update websocket",
            payment_id=payment_id,
            switch_id=bitcoinswitch_payment.bitcoinswitch_id,
            error=str(e)
        )
        raise  # Re-raise to trigger global error handler