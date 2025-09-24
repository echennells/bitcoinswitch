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
    amount: float = 0.0
    duration: int = 0
    pin: int = 0
    comment: bool = False
    variable: bool = False
    label: str | None = None
    # Your Taproot Assets additions
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
    title: str
    wallet: str
    currency: str
    switches: list[Switch]
    password: str | None = None
    disabled: bool = False
    disposable: bool = True

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
    id: str
    title: str
    wallet: str
    currency: str
    switches: list[Switch]
    password: str | None = None
    disabled: bool = False
    disposable: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # obsolete field, do not use anymore
    # should be deleted from the database in the future
    key: str = ""

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
    id: str
    bitcoinswitch_id: str
    payment_hash: str
    pin: int
    sats: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Your Taproot Assets additions
    is_taproot: bool = Field(
        default=False,
        description="Whether this is a Taproot Asset payment"
    )
    asset_id: Optional[str] = Field(
        default=None,
        description="Taproot Asset ID if applicable"
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

    # TODO: deprecated do not use this field anymore
    # should be deleted from the database in the future
    payload: str = ""

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