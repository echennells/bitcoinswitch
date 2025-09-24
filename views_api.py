"""
API endpoints for Bitcoin Switch management.

This module handles all REST API endpoints for managing Bitcoin Switch devices,
including creation, updates, retrieval, and deletion. Supports both standard
Lightning Network and Taproot Asset functionality.
"""
from http import HTTPStatus
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from lnbits.core.crud import get_user
from lnbits.core.models import WalletTypeInfo
from lnbits.core.services import websocket_updater
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from loguru import logger

from .crud import (
    create_bitcoinswitch,
    delete_bitcoinswitch,
    get_bitcoinswitch,
    get_bitcoinswitches,
    update_bitcoinswitch,
)
from .models import Bitcoinswitch, CreateBitcoinswitch

# API router for Bitcoin Switch endpoints
bitcoinswitch_api_router = APIRouter(prefix="/api/v1")


@bitcoinswitch_api_router.post("", dependencies=[Depends(require_admin_key)])
async def api_bitcoinswitch_create(data: CreateBitcoinswitch) -> Bitcoinswitch:
    """
    Create a new Bitcoin Switch device.

    Creates a switch configuration with support for both standard Lightning
    payments and Taproot Asset payments.

    Args:
        data: Switch configuration including title, wallet, currency, switches, etc.

    Returns:
        Bitcoinswitch: The created switch device

    Raises:
        HTTPException: If creation fails
    """
    try:
        return await create_bitcoinswitch(data)
    except Exception as e:
        logger.error(f"Failed to create bitcoinswitch: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to create Bitcoin Switch"
        )


@bitcoinswitch_api_router.put("/trigger/{switch_id}/{pin}")
async def api_bitcoinswitch_trigger(
    switch_id: str, pin: int, key_info: WalletTypeInfo = Depends(require_admin_key)
) -> None:
    """
    Manually trigger a switch pin.

    Allows manual activation of a switch without payment.

    Args:
        switch_id: ID of the switch to trigger
        pin: GPIO pin to activate
        key_info: Wallet authentication info

    Raises:
        HTTPException: If switch not found or permission denied
    """
    switch = await get_bitcoinswitch(switch_id)
    if not switch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Bitcoinswitch does not exist."
        )

    _switch = next((s for s in switch.switches if s.pin == pin), None)
    if not _switch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Switch with this pin does not exist.",
        )

    if switch.wallet != key_info.wallet.id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="You do not have permission to trigger this switch.",
        )

    await websocket_updater(switch.id, f"{pin}-{_switch.duration}")
    logger.info(f"Manually triggered switch {switch_id} pin {pin}")


@bitcoinswitch_api_router.put("/{bitcoinswitch_id}")
async def api_bitcoinswitch_update(
    data: CreateBitcoinswitch,
    bitcoinswitch_id: str,
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> Bitcoinswitch:
    """
    Update an existing Bitcoin Switch device.

    Updates switch configuration while preserving Taproot Asset settings.

    Args:
        data: Updated switch configuration
        bitcoinswitch_id: ID of the switch to update
        key_info: Wallet authentication info

    Returns:
        Bitcoinswitch: The updated switch device

    Raises:
        HTTPException: If switch not found or permission denied
    """
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="bitcoinswitch does not exist"
        )

    if bitcoinswitch.wallet != key_info.wallet.id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="You do not have permission to update this bitcoinswitch.",
        )

    for k, v in data.dict().items():
        if v is not None:
            setattr(bitcoinswitch, k, v)

    bitcoinswitch.switches = data.switches
    return await update_bitcoinswitch(bitcoinswitch)


@bitcoinswitch_api_router.get("")
async def api_bitcoinswitchs_retrieve(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> list[Bitcoinswitch]:
    """
    Retrieve all Bitcoin Switch devices for the authenticated user.

    Returns devices with full Taproot Asset configuration if enabled.

    Args:
        key_info: Wallet authentication info

    Returns:
        list[Bitcoinswitch]: List of switch devices

    Raises:
        HTTPException: If user not found
    """
    user = await get_user(key_info.wallet.user)
    if not user:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="User does not exist"
        )
    return await get_bitcoinswitches(user.wallet_ids)


@bitcoinswitch_api_router.get("/{bitcoinswitch_id}")
async def api_bitcoinswitch_retrieve(
    bitcoinswitch_id: str, key_info: WalletTypeInfo = Depends(require_admin_key)
) -> Bitcoinswitch:
    """
    Retrieve a specific Bitcoin Switch device.

    Args:
        bitcoinswitch_id: ID of the switch to retrieve
        key_info: Wallet authentication info

    Returns:
        Bitcoinswitch: The switch device

    Raises:
        HTTPException: If switch not found or permission denied
    """
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Bitcoinswitch does not exist"
        )

    if bitcoinswitch.wallet != key_info.wallet.id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="You do not have permission to access this bitcoinswitch.",
        )
    return bitcoinswitch


@bitcoinswitch_api_router.delete("/{bitcoinswitch_id}")
async def api_bitcoinswitch_delete(
    bitcoinswitch_id: str, key_info: WalletTypeInfo = Depends(require_admin_key)
) -> None:
    """
    Delete a Bitcoin Switch device.

    Args:
        bitcoinswitch_id: ID of the switch to delete
        key_info: Wallet authentication info

    Raises:
        HTTPException: If switch not found or permission denied
    """
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Bitcoinswitch does not exist."
        )

    if bitcoinswitch.wallet != key_info.wallet.id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="You do not have permission to delete this bitcoinswitch.",
        )

    await delete_bitcoinswitch(bitcoinswitch_id)
    logger.info(f"Deleted bitcoinswitch {bitcoinswitch_id}")