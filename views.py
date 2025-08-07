"""
Web views for Bitcoin Switch with Taproot Assets.

This module handles the web interface rendering for the Bitcoin Switch extension,
providing the main UI for managing switches and payments.
"""
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

# Router for web views
bitcoinswitch_generic_router = APIRouter()


def bitcoinswitch_renderer() -> Jinja2Templates:
    """
    Create template renderer for Bitcoin Switch views.

    Returns:
        Jinja2Templates: Configured template renderer
    """
    return template_renderer(["bitcoinswitch/templates"])


@bitcoinswitch_generic_router.get(
    "/",
    response_class=HTMLResponse,
    summary="Bitcoin Switch Dashboard",
    description="Main interface for managing Bitcoin Switch devices and Taproot Asset payments"
)
async def index(
    request: Request,
    user: User = Depends(check_user_exists)
) -> HTMLResponse:
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
        {
            "request": request,
            "user": user.json(),
        }
    )