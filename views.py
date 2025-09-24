"""
Web views for Bitcoin Switch with Taproot Assets.

This module handles the web interface rendering for the Bitcoin Switch extension,
providing the main UI for managing switches and payments.
"""
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

from .crud import get_bitcoinswitch

# Router for web views
bitcoinswitch_generic_router = APIRouter()


def bitcoinswitch_renderer():
    """
    Create template renderer for Bitcoin Switch views.

    Returns:
        Jinja2Templates: Configured template renderer
    """
    return template_renderer(["bitcoinswitch/templates"])


@bitcoinswitch_generic_router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(check_user_exists)):
    """
    Render the main Bitcoin Switch dashboard.

    Args:
        request: FastAPI request object
        user: Authenticated LNbits user

    Returns:
        HTMLResponse: Rendered dashboard template
    """
    return bitcoinswitch_renderer().TemplateResponse(
        "bitcoinswitch/index.html",
        {"request": request, "user": user.json()},
    )


@bitcoinswitch_generic_router.get("/public/{switch_id}", response_class=HTMLResponse)
async def public(
    switch_id: str, request: Request, user: User = Depends(check_user_exists)
):
    """
    Render public view for a specific switch.

    Args:
        switch_id: ID of the switch to display
        request: FastAPI request object
        user: Authenticated LNbits user

    Returns:
        HTMLResponse: Rendered public switch template

    Raises:
        HTTPException: If switch not found or disabled
    """
    switch = await get_bitcoinswitch(switch_id)
    if not switch:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Switch not found."
        )
    if switch.disabled:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Switch disabled.")

    return bitcoinswitch_renderer().TemplateResponse(
        "bitcoinswitch/public.html",
        {"request": request, "user": user.json(), "switch": switch.json()},
    )