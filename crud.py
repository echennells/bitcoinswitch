"""
Database operations for the Bitcoin Switch extension.

This module handles all database interactions for switches and payments,
including creation, updates, retrievals, and deletions. Supports both
standard Lightning Network payments and Taproot Asset payments.
"""
from datetime import datetime, timezone
from typing import Optional

from lnbits.db import Database
from lnbits.helpers import urlsafe_short_hash

from .models import (
    Bitcoinswitch,
    BitcoinswitchPayment,
    CreateBitcoinswitch,
)

# Database instance for the extension
db = Database("ext_bitcoinswitch")


async def create_bitcoinswitch(
    data: CreateBitcoinswitch,
) -> Bitcoinswitch:
    """
    Create a new Bitcoin Switch device.

    Args:
        data: Switch configuration data including title, wallet, currency, etc.

    Returns:
        Bitcoinswitch: The newly created switch device
    """
    bitcoinswitch_id = urlsafe_short_hash()
    device = Bitcoinswitch(
        id=bitcoinswitch_id,
        title=data.title,
        wallet=data.wallet,
        currency=data.currency,
        switches=data.switches,
        password=data.password,
        disabled=data.disabled,
        disposable=data.disposable,
    )
    await db.insert("bitcoinswitch.switch", device)
    return device


async def update_bitcoinswitch(device: Bitcoinswitch) -> Bitcoinswitch:
    """
    Update an existing Bitcoin Switch device.

    Args:
        device: The switch device with updated data

    Returns:
        Bitcoinswitch: The updated switch device
    """
    device.updated_at = datetime.now(timezone.utc)
    await db.update("bitcoinswitch.switch", device)
    return device


async def get_bitcoinswitch(bitcoinswitch_id: str) -> Bitcoinswitch | None:
    """
    Retrieve a Bitcoin Switch device by ID.

    Args:
        bitcoinswitch_id: ID of the switch to retrieve

    Returns:
        Optional[Bitcoinswitch]: The switch device if found, None otherwise
    """
    return await db.fetchone(
        "SELECT * FROM bitcoinswitch.switch WHERE id = :id",
        {"id": bitcoinswitch_id},
        Bitcoinswitch,
    )


async def get_bitcoinswitches(wallet_ids: list[str]) -> list[Bitcoinswitch]:
    """
    Retrieve all Bitcoin Switch devices for given wallet IDs.

    Args:
        wallet_ids: List of wallet IDs to fetch switches for

    Returns:
        list[Bitcoinswitch]: List of found switch devices
    """
    if len(wallet_ids) == 0:
        return []
    q = ",".join([f"'{w}'" for w in wallet_ids])
    return await db.fetchall(
        f"""
        SELECT * FROM bitcoinswitch.switch WHERE wallet IN ({q})
        ORDER BY id
        """,
        model=Bitcoinswitch,
    )


async def delete_bitcoinswitch(bitcoinswitch_id: str) -> None:
    """
    Delete a Bitcoin Switch device.

    Args:
        bitcoinswitch_id: ID of the switch to delete
    """
    await db.execute(
        "DELETE FROM bitcoinswitch.switch WHERE id = :id",
        {"id": bitcoinswitch_id},
    )


async def create_switch_payment(
    payment_hash: str,
    switch_id: str,
    pin: int,
    amount_msat: int = 0,
) -> BitcoinswitchPayment:
    """
    Create a new payment record (upstream naming convention).

    Args:
        payment_hash: Lightning payment hash
        switch_id: ID of the switch being paid
        pin: GPIO pin being controlled
        amount_msat: Payment amount in millisatoshis

    Returns:
        BitcoinswitchPayment: The created payment record
    """
    payment_id = urlsafe_short_hash()
    payment = BitcoinswitchPayment(
        id=payment_id,
        payment_hash=payment_hash,
        bitcoinswitch_id=switch_id,
        pin=pin,
        sats=amount_msat // 1000,  # Convert msat to sat
    )
    await db.insert("bitcoinswitch.payment", payment)
    return payment


# Keep your original function name for backward compatibility with Taproot Assets code
async def create_bitcoinswitch_payment(
    bitcoinswitch_id: str,
    payment_hash: str,
    payload: str,
    pin: int,
    amount_msat: int = 0,
) -> BitcoinswitchPayment:
    """
    Create a new payment record (your naming convention for Taproot Assets).

    Args:
        bitcoinswitch_id: ID of the switch being paid
        payment_hash: Lightning payment hash
        payload: Payment metadata
        pin: GPIO pin being controlled
        amount_msat: Payment amount in millisatoshis

    Returns:
        BitcoinswitchPayment: The created payment record
    """
    bitcoinswitchpayment_id = urlsafe_short_hash()
    payment = BitcoinswitchPayment(
        id=bitcoinswitchpayment_id,
        bitcoinswitch_id=bitcoinswitch_id,
        payment_hash=payment_hash,
        pin=pin,
        sats=amount_msat // 1000,  # Convert msat to sat
        payload=payload,  # Keep for backward compatibility
    )
    await db.insert("bitcoinswitch.payment", payment)
    return payment


async def update_switch_payment(
    switch_payment: BitcoinswitchPayment,
) -> BitcoinswitchPayment:
    """Update a payment record (upstream naming)."""
    switch_payment.updated_at = datetime.now(timezone.utc)
    await db.update("bitcoinswitch.payment", switch_payment)
    return switch_payment


# Keep your original function name for backward compatibility
async def update_bitcoinswitch_payment(
    bitcoinswitch_payment: BitcoinswitchPayment,
) -> BitcoinswitchPayment:
    """
    Update an existing switch payment record (your naming for Taproot Assets).

    Args:
        bitcoinswitch_payment: The payment record with updated data

    Returns:
        BitcoinswitchPayment: The updated payment record
    """
    bitcoinswitch_payment.updated_at = datetime.now(timezone.utc)
    await db.update("bitcoinswitch.payment", bitcoinswitch_payment)
    return bitcoinswitch_payment


async def delete_switch_payment(switch_payment_id: str) -> None:
    """Delete a payment record (upstream naming)."""
    await db.execute(
        "DELETE FROM bitcoinswitch.payment WHERE id = :id",
        {"id": switch_payment_id},
    )


# Keep your original function name for backward compatibility
async def delete_bitcoinswitch_payment(bitcoinswitch_payment_id: str) -> None:
    """
    Delete a payment record (your naming for Taproot Assets).

    Args:
        bitcoinswitch_payment_id: ID of the payment to delete
    """
    await db.execute(
        "DELETE FROM bitcoinswitch.payment WHERE id = :id",
        {"id": bitcoinswitch_payment_id},
    )


async def get_switch_payment(
    bitcoinswitchpayment_id: str,
) -> BitcoinswitchPayment | None:
    """Get payment by ID (upstream naming)."""
    return await db.fetchone(
        "SELECT * FROM bitcoinswitch.payment WHERE id = :id",
        {"id": bitcoinswitchpayment_id},
        BitcoinswitchPayment,
    )


# Keep your original function name for backward compatibility
async def get_bitcoinswitch_payment(
    bitcoinswitchpayment_id: str,
) -> Optional[BitcoinswitchPayment]:
    """
    Retrieve a payment record by ID (your naming for Taproot Assets).

    Args:
        bitcoinswitchpayment_id: ID of the payment to retrieve

    Returns:
        Optional[BitcoinswitchPayment]: The payment record if found, None otherwise
    """
    return await db.fetchone(
        "SELECT * FROM bitcoinswitch.payment WHERE id = :id",
        {"id": bitcoinswitchpayment_id},
        BitcoinswitchPayment,
    )


async def get_switch_payment_by_payment_hash(
    payment_hash: str,
) -> BitcoinswitchPayment | None:
    """Get payment by payment hash (upstream function)."""
    return await db.fetchone(
        "SELECT * FROM bitcoinswitch.payment WHERE payment_hash = :h",
        {"h": payment_hash},
        BitcoinswitchPayment,
    )


async def get_switch_payments(
    bitcoinswitch_ids: list[str],
) -> list[BitcoinswitchPayment]:
    """Get all payments for given switch IDs (upstream function)."""
    if len(bitcoinswitch_ids) == 0:
        return []
    q = ",".join([f"'{w}'" for w in bitcoinswitch_ids])
    return await db.fetchall(
        f"""
        SELECT * FROM bitcoinswitch.payment WHERE bitcoinswitch_id IN ({q})
        ORDER BY id
        """,
        model=BitcoinswitchPayment,
    )