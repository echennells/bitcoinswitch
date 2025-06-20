"""
Rate service for managing exchange rates between assets and sats.
Implements market maker functionality for LNURL + Taproot Assets.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from loguru import logger
import os

# Configuration from environment or defaults
RATE_TOLERANCE = float(os.getenv("BITCOINSWITCH_RATE_TOLERANCE", "0.05"))  # 5% default
RATE_VALIDITY_MINUTES = int(os.getenv("BITCOINSWITCH_RATE_VALIDITY_MINUTES", "5"))  # 5 minutes default
RATE_REFRESH_SECONDS = int(os.getenv("BITCOINSWITCH_RATE_REFRESH_SECONDS", "60"))  # 1 minute default

# Simple in-memory cache for rates
# In production, this could be Redis or database
rate_cache: Dict[str, Dict[str, Any]] = {}


class RateService:
    """Service for managing asset exchange rates."""
    
    @staticmethod
    async def get_current_rate(asset_id: str, wallet_id: str, user_id: str, asset_amount: int = 1) -> Optional[float]:
        """
        Get current exchange rate for an asset by creating a test RFQ quote.
        Returns sats per asset unit.
        
        This creates a minimal RFQ buy order to discover the current rate
        without actually creating an invoice.
        """
        # Check cache first
        cached = rate_cache.get(asset_id)
        if cached:
            cached_at = cached.get("timestamp")
            if cached_at and datetime.now(timezone.utc) - cached_at < timedelta(seconds=RATE_REFRESH_SECONDS):
                logger.info(f"Using cached rate for {asset_id[:8]}...: {cached['rate']} sats/unit")
                return cached.get("rate")
        
        try:
            # Import taproot assets services to get RFQ rate
            from ...taproot_assets.tapd.taproot_factory import TaprootAssetsFactory
            from ...taproot_assets.tapd.taproot_invoices import TaprootInvoiceManager
            from ....wallets.tapd_grpc_files.rfqrpc import rfq_pb2, rfq_pb2_grpc
            
            # Create wallet instance
            taproot_wallet = await TaprootAssetsFactory.create_wallet(
                user_id=user_id,
                wallet_id=wallet_id
            )
            
            # Get RFQ stub from the node
            node = taproot_wallet.node
            rfq_stub = rfq_pb2_grpc.RfqStub(node.channel)
            
            # First, find the peer with an asset channel
            # Import asset service to find peer
            from ...taproot_assets.services.asset_service import AssetService
            from lnbits.core.models import WalletTypeInfo, Wallet
            from lnbits.core.models.wallets import KeyType
            
            # Create wallet info for asset lookup
            wallet_obj = Wallet(id=wallet_id, user=user_id, adminkey="", inkey="", balance_msat=0, name="")
            wallet_info = WalletTypeInfo(key_type=KeyType.admin, wallet=wallet_obj)
            
            # Get user's assets to find peer
            assets = await AssetService.list_assets(wallet_info)
            peer_pubkey = None
            
            for asset in assets:
                if asset.get("asset_id") == asset_id and asset.get("channel_info") and asset["channel_info"].get("peer_pubkey"):
                    peer_pubkey = asset["channel_info"]["peer_pubkey"]
                    logger.info(f"Found peer for rate quote: {peer_pubkey[:16]}...")
                    break
            
            if not peer_pubkey:
                logger.warning(f"No peer found with channel for asset {asset_id}")
                return None
            
            # Create a minimal buy order request to get rate quote
            # Using 1 unit to get per-unit rate
            asset_id_bytes = bytes.fromhex(asset_id)
            
            # Request a quote for the actual amount we'll be invoicing
            # The RFQ rate can vary based on order size
            buy_order_request = rfq_pb2.AddAssetBuyOrderRequest(
                asset_specifier=rfq_pb2.AssetSpecifier(asset_id=asset_id_bytes),
                asset_max_amt=asset_amount,  # Use the actual amount passed in
                expiry=int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp()),
                timeout_seconds=5,
                peer_pub_key=bytes.fromhex(peer_pubkey)  # Add the peer
            )
            
            # Get quote
            logger.info(f"Fetching RFQ rate for asset {asset_id[:8]}...")
            buy_order_response = await rfq_stub.AddAssetBuyOrder(buy_order_request, timeout=5)
            
            if buy_order_response.accepted_quote:
                # Extract rate from the accepted quote
                # The rate is expressed as coefficient * 10^(-scale)
                rate_info = buy_order_response.accepted_quote.ask_asset_rate
                
                # Calculate total millisats for the order: coefficient / 10^scale
                total_millisats = float(rate_info.coefficient) / (10 ** rate_info.scale)
                
                # The rate is for the total order amount we requested
                # Divide by the asset amount to get per-unit rate
                rate_millisats_per_unit = total_millisats / asset_amount
                
                # Convert from millisats to sats per unit
                rate = rate_millisats_per_unit / 1000
                
                logger.info(f"RFQ rate for {asset_amount} units of {asset_id[:8]}...: total={total_millisats} millisats, per-unit={rate} sats/unit (coefficient={rate_info.coefficient}, scale={rate_info.scale})")
                
                # Cache the rate
                rate_cache[asset_id] = {
                    "rate": rate,
                    "timestamp": datetime.now(timezone.utc),
                    "quote_id": buy_order_response.accepted_quote.id.hex()
                }
                
                return rate
            else:
                logger.warning(f"No RFQ quote received for asset {asset_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to fetch RFQ rate for asset {asset_id}: {e}")
            return None
    
    @staticmethod
    def is_rate_within_tolerance(
        quoted_rate: float,
        current_rate: float,
        tolerance: float = RATE_TOLERANCE
    ) -> bool:
        """
        Check if current rate is within acceptable tolerance of quoted rate.
        
        Args:
            quoted_rate: Rate at quote time (sats per asset)
            current_rate: Current rate (sats per asset)
            tolerance: Acceptable deviation (e.g., 0.05 = 5%)
            
        Returns:
            True if within tolerance, False otherwise
        """
        if quoted_rate <= 0:
            return False
        
        deviation = abs(current_rate - quoted_rate) / quoted_rate
        within_tolerance = deviation <= tolerance
        
        logger.info(
            f"Rate check: quoted={quoted_rate}, current={current_rate}, "
            f"deviation={deviation:.2%}, tolerance={tolerance:.2%}, "
            f"within_tolerance={within_tolerance}"
        )
        
        return within_tolerance
    
    @staticmethod
    def is_rate_expired(quoted_at: datetime) -> bool:
        """
        Check if a rate quote has expired.
        
        Args:
            quoted_at: When the rate was quoted
            
        Returns:
            True if expired, False if still valid
        """
        if not quoted_at:
            return True
        
        # Ensure quoted_at is timezone-aware
        if quoted_at.tzinfo is None:
            quoted_at = quoted_at.replace(tzinfo=timezone.utc)
        
        age = datetime.now(timezone.utc) - quoted_at
        expired = age > timedelta(minutes=RATE_VALIDITY_MINUTES)
        
        logger.info(
            f"Rate age check: quoted_at={quoted_at}, age={age}, "
            f"validity={RATE_VALIDITY_MINUTES}min, expired={expired}"
        )
        
        return expired
    
    @staticmethod
    async def calculate_sat_amount(
        asset_id: str,
        asset_amount: int,
        wallet_id: str,
        user_id: str
    ) -> Optional[int]:
        """
        Calculate satoshi amount for given asset amount.
        
        Args:
            asset_id: The asset ID
            asset_amount: Amount of assets
            wallet_id: Wallet ID for RFQ access
            user_id: User ID for RFQ access
            
        Returns:
            Satoshi amount, or None if rate not available
        """
        rate = await RateService.get_current_rate(asset_id, wallet_id, user_id, asset_amount)
        if not rate:
            return None
        
        sat_amount = int(asset_amount * rate)
        logger.info(f"Calculated: {asset_amount} assets × {rate} sats/asset = {sat_amount} sats")
        
        return sat_amount