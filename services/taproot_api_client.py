"""Simple API client for Taproot Assets extension."""
from typing import Optional, Dict, Any
import httpx
from loguru import logger
from lnbits.settings import settings


class TaprootAPIClient:
    """Minimal API client for communicating with Taproot Assets extension."""
    
    def __init__(self, base_url: Optional[str] = None):
        """Initialize the API client."""
        self.base_url = base_url or settings.lnbits_baseurl
        if not self.base_url.startswith("http"):
            self.base_url = f"http://{self.base_url}"
    
    async def post(self, endpoint: str, data: Dict[str, Any], api_key: str) -> Optional[Dict[str, Any]]:
        """Make a POST request to taproot_assets extension."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/taproot_assets/api/v1{endpoint}",
                    json=data,
                    headers={"X-Api-Key": api_key},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"API POST failed: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"API POST error: {e}")
            return None
    
    async def get(self, endpoint: str, params: Optional[Dict] = None, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Make a GET request to taproot_assets extension."""
        try:
            headers = {"X-Api-Key": api_key} if api_key else {}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/taproot_assets/api/v1{endpoint}",
                    params=params,
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"API GET failed: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"API GET error: {e}")
            return None