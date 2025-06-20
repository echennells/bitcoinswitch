"""Integration with Taproot Assets extension."""
from typing import Optional, Dict, Any
from loguru import logger
from lnbits.core.crud import get_installed_extensions, get_wallet
from .taproot_api_client import TaprootAPIClient


class TaprootIntegration:
    """Handle integration with Taproot Assets extension."""
    
    _client = TaprootAPIClient()
    
    @staticmethod
    async def is_taproot_available() -> bool:
        """Check if Taproot Assets extension is installed and enabled."""
        try:
            extensions = await get_installed_extensions()
            return any(ext.id == "taproot_assets" and ext.active for ext in extensions)
        except Exception as e:
            logger.warning(f"Failed to check taproot availability: {e}")
            return False
    
    @staticmethod
    async def create_rfq_invoice(
        asset_id: str,
        amount: int,
        description: str,
        wallet_id: str,
        user_id: str,
        extra: Dict[str, Any],
        peer_pubkey: Optional[str] = None,
        expiry: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a Taproot Asset invoice via API."""
        try:
            # Get wallet for API key
            wallet = await get_wallet(wallet_id)
            if not wallet:
                logger.error("Wallet not found")
                return None
            
            # Prepare invoice data
            invoice_data = {
                "asset_id": asset_id,
                "amount": amount,
                "description": description,
                "extra": extra
            }
            
            if expiry:
                invoice_data["expiry"] = expiry
            if peer_pubkey:
                invoice_data["peer_pubkey"] = peer_pubkey
            
            # Call API
            result = await TaprootIntegration._client.post(
                "/taproot/invoices",
                invoice_data,
                wallet.adminkey
            )
            
            if result:
                return {
                    "payment_hash": result.get("payment_hash"),
                    "payment_request": result.get("payment_request"),
                    "checking_id": result.get("checking_id"),
                    "is_rfq": True
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to create RFQ invoice: {e}")
            return None