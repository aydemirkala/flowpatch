#!/usr/bin/env python3
"""
Cleanup script to remove redundant consecutive duplicate history entries.

This script:
1. Finds all resources with history
2. For each resource, identifies consecutive duplicate entries (same version)
3. Keeps only the FIRST occurrence of each consecutive version group
4. Removes all subsequent duplicates in that group

Example:
Before:
  - v1.0.0 | 2025-10-15 12:00  ← KEEP
  - v1.0.0 | 2025-10-15 14:00  ← DELETE
  - v1.0.0 | 2025-10-15 16:00  ← DELETE
  - v1.2.0 | 2025-10-16 10:00  ← KEEP (version changed)
  - v1.2.0 | 2025-10-16 12:00  ← DELETE
  - v1.0.0 | 2025-10-17 08:00  ← KEEP (downgrade/rollback)
  - v1.0.0 | 2025-10-17 10:00  ← DELETE

After:
  - v1.0.0 | 2025-10-15 12:00
  - v1.2.0 | 2025-10-16 10:00
  - v1.0.0 | 2025-10-17 08:00
"""

import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import settings


def cleanup_duplicate_history(dry_run: bool = False):
    """
    Remove consecutive duplicate history entries.
    
    Args:
        dry_run: If True, only print what would be deleted without actually deleting
    """
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    try:
        print(f"{'=' * 80}")
        print(f"Resource History Cleanup - {'DRY RUN' if dry_run else 'LIVE MODE'}")
        print(f"{'=' * 80}")
        print(f"Started at: {datetime.now().isoformat()}\n")
        
        # Get all resources with history
        resources_query = text("""
            SELECT DISTINCT r.id, r.resource_name, r.namespace, r.platform
            FROM resources r
            INNER JOIN resource_history rh ON rh.resource_id = r.id
            ORDER BY r.platform, r.namespace, r.resource_name
        """)
        
        resources = db.execute(resources_query).fetchall()
        print(f"Found {len(resources)} resources with history\n")
        
        total_deleted = 0
        resources_cleaned = 0
        
        for resource in resources:
            resource_id, resource_name, namespace, platform = resource
            
            # Get all history entries for this resource, ordered by time
            history_query = text("""
                SELECT id, version, checked_at
                FROM resource_history
                WHERE resource_id = :resource_id
                ORDER BY checked_at ASC
            """)
            
            history = db.execute(history_query, {"resource_id": resource_id}).fetchall()
            
            if len(history) <= 1:
                continue  # No duplicates possible
            
            # Find IDs to delete (consecutive duplicates)
            ids_to_delete = []
            previous_version = None
            
            for hist_id, version, checked_at in history:
                if version == previous_version:
                    # This is a consecutive duplicate - mark for deletion
                    ids_to_delete.append(hist_id)
                else:
                    # Version changed (or first entry) - keep it
                    previous_version = version
            
            if ids_to_delete:
                resources_cleaned += 1
                total_deleted += len(ids_to_delete)
                
                print(f"📦 {platform}/{namespace}/{resource_name}")
                print(f"   Total history: {len(history)} entries")
                print(f"   Duplicates: {len(ids_to_delete)} entries")
                print(f"   Remaining: {len(history) - len(ids_to_delete)} entries")
                
                if dry_run:
                    print(f"   [DRY RUN] Would delete IDs: {ids_to_delete[:5]}{'...' if len(ids_to_delete) > 5 else ''}")
                else:
                    # Delete the duplicates
                    delete_query = text("""
                        DELETE FROM resource_history
                        WHERE id = ANY(:ids)
                    """)
                    db.execute(delete_query, {"ids": ids_to_delete})
                    db.commit()
                    print(f"   ✅ Deleted {len(ids_to_delete)} duplicate entries")
                
                print()
        
        print(f"{'=' * 80}")
        print(f"Cleanup Summary:")
        print(f"  - Resources processed: {len(resources)}")
        print(f"  - Resources cleaned: {resources_cleaned}")
        print(f"  - Duplicate entries {'would be ' if dry_run else ''}removed: {total_deleted}")
        print(f"{'=' * 80}")
        print(f"Finished at: {datetime.now().isoformat()}")
        
        if dry_run:
            print("\n⚠️  This was a DRY RUN - no data was deleted.")
            print("   Run with --execute to actually delete duplicates.")
        else:
            print("\n✅ Cleanup completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Cleanup duplicate resource history entries")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete duplicates (default is dry-run mode)"
    )
    
    args = parser.parse_args()
    
    cleanup_duplicate_history(dry_run=not args.execute)

