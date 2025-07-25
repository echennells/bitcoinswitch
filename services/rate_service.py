"""
Rate service for managing exchange rates between assets and sats.
Implements market maker functionality for LNURL + Taproot Assets.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from loguru import logger

from .config import config


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
                logger.error(f"Wallet {wallet_id} not found")
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
                    timeout=config.http_timeout
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
        tolerance: float = None
    ) -> bool:
        """Check if current rate is within acceptable tolerance of quoted rate."""
        if quoted_rate <= 0:
            return False
        
        tolerance = tolerance or config.rate_tolerance
        deviation = abs(current_rate - quoted_rate) / quoted_rate
        within_tolerance = deviation <= tolerance
        
        logger.debug(
            f"Rate check: quoted={quoted_rate:.8f}, current={current_rate:.8f}, "
            f"deviation={deviation:.2%}, tolerance={tolerance:.2%}, "
            f"within_tolerance={within_tolerance}"
        )
        
        return within_tolerance
    
    @staticmethod
    def is_rate_expired(quoted_at: datetime) -> bool:
        """Check if a rate quote has expired."""
        if not quoted_at:
            return True
        
        # Ensure quoted_at is timezone-aware
        if quoted_at.tzinfo is None:
            quoted_at = quoted_at.replace(tzinfo=timezone.utc)
        
        age = datetime.now(timezone.utc) - quoted_at
        expired = age > timedelta(minutes=config.rate_validity_minutes)
        
        logger.debug(
            f"Rate age check: quoted_at={quoted_at}, age={age}, "
            f"validity={config.rate_validity_minutes}min, expired={expired}"
        )
        
        return expired