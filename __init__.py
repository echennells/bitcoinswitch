"""
Bitcoin Switch Extension with Taproot Assets Support.

This extension allows controlling GPIO pins through Lightning Network payments
and Taproot Asset transfers. It provides:
- Standard Lightning Network payments
- Taproot Asset payments
- Real-time payment processing
- WebSocket updates
- Variable time control
"""
import asyncio
from typing import List, Dict, Any

from fastapi import APIRouter
from loguru import logger

from .crud import db
from .tasks import wait_for_paid_invoices
from .views import bitcoinswitch_generic_router
from .views_api import bitcoinswitch_api_router
from .views_lnurl import bitcoinswitch_lnurl_router

# Main extension router with all endpoints
bitcoinswitch_ext: APIRouter = APIRouter(
    prefix="/bitcoinswitch",
    tags=["bitcoinswitch"]
)

# Include all route handlers
bitcoinswitch_ext.include_router(bitcoinswitch_generic_router)
bitcoinswitch_ext.include_router(bitcoinswitch_api_router)
bitcoinswitch_ext.include_router(bitcoinswitch_lnurl_router)

# Static file configuration
bitcoinswitch_static_files: List[Dict[str, str]] = [
    {
        "path": "/bitcoinswitch/static",
        "name": "bitcoinswitch_static",
    }
]

# Track background tasks
scheduled_tasks: List[asyncio.Task] = []


def bitcoinswitch_start() -> None:
    """
    Start the Bitcoin Switch extension.

    Initializes background tasks for payment processing and monitoring.
    Uses LNbits permanent task system for reliability.
    """
    from lnbits.tasks import create_permanent_unique_task

    try:
        task = create_permanent_unique_task("ext_bitcoinswitch", wait_for_paid_invoices)
        scheduled_tasks.append(task)
        logger.info("Bitcoin Switch extension started successfully")
    except Exception as e:
        logger.error(f"Failed to start Bitcoin Switch extension: {e}")


def bitcoinswitch_stop() -> None:
    """
    Stop the Bitcoin Switch extension.

    Cleanly cancels all background tasks and cleans up resources.
    """
    for task in scheduled_tasks:
        try:
            task.cancel()
            logger.debug(f"Cancelled task: {task}")
        except Exception as ex:
            logger.warning(f"Error cancelling task: {ex}")

    scheduled_tasks.clear()
    logger.info("Bitcoin Switch extension stopped")


__all__ = [
    "db",
    "bitcoinswitch_ext",
    "bitcoinswitch_static_files",
    "bitcoinswitch_start",
    "bitcoinswitch_stop",
]