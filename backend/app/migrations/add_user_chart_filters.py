"""
Migration: Add filters column to user_charts table for multiple filter support
"""
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def migrate():
    """Add filters column to user_charts"""
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        print("No DATABASE_URL found, skipping migration")
        return
    
    conn = psycopg2.connect(db_url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    
    try:
        # Check if column exists
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='user_charts' AND column_name='filters'
        """)
        
        if cur.fetchone():
            print("✅ filters column already exists")
        else:
            print("Adding filters column to user_charts...")
            cur.execute("""
                ALTER TABLE user_charts 
                ADD COLUMN IF NOT EXISTS filters TEXT NULL
            """)
            print("✅ filters column added successfully")
        
        conn.commit()
        
    except Exception as e:
        print(f"❌ Migration error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    migrate()

