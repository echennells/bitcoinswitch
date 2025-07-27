"""
Database operations for the Bitcoin Switch extension.

This module handles all database interactions for switches and payments,
including creation, updates, retrievals, and deletions.
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
    bitcoinswitch_id: str,
    data: CreateBitcoinswitch,
) -> Bitcoinswitch:
    """
    Create a new Bitcoin Switch device.

    Args:
        bitcoinswitch_id: Unique identifier for the switch
        data: Switch configuration data including title, wallet, currency, etc.

    Returns:
        Bitcoinswitch: The newly created switch device
    """
    bitcoinswitch_key = urlsafe_short_hash()
    device = Bitcoinswitch(
        id=bitcoinswitch_id,
        key=bitcoinswitch_key,
        title=data.title,
        wallet=data.wallet,
        currency=data.currency,
        switches=data.switches,
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


async def get_bitcoinswitch(bitcoinswitch_id: str) -> Optional[Bitcoinswitch]:
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
    if not wallet_ids:
        return []
    
    # Build SQL query with correct number of placeholders
    placeholders = ",".join([f":wallet_{i}" for i in range(len(wallet_ids))])
    params = {f"wallet_{i}": wallet_id for i, wallet_id in enumerate(wallet_ids)}
    
    return await db.fetchall(
        f"SELECT * FROM bitcoinswitch.switch WHERE wallet IN ({placeholders}) ORDER BY id",
        params,
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


async def create_bitcoinswitch_payment(
    bitcoinswitch_id: str,
    payment_hash: str,
    payload: str,
    pin: int,
    amount_msat: int = 0,
) -> BitcoinswitchPayment:
    """
    Create a new payment record for a switch activation.

    Args:
        bitcoinswitch_id: ID of the switch being paid for
        payment_hash: Lightning payment hash
        payload: Payment metadata
        pin: GPIO pin number
        amount_msat: Payment amount in millisatoshis

    Returns:
        BitcoinswitchPayment: The newly created payment record
    """
    bitcoinswitchpayment_id = urlsafe_short_hash()
    payment = BitcoinswitchPayment(
        id=bitcoinswitchpayment_id,
        bitcoinswitch_id=bitcoinswitch_id,
        payload=payload,
        pin=pin,
        payment_hash=payment_hash,
        sats=amount_msat,
    )
    await db.insert("bitcoinswitch.payment", payment)
    return payment


async def update_bitcoinswitch_payment(
    bitcoinswitch_payment: BitcoinswitchPayment,
) -> BitcoinswitchPayment:
    """
    Update an existing switch payment record.

    Args:
        bitcoinswitch_payment: The payment record with updated data

    Returns:
        BitcoinswitchPayment: The updated payment record
    """
    bitcoinswitch_payment.updated_at = datetime.now(timezone.utc)
    await db.update("bitcoinswitch.payment", bitcoinswitch_payment)
    return bitcoinswitch_payment


async def get_bitcoinswitch_payment(
    bitcoinswitchpayment_id: str,
) -> Optional[BitcoinswitchPayment]:
    """
    Retrieve a payment record by ID.

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


async def delete_bitcoinswitch_payment(bitcoinswitch_payment_id: str) -> None:
    """
    Delete a payment record.

    Args:
        bitcoinswitch_payment_id: ID of the payment to delete
    """
    await db.execute(
        "DELETE FROM bitcoinswitch.payment WHERE id = :id",
        {"id": bitcoinswitch_payment_id},
    )