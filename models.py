import json
from datetime import datetime, timezone
from typing import Optional

from lnurl import encode as lnurl_encode
from lnurl.types import LnurlPayMetadata
from pydantic import BaseModel, Field


class Switch(BaseModel):
    amount: float = 0.0
    duration: int = 0
    pin: int = 0
    comment: bool = False
    variable: bool = False
    label: Optional[str] = None
    lnurl: Optional[str] = None
    accepts_assets: bool = False
    accepted_asset_ids: Optional[list[str]] = []

    def set_lnurl(self, url: str) -> str:
        self.lnurl = str(
            lnurl_encode(
                url
                + f"?pin={self.pin}"
                + f"&amount={self.amount}"
                + f"&duration={self.duration}"
                + f"&variable={self.variable}"
                + f"&comment={self.comment}"
                + "&disabletime=0"
            )
        )
        return self.lnurl


class CreateBitcoinswitch(BaseModel):
    title: str
    wallet: str
    currency: str
    switches: list[Switch]
    default_accepts_assets: bool = False


class Bitcoinswitch(BaseModel):
    id: str
    title: str
    wallet: str
    currency: str
    key: str
    switches: list[Switch]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    default_accepts_assets: bool = False

    @property
    def lnurlpay_metadata(self) -> LnurlPayMetadata:
        return LnurlPayMetadata(json.dumps([["text/plain", self.title]]))


class BitcoinswitchPayment(BaseModel):
    id: str
    payment_hash: str
    bitcoinswitch_id: str
    payload: str
    pin: int
    sats: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_taproot: bool = False
    asset_id: Optional[str] = None
    # Market maker fields for rate management
    quoted_rate: Optional[float] = None  # Sats per asset at quote time
    quoted_at: Optional[datetime] = None  # When the rate was quoted
    asset_amount: Optional[int] = None  # Target asset amount
    # RFQ fields for LNURL flow
    rfq_invoice_hash: Optional[str] = None  # Hash of the first RFQ invoice
    rfq_asset_amount: Optional[int] = None  # Asset amount from first RFQ
    rfq_sat_amount: Optional[float] = None  # Sat amount from first RFQ
