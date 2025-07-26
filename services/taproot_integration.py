"""Integration with Taproot Assets extension."""
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from loguru import logger
from lnbits.core.crud import get_installed_extensions


@dataclass
class TaprootError:
    """Error class for Taproot integration errors."""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def __str__(self):
        return f"{self.code}: {self.message}"


class TaprootIntegration:
    """Handle integration with Taproot Assets extension."""
    
    @staticmethod
    async def is_taproot_available() -> Tuple[bool, Optional[TaprootError]]:
        """
        Check if Taproot Assets extension is installed and enabled.
        
        Returns:
            Tuple[bool, Optional[TaprootError]]: (is_available, error_if_any)
        """
        try:
            extensions = await get_installed_extensions()
            is_available = any(ext.id == "taproot_assets" and ext.active for ext in extensions)
            
            if not is_available:
                return False, TaprootError(
                    code="TAPROOT_NOT_AVAILABLE",
                    message="Taproot Assets extension is not installed or not active",
                    details={"installed_extensions": [ext.id for ext in extensions]}
                )
                
            return True, None
            
        except Exception as e:
            error = TaprootError(
                code="TAPROOT_CHECK_FAILED",
                message="Failed to check taproot availability",
                details={"error": str(e)}
            )
            logger.warning(str(error))
            return False, error
    
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
    ) -> Tuple[Optional[Dict[str, Any]], Optional[TaprootError]]:
        """
        Create a Taproot Asset invoice using the RFQ (Request for Quote) process.
        This creates an invoice that can be paid with either sats or the specified asset.
        
        Returns:
            Tuple[Optional[Dict], Optional[TaprootError]]: (invoice_data, error_if_any)
            invoice_data contains payment_hash, payment_request, checking_id if successful
        """
        try:
            # Check if extension is available first
            taproot_available, error = await TaprootIntegration.is_taproot_available()
            if not taproot_available:
                return None, error

            try:
                from lnbits.extensions.taproot_assets.services.invoice_service import InvoiceService
                from lnbits.extensions.taproot_assets.models import TaprootInvoiceRequest
            except ImportError as e:
                error = TaprootError(
                    code="TAPROOT_IMPORT_ERROR",
                    message="Failed to import Taproot Assets modules",
                    details={"error": str(e)}
                )
                logger.error(str(error))
                return None, error
            
            # Validate inputs
            if not asset_id:
                return None, TaprootError(
                    code="INVALID_ASSET_ID",
                    message="Asset ID is required"
                )
            
            if amount <= 0:
                return None, TaprootError(
                    code="INVALID_AMOUNT",
                    message="Amount must be greater than 0",
                    details={"amount": amount}
                )
            
            # Create the invoice request
            request = TaprootInvoiceRequest(
                asset_id=asset_id,
                amount=amount,
                description=description,
                expiry=expiry,
                peer_pubkey=peer_pubkey,  # Can be None - invoice service will find it
                extra=extra
            )
            
            try:
                # Let the invoice service handle everything including peer discovery
                response = await InvoiceService.create_invoice(
                    data=request,
                    user_id=user_id,
                    wallet_id=wallet_id
                )
                
                return {
                    "payment_hash": response.payment_hash,
                    "payment_request": response.payment_request,
                    "checking_id": response.checking_id,
                    "is_rfq": True
                }, None
                
            except Exception as e:
                error = TaprootError(
                    code="RFQ_CREATION_FAILED",
                    message="Failed to create RFQ invoice",
                    details={
                        "error": str(e),
                        "asset_id": asset_id,
                        "amount": amount,
                        "wallet_id": wallet_id
                    }
                )
                logger.error(str(error))
                return None, error
            
        except Exception as e:
            error = TaprootError(
                code="UNEXPECTED_ERROR",
                message="Unexpected error during RFQ invoice creation",
                details={"error": str(e)}
            )
            logger.error(str(error))
            return None, error</file_text>
</invoke>