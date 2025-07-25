"""BitcoinSwitch configuration."""
from typing import Dict, Any
import os
from pydantic import BaseModel, Field


class BitcoinSwitchConfig(BaseModel):
    """Configuration settings for BitcoinSwitch extension."""
    
    # Rate management
    rate_tolerance: float = Field(
        default=float(os.getenv("BITCOINSWITCH_RATE_TOLERANCE", "0.05")),
        description="Allowed deviation in exchange rates (e.g., 0.05 = 5%)"
    )
    rate_validity_minutes: int = Field(
        default=int(os.getenv("BITCOINSWITCH_RATE_VALIDITY_MINUTES", "5")),
        description="How long a rate quote remains valid"
    )
    rate_refresh_seconds: int = Field(
        default=int(os.getenv("BITCOINSWITCH_RATE_REFRESH_SECONDS", "60")),
        description="How often to refresh rates"
    )
    
    # HTTP timeouts
    http_timeout: float = Field(
        default=float(os.getenv("BITCOINSWITCH_HTTP_TIMEOUT", "10.0")),
        description="HTTP request timeout in seconds"
    )

    # Taproot settings
    taproot_invoice_expiry: int = Field(
        default=int(os.getenv("BITCOINSWITCH_TAPROOT_INVOICE_EXPIRY", "3600")),
        description="Taproot invoice expiry in seconds"
    )
    
    # Comment settings
    max_comment_length: int = Field(
        default=int(os.getenv("BITCOINSWITCH_MAX_COMMENT_LENGTH", "1500")),
        description="Maximum length for payment comments"
    )

# Global config instance
config = BitcoinSwitchConfig()