"""
Migration: Add image_update_jobs and image_update_results tables.

Adds tables for the Image Update feature that allows patching Kubernetes
resources with new container images.

Run with: python -m app.migrations.add_image_update_tables
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal, engine
from ..logging_utils import log_event


def migrate(db: Session) -> None:
    """Create image update tables if they don't exist."""
    
    # Check if image_update_jobs table exists
    jobs_exists = False
    results_exists = False
    
    try:
        db.execute(text("SELECT 1 FROM image_update_jobs LIMIT 1"))
        jobs_exists = True
    except Exception:
        pass
    
    try:
        db.execute(text("SELECT 1 FROM image_update_results LIMIT 1"))
        results_exists = True
    except Exception:
        pass
    
    if jobs_exists and results_exists:
        log_event("migration.image_update.skip", reason="Tables already exist")
        return
    
    # Create image_update_jobs table
    if not jobs_exists:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS image_update_jobs (
                id SERIAL PRIMARY KEY,
                product_name VARCHAR(256) NOT NULL,
                target_image VARCHAR(512) NOT NULL,
                image_source VARCHAR(32) NOT NULL DEFAULT 'custom',
                target_platforms TEXT,
                target_namespaces TEXT,
                target_resources TEXT,
                scheduled_at TIMESTAMP WITH TIME ZONE,
                status VARCHAR(32) NOT NULL DEFAULT 'pending_approval',
                approval_required BOOLEAN NOT NULL DEFAULT TRUE,
                approved_by VARCHAR(128),
                approved_at TIMESTAMP WITH TIME ZONE,
                trigger_token VARCHAR(128) UNIQUE,
                pipeline_callback_url VARCHAR(512),
                started_at TIMESTAMP WITH TIME ZONE,
                finished_at TIMESTAMP WITH TIME ZONE,
                created_by VARCHAR(128) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """))
        
        # Create indexes
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_product_name ON image_update_jobs (product_name)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_status ON image_update_jobs (status)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_trigger_token ON image_update_jobs (trigger_token)"))
        
        log_event("migration.image_update.jobs_created")
    
    # Create image_update_results table
    if not results_exists:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS image_update_results (
                id SERIAL PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES image_update_jobs(id) ON DELETE CASCADE,
                platform VARCHAR(64) NOT NULL,
                namespace VARCHAR(128) NOT NULL,
                resource_name VARCHAR(256) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                container_name VARCHAR(128),
                old_image VARCHAR(512),
                new_image VARCHAR(512) NOT NULL,
                status VARCHAR(32) NOT NULL,
                error_message TEXT,
                executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Create index
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_results_job_id ON image_update_results (job_id)"))
        
        log_event("migration.image_update.results_created")
    
    db.commit()
    log_event("migration.image_update.complete")


def run() -> None:
    """Run the migration."""
    db = SessionLocal()
    try:
        migrate(db)
        print("Migration complete: image_update_jobs and image_update_results tables created.")
    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()

