"""
Migration: Add replicas column to resources table

This migration adds a replicas column to track the current replica count
for each resource (deployment, statefulset, daemonset).
"""

import sys
import os

# Add parent directory to path to import db and models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db import engine
from logging_utils import log_event


def migrate():
    """Add replicas column to resources table if it doesn't exist."""
    with engine.connect() as conn:
        # Check if column exists
        result = conn.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='resources' AND column_name='replicas'
        """))
        
        if result.fetchone():
            log_event("migration.replicas.already_exists")
            print("✓ Column 'replicas' already exists in resources table")
            return
        
        # Add the column
        conn.execute(text("""
            ALTER TABLE resources 
            ADD COLUMN replicas INTEGER NULL
        """))
        conn.commit()
        
        log_event("migration.replicas.success")
        print("✓ Successfully added 'replicas' column to resources table")


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        log_event("migration.replicas.error", error=str(e))
        print(f"✗ Migration failed: {e}")
        sys.exit(1)

