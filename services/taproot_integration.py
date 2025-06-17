"""Integration with Taproot Assets extension."""
from typing import Optional, Dict, Any
from loguru import logger
from lnbits.core.crud import get_installed_extensions


class TaprootIntegration:
    """Handle integration with Taproot Assets extension."""
    
    @staticmethod
    async def is_taproot_available() -> bool:
        """Check if Taproot Assets extension is installed and enabled."""
        try:
            extensions = await get_installed_extensions()
            return any(ext.code == "taproot_assets" and ext.active for ext in extensions)
        except Exception as e:
            logger.warning(f"Failed to check taproot availability: {e}")
            return False
    
    @staticmethod
    async def create_taproot_invoice(
        asset_id: str,
        amount: int,
        description: str,
        wallet_id: str,
        user_id: str,
        extra: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Create a Taproot Asset invoice."""
        try:
            # Dynamically import to avoid dependency issues
            from ...taproot_assets.services.invoice_service import InvoiceService
            from ...taproot_assets.models import TaprootInvoiceRequest
            
            request = TaprootInvoiceRequest(
                asset_id=asset_id,
                amount=amount,
                description=description,
                extra=extra
            )
            
            response = await InvoiceService.create_invoice(
                data=request,
                user_id=user_id,
                wallet_id=wallet_id
            )
            
            return {
                "payment_hash": response.payment_hash,
                "payment_request": response.payment_request,
                "checking_id": response.checking_id
            }
            
        except ImportError:
            logger.error("Taproot Assets extension not available")
            return None
        except Exception as e:
            logger.error(f"Failed to create taproot invoice: {e}")
            return None
    
    @staticmethod
    async def get_available_assets(wallet_id: str) -> list[Dict[str, Any]]:
        """Get list of available Taproot Assets."""
        try:
            from ...taproot_assets.services.asset_service import AssetService
            from lnbits.core.models import WalletTypeInfo, Wallet
            
            # Create a mock WalletTypeInfo for the asset service
            wallet = Wallet(id=wallet_id, user="", adminkey="", inkey="", balance_msat=0, name="")
            wallet_info = WalletTypeInfo(wallet=wallet, wallet_type=0)
            
            assets = await AssetService.list_assets(wallet_info)
            return assets
        except Exception as e:
            logger.error(f"Failed to get available assets: {e}")
            return []