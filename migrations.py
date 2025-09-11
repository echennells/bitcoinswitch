"""
Database migrations for Bitcoin Switch with Taproot Assets support.
"""
from lnbits.db import Database

db = Database("ext_bitcoinswitch")


async def m001_initial(db):
    """
    Initial migration creating all necessary tables with Taproot Assets support.
    """
    # Create switch table with all required fields
    await db.execute(
        f"""
        CREATE TABLE bitcoinswitch.switch (
            id TEXT NOT NULL PRIMARY KEY,
            key TEXT NOT NULL,
            title TEXT NOT NULL,
            wallet TEXT NOT NULL,
            currency TEXT NOT NULL,
            switches TEXT NOT NULL,
            password TEXT,
            default_accepts_assets BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """
    )

    # Create payment table with all required fields including Taproot support
    await db.execute(
        f"""
        CREATE TABLE bitcoinswitch.payment (
            id TEXT NOT NULL PRIMARY KEY,
            bitcoinswitch_id TEXT NOT NULL,
            payment_hash TEXT,
            payload TEXT NOT NULL,
            pin INT,
            sats {db.big_int},
            -- Taproot Assets fields
            is_taproot BOOLEAN DEFAULT FALSE,
            asset_id TEXT,
            quoted_rate REAL,
            quoted_at TIMESTAMP,
            asset_amount INTEGER,
            -- RFQ fields
            rfq_invoice_hash TEXT,
            rfq_asset_amount INTEGER,
            rfq_sat_amount REAL,
            -- Timestamps
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """
    )


async def m002_add_direct_asset_field(db):
    """Add is_direct_asset field to bitcoinswitch_payments table."""
    await db.execute(
        "ALTER TABLE bitcoinswitch.payment ADD COLUMN is_direct_asset BOOLEAN DEFAULT FALSE"
    )