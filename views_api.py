"""
API endpoints for Bitcoin Switch management.

This module handles all REST API endpoints for managing Bitcoin Switch devices,
including creation, updates, retrieval, and deletion. Supports both standard
Lightning Network and Taproot Asset functionality.
"""
from http import HTTPStatus
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from lnbits.core.crud import get_user
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnbits.helpers import urlsafe_short_hash
from loguru import logger
from lnurl.exceptions import InvalidUrl

from .crud import (
    create_bitcoinswitch,
    delete_bitcoinswitch,
    get_bitcoinswitch,
    get_bitcoinswitches,
    update_bitcoinswitch,
)
from .models import Bitcoinswitch, CreateBitcoinswitch

# API router for Bitcoin Switch endpoints
bitcoinswitch_api_router = APIRouter()


@bitcoinswitch_api_router.post(
    "/api/v1/bitcoinswitch",
    dependencies=[Depends(require_admin_key)],
    response_model=Bitcoinswitch,
    status_code=HTTPStatus.CREATED,
)
async def api_bitcoinswitch_create(
    request: Request,
    data: CreateBitcoinswitch
) -> Bitcoinswitch:
    """
    Create a new Bitcoin Switch device.

    Args:
        request: FastAPI request object
        data: Switch configuration data

    Returns:
        Bitcoinswitch: The newly created switch device

    Raises:
        HTTPException: If LNURL generation fails
    """
    bitcoinswitch_id = urlsafe_short_hash()

    # Generate LNURL for each switch
    url = request.url_for(
        "bitcoinswitch.lnurl_params",
        bitcoinswitch_id=bitcoinswitch_id
    )
    
    for switch in data.switches:
        try:
            switch.set_lnurl(str(url))
        except InvalidUrl as exc:
            logger.error(f"Invalid LNURL generated: {url}")
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid LNURL configuration: {str(exc)}",
            ) from exc

    return await create_bitcoinswitch(bitcoinswitch_id, data)


@bitcoinswitch_api_router.put(
    "/api/v1/bitcoinswitch/{bitcoinswitch_id}",
    dependencies=[Depends(require_admin_key)],
    response_model=Bitcoinswitch,
)
async def api_bitcoinswitch_update(
    request: Request,
    data: CreateBitcoinswitch,
    bitcoinswitch_id: str
) -> Bitcoinswitch:
    """
    Update an existing Bitcoin Switch device.

    Args:
        request: FastAPI request object
        data: Updated switch configuration
        bitcoinswitch_id: ID of switch to update

    Returns:
        Bitcoinswitch: The updated switch device

    Raises:
        HTTPException: If switch not found or LNURL generation fails
    """
    # Verify switch exists
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        logger.warning(f"Attempted to update non-existent switch: {bitcoinswitch_id}")
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Bitcoin Switch device not found"
        )

    # Update fields
    for k, v in data.dict().items():
        if v is not None:
            setattr(bitcoinswitch, k, v)

    # Regenerate LNURLs
    url = request.url_for(
        "bitcoinswitch.lnurl_params",
        bitcoinswitch_id=bitcoinswitch_id
    )
    
    for switch in data.switches:
        try:
            switch.set_lnurl(str(url))
        except InvalidUrl as exc:
            logger.error(f"Invalid LNURL on update: {url}")
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Invalid LNURL configuration: {str(exc)}",
            ) from exc

    bitcoinswitch.switches = data.switches
    return await update_bitcoinswitch(bitcoinswitch)


@bitcoinswitch_api_router.get(
    "/api/v1/bitcoinswitch",
    response_model=List[Bitcoinswitch]
)
async def api_bitcoinswitches_retrieve(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> List[Bitcoinswitch]:
    """
    Retrieve all Bitcoin Switch devices for a user.

    Args:
        key_info: User's wallet information

    Returns:
        List[Bitcoinswitch]: List of user's switch devices

    Raises:
        HTTPException: If user not found
    """
    user = await get_user(key_info.wallet.user)
    if not user:
        logger.error(f"User not found for wallet: {key_info.wallet.id}")
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="User not found"
        )
    return await get_bitcoinswitches(user.wallet_ids)


@bitcoinswitch_api_router.get(
    "/api/v1/bitcoinswitch/{bitcoinswitch_id}",
    dependencies=[Depends(require_invoice_key)],
    response_model=Bitcoinswitch,
)
async def api_bitcoinswitch_retrieve(bitcoinswitch_id: str) -> Bitcoinswitch:
    """
    Retrieve a specific Bitcoin Switch device.

    Args:
        bitcoinswitch_id: ID of switch to retrieve

    Returns:
        Bitcoinswitch: The requested switch device

    Raises:
        HTTPException: If switch not found
    """
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        logger.warning(f"Attempted to retrieve non-existent switch: {bitcoinswitch_id}")
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Bitcoin Switch device not found"
        )
    return bitcoinswitch


@bitcoinswitch_api_router.delete(
    "/api/v1/bitcoinswitch/{bitcoinswitch_id}",
    dependencies=[Depends(require_admin_key)],
    status_code=HTTPStatus.NO_CONTENT,
)
async def api_bitcoinswitch_delete(bitcoinswitch_id: str) -> None:
    """
    Delete a Bitcoin Switch device.

    Args:
        bitcoinswitch_id: ID of switch to delete

    Raises:
        HTTPException: If switch not found
    """
    bitcoinswitch = await get_bitcoinswitch(bitcoinswitch_id)
    if not bitcoinswitch:
        logger.warning(f"Attempted to delete non-existent switch: {bitcoinswitch_id}")
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Bitcoin Switch device not found"
        )
    await delete_bitcoinswitch(bitcoinswitch_id)