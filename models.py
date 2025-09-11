"""
Pydantic models for Bitcoin Switch with Taproot Assets support.

These models define the data structures for switches, payments, and
configuration, including support for both standard Lightning payments
and Taproot Asset payments.
"""
from datetime import datetime, timezone
from typing import Optional, List

from lnurl import encode as lnurl_encode
from lnurl.types import LnurlPayMetadata
from pydantic import BaseModel, Field, validator


class Switch(BaseModel):
    """
    Individual switch configuration within a Bitcoin Switch device.
    
    Supports both standard Lightning payments and Taproot Asset payments.
    """
    amount: float = Field(default=0.0, description="Payment amount in currency units")
    duration: int = Field(default=0, description="Switch activation duration in milliseconds")
    pin: int = Field(default=0, description="GPIO pin number to control")
    comment: bool = Field(default=False, description="Whether to allow payment comments")
    variable: bool = Field(default=False, description="Whether to allow variable time based on payment")
    label: Optional[str] = Field(default=None, description="Optional display label for the switch")
    lnurl: Optional[str] = Field(default=None, description="Generated LNURL for payments")
    accepts_assets: bool = Field(default=False, description="Whether this switch accepts Taproot Assets")
    accepted_asset_ids: List[str] = Field(
        default_factory=list,
        description="List of Taproot Asset IDs accepted by this switch"
    )

    @validator('amount')
    def amount_must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Amount must be positive')
        return v

    @validator('duration')
    def duration_must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Duration must be positive')
        return v

    @validator('pin')
    def pin_must_be_valid(cls, v):
        if v < 0:
            raise ValueError('PIN must be positive')
        return v

    def set_lnurl(self, url: str) -> str:
        """Generate and set the LNURL for this switch."""
        params = [
            f"pin={self.pin}",
            f"amount={self.amount}",
            f"duration={self.duration}",
            f"variable={self.variable}",
            f"comment={self.comment}",
            "disabletime=0"
        ]
        self.lnurl = str(lnurl_encode(url + "?" + "&".join(params)))
        return self.lnurl


class CreateBitcoinswitch(BaseModel):
    """Parameters for creating a new Bitcoin Switch device."""
    title: str = Field(..., description="Display name for the switch device")
    wallet: str = Field(..., description="LNbits wallet ID for payments")
    currency: str = Field(..., description="Currency for payment amounts (e.g., 'sat', 'USD')")
    switches: List[Switch] = Field(..., description="List of switch configurations")

    @validator('switches')
    def must_have_switches(cls, v):
        if not v:
            raise ValueError('At least one switch must be configured')
        return v


class Bitcoinswitch(BaseModel):
    """
    Bitcoin Switch device configuration.
    
    Represents a complete switch device with one or more individual switches,
    supporting both Lightning Network and Taproot Asset payments.
    """
    id: str = Field(..., description="Unique identifier for this device")
    title: str = Field(..., description="Display name for the device")
    wallet: str = Field(..., description="LNbits wallet ID for payments")
    currency: str = Field(..., description="Currency for payment amounts")
    key: str = Field(..., description="Access key for device control")
    switches: List[Switch] = Field(..., description="List of configured switches")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of device creation"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of last update"
    )

    @property
    def lnurlpay_metadata(self) -> LnurlPayMetadata:
        """Generate LNURL metadata for payments."""
        from json import dumps
        return LnurlPayMetadata(dumps([["text/plain", self.title]]))


class BitcoinswitchPayment(BaseModel):
    """
    Payment record for a switch activation.
    
    Supports both standard Lightning payments and Taproot Asset payments,
    including RFQ (Request for Quote) data for asset payments.
    """
    # Basic payment fields
    id: str = Field(..., description="Unique payment identifier")
    payment_hash: str = Field(..., description="Lightning payment hash")
    bitcoinswitch_id: str = Field(..., description="ID of the switch being paid")
    payload: str = Field(..., description="Payment metadata")
    pin: int = Field(..., description="GPIO pin being controlled")
    sats: int = Field(..., description="Payment amount in satoshis")
    
    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Payment creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp"
    )
    
    # Taproot Assets fields
    is_taproot: bool = Field(
        default=False,
        description="Whether this is a Taproot Asset payment"
    )
    asset_id: Optional[str] = Field(
        default=None,
        description="Taproot Asset ID if applicable"
    )
    is_direct_asset: Optional[bool] = Field(
        default=False, 
        description="True if this is a direct asset payment (not RFQ)"
    )
    
    # Market maker fields
    quoted_rate: Optional[float] = Field(
        default=None,
        description="Exchange rate at quote time (sats per asset)"
    )
    quoted_at: Optional[datetime] = Field(
        default=None,
        description="When the rate was quoted"
    )
    asset_amount: Optional[int] = Field(
        default=None,
        description="Requested asset amount"
    )
    
    # RFQ fields
    rfq_invoice_hash: Optional[str] = Field(
        default=None,
        description="Hash of the initial RFQ invoice"
    )
    rfq_asset_amount: Optional[int] = Field(
        default=None,
        description="Asset amount from RFQ"
    )
    rfq_sat_amount: Optional[float] = Field(
        default=None,
        description="Sat amount from RFQ"
    )

    @validator('sats')
    def sats_must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Satoshi amount must be positive')
        return v

    @validator('asset_amount')
    def asset_amount_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError('Asset amount must be positive')
        return v