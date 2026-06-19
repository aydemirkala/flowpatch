"""
Migration: Enforce dynamic discovery for secondary backends

Secondary backends in federation mode MUST use dynamic discovery to fetch
the managed product list from the primary backend. This is not optional.
"""

import sys
import os

# Add parent directory to path to import db and models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db import engine
from logging_utils import log_event


def migrate():
    """Enable dynamic discovery for all secondary backends."""
    with engine.connect() as conn:
        # Update all secondary backends to use dynamic discovery
        result = conn.execute(text("""
            UPDATE backend_endpoints 
            SET use_dynamic_discovery = TRUE 
            WHERE backend_type = 'secondary'
            RETURNING name, platform
        """))
        
        updated = result.fetchall()
        conn.commit()
        
        if updated:
            for row in updated:
                log_event("migration.enforce_dynamic_discovery", 
                         backend=row[0], platform=row[1])
                print(f"✓ Enabled dynamic discovery for: {row[0]} ({row[1]})")
        else:
            print("✓ No secondary backends found or already configured")
        
        log_event("migration.enforce_dynamic_discovery.success", count=len(updated))


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        log_event("migration.enforce_dynamic_discovery.error", error=str(e))
        print(f"✗ Migration failed: {e}")
        sys.exit(1)

