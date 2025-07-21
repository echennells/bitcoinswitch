from lnbits.db import Database

db = Database("ext_bitcoinswitch")


async def m001_initial(db):
    """
    Initial bitcoinswitch table.
    """
    await db.execute(
        f"""
        CREATE TABLE bitcoinswitch.switch (
            id TEXT NOT NULL PRIMARY KEY,
            key TEXT NOT NULL,
            title TEXT NOT NULL,
            wallet TEXT NOT NULL,
            currency TEXT NOT NULL,
            switches TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
    """
    )
    await db.execute(
        f"""
        CREATE TABLE bitcoinswitch.payment (
            id TEXT NOT NULL PRIMARY KEY,
            bitcoinswitch_id TEXT NOT NULL,
            payment_hash TEXT,
            payload TEXT NOT NULL,
            pin INT,
            sats {db.big_int},
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
    """
    )


async def m002_add_password(db):
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.switch
        ADD COLUMN password TEXT;
        """
    )


async def m003_add_taproot_support(db):
    """Add Taproot Assets support columns."""
    
    # Add default_accepts_assets to switch table
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.switch 
        ADD COLUMN default_accepts_assets BOOLEAN DEFAULT FALSE;
        """
    )
    
    # Add columns to payment table
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN is_taproot BOOLEAN DEFAULT FALSE;
        """
    )
    
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN asset_id TEXT;
        """
    )


async def m004_add_market_maker_fields(db):
    """Add market maker fields for rate management."""
    
    # Add quoted_rate column
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN quoted_rate REAL;
        """
    )
    
    # Add quoted_at column
    await db.execute(
        f"""
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN quoted_at TIMESTAMP;
        """
    )
    
    # Add asset_amount column
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN asset_amount INTEGER;
        """
    )


async def m005_add_rfq_fields(db):
    """Add RFQ fields for LNURL flow."""
    
    # Add rfq_invoice_hash column
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN rfq_invoice_hash TEXT;
        """
    )
    
    # Add rfq_asset_amount column
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN rfq_asset_amount INTEGER;
        """
    )
    
    # Add rfq_sat_amount column
    await db.execute(
        """
        ALTER TABLE bitcoinswitch.payment 
        ADD COLUMN rfq_sat_amount REAL;
        """
    )
