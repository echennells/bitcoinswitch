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
            return any(ext.id == "taproot_assets" and ext.active for ext in extensions)
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
        """
        Create a Taproot Asset invoice using the RFQ (Request for Quote) process.
        This creates an invoice that can be paid with either sats or the specified asset.
        """
        try:
            # Dynamically import to avoid dependency issues
            from ...taproot_assets.tapd.taproot_factory import TaprootAssetsFactory
            
            # Create a wallet instance
            taproot_wallet = await TaprootAssetsFactory.create_wallet(
                user_id=user_id,
                wallet_id=wallet_id
            )
            
            # Use the RFQ invoice creation method
            # Only pass peer_pubkey if it's actually provided (not None)
            invoice_params = {
                "description": description,
                "asset_id": asset_id,
                "asset_amount": amount,
                "expiry": expiry
            }
            if peer_pubkey is not None:
                invoice_params["peer_pubkey"] = peer_pubkey
                
            invoice_result = await taproot_wallet.get_raw_node_invoice(**invoice_params)
            
            if not invoice_result or "invoice_result" not in invoice_result:
                logger.error("Failed to create RFQ invoice: Invalid response")
                return None
            
            # Extract payment details
            payment_hash = invoice_result["invoice_result"]["r_hash"]
            payment_request = invoice_result["invoice_result"]["payment_request"]
            
            # Store the invoice in the database
            from ...taproot_assets.crud.invoices import create_invoice
            from ...taproot_assets.tapd_settings import taproot_settings
            from ...taproot_assets.db_utils import transaction
            
            # Get satoshi fee from settings
            satoshi_amount = taproot_settings.default_sat_fee
            
            # Create invoice record
            async with transaction() as conn:
                invoice = await create_invoice(
                    asset_id=asset_id,
                    asset_amount=amount,
                    satoshi_amount=satoshi_amount,
                    payment_hash=payment_hash,
                    payment_request=payment_request,
                    user_id=user_id,
                    wallet_id=wallet_id,
                    description=description,
                    expiry=expiry,
                    extra=extra,
                    conn=conn
                )
            
            return {
                "payment_hash": payment_hash,
                "payment_request": payment_request,
                "checking_id": payment_hash,
                "is_rfq": True,
                "accepted_buy_quote": invoice_result.get("accepted_buy_quote", {})
            }
            
        except ImportError as e:
            logger.error(f"Taproot Assets extension not available: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to create RFQ invoice: {e}")
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