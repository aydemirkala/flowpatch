"""
Migration: Add health check and rollback fields to image_update_results table.

Adds fields for:
- Pre/post patch state capture
- Health check results
- Auto-rollback tracking  
- Detailed execution logs

Run with: python -m app.migrations.add_health_check_fields
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal, engine
from ..logging_utils import log_event


def migrate(db: Session) -> None:
    """Add health check fields to image_update_results if they don't exist."""
    
    # Check which columns already exist
    columns_to_add = [
        ("pre_patch_state", "TEXT"),
        ("post_patch_state", "TEXT"),
        ("health_check_passed", "BOOLEAN"),
        ("health_check_message", "TEXT"),
        ("health_checked_at", "TIMESTAMP WITH TIME ZONE"),
        ("was_rolled_back", "BOOLEAN DEFAULT FALSE"),
        ("rollback_reason", "TEXT"),
        ("execution_log", "TEXT"),
    ]
    
    # Also add source_image to image_update_jobs if it doesn't exist
    job_columns_to_add = [
        ("source_image", "VARCHAR(512)"),
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            # Check if column exists
            db.execute(text(f"SELECT {col_name} FROM image_update_results LIMIT 1"))
            log_event("migration.health_check.skip_column", column=col_name, reason="Already exists")
        except Exception:
            # Column doesn't exist, add it
            try:
                db.execute(text(f"ALTER TABLE image_update_results ADD COLUMN {col_name} {col_type}"))
                log_event("migration.health_check.add_column", column=col_name)
            except Exception as e:
                log_event("migration.health_check.add_column_error", column=col_name, error=str(e))
    
    for col_name, col_type in job_columns_to_add:
        try:
            db.execute(text(f"SELECT {col_name} FROM image_update_jobs LIMIT 1"))
            log_event("migration.health_check.skip_column", table="jobs", column=col_name, reason="Already exists")
        except Exception:
            try:
                db.execute(text(f"ALTER TABLE image_update_jobs ADD COLUMN {col_name} {col_type}"))
                log_event("migration.health_check.add_column", table="jobs", column=col_name)
            except Exception as e:
                log_event("migration.health_check.add_column_error", table="jobs", column=col_name, error=str(e))
    
    db.commit()
    log_event("migration.health_check.complete")


def run() -> None:
    """Run the migration."""
    db = SessionLocal()
    try:
        migrate(db)
        print("Migration complete: Health check fields added to image_update_results.")
    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()

