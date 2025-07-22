"""
Rate service for managing exchange rates between assets and sats.
Implements market maker functionality for LNURL + Taproot Assets.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from loguru import logger
import os

# Configuration from environment or defaults
RATE_TOLERANCE = float(os.getenv("BITCOINSWITCH_RATE_TOLERANCE", "0.05"))  # 5% default
RATE_VALIDITY_MINUTES = int(os.getenv("BITCOINSWITCH_RATE_VALIDITY_MINUTES", "5"))  # 5 minutes default
RATE_REFRESH_SECONDS = int(os.getenv("BITCOINSWITCH_RATE_REFRESH_SECONDS", "60"))  # 1 minute default



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
        
        try:
            # Call the taproot assets API to get rate
            import httpx
            from lnbits.core.crud import get_wallet
            from lnbits.settings import settings
            
            # Get wallet for API key
            wallet = await get_wallet(wallet_id)
            if not wallet:
                logger.error("Wallet not found")
                return None
            
            # Build API URL
            base_url = settings.lnbits_baseurl
            if not base_url.startswith("http"):
                base_url = f"http://{base_url}"
            
            url = f"{base_url}/taproot_assets/api/v1/taproot/rate/{asset_id}"
            
            # Make API request
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params={"amount": asset_amount},
                    headers={"X-Api-Key": wallet.adminkey},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get("rate_per_unit"):
                        rate = data["rate_per_unit"]
                        logger.debug(f"Got rate from API: {rate} sats/unit for {asset_amount} units of {asset_id[:8]}...")
                        return rate
                    else:
                        logger.warning(f"No rate returned from API: {data.get('error', 'Unknown error')}")
                        return None
                else:
                    logger.error(f"API request failed with status {response.status_code}")
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