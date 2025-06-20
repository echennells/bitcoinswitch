"""
Rate service for managing exchange rates between assets and sats.
Implements market maker functionality for LNURL + Taproot Assets.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from loguru import logger
import os
from lnbits.core.crud import get_wallet
from .taproot_api_client import TaprootAPIClient

# Configuration from environment or defaults
RATE_TOLERANCE = float(os.getenv("BITCOINSWITCH_RATE_TOLERANCE", "0.05"))  # 5% default
RATE_VALIDITY_MINUTES = int(os.getenv("BITCOINSWITCH_RATE_VALIDITY_MINUTES", "5"))  # 5 minutes default
RATE_REFRESH_SECONDS = int(os.getenv("BITCOINSWITCH_RATE_REFRESH_SECONDS", "60"))  # 1 minute default

# Simple in-memory cache for rates
rate_cache: Dict[str, Dict[str, Any]] = {}


class RateService:
    """Service for managing asset exchange rates."""
    
    _client = TaprootAPIClient()
    
    @staticmethod
    async def get_current_rate(asset_id: str, wallet_id: str, user_id: str, asset_amount: int = 1) -> Optional[float]:
        """
        Get current exchange rate for an asset via API.
        Returns sats per asset unit.
        """
        # Check cache first
        cache_key = f"{asset_id}:{asset_amount}"
        cached = rate_cache.get(cache_key)
        if cached:
            cached_at = cached.get("timestamp")
            if cached_at and datetime.now(timezone.utc) - cached_at < timedelta(seconds=RATE_REFRESH_SECONDS):
                logger.debug(f"Using cached rate for {asset_id[:8]}...: {cached['rate']} sats/unit")
                return cached.get("rate")
        
        try:
            # Get wallet for API key
            wallet = await get_wallet(wallet_id)
            if not wallet:
                logger.error("Wallet not found")
                return None
            
            # Call API to get rate
            result = await RateService._client.get(
                f"/taproot/rates/{asset_id}",
                params={"amount": asset_amount},
                api_key=wallet.adminkey
            )
            
            if result and result.get("rate_per_unit"):
                rate = result["rate_per_unit"]
                
                # Cache the rate
                rate_cache[cache_key] = {
                    "rate": rate,
                    "timestamp": datetime.now(timezone.utc)
                }
                
                logger.debug(f"Got rate from API: {rate} sats/unit for {asset_amount} units of {asset_id[:8]}...")
                return rate
            else:
                logger.warning(f"No rate returned from API for asset {asset_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to fetch rate for asset {asset_id}: {e}")
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
        
        logger.debug(
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
        
        logger.debug(
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
        logger.debug(f"Calculated: {asset_amount} assets × {rate} sats/asset = {sat_amount} sats")
        
        return sat_amount