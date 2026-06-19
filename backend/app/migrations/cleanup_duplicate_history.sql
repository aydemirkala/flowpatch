-- ============================================================================
-- Cleanup Duplicate Resource History Entries
-- ============================================================================
-- 
-- This script removes consecutive duplicate history entries for each resource.
-- It keeps only the FIRST occurrence when the version changes.
--
-- Example:
--   Before: v1.0 | 10/15 12:00 PM
--           v1.0 | 10/15 02:00 PM  ← DELETE (duplicate)
--           v1.0 | 10/15 05:00 PM  ← DELETE (duplicate)
--           v1.2 | 10/16 10:00 AM
--           v1.2 | 10/16 12:00 PM  ← DELETE (duplicate)
--
--   After:  v1.0 | 10/15 12:00 PM
--           v1.2 | 10/16 10:00 AM
-- ============================================================================

BEGIN;

-- Create a temporary table to identify which history entries to keep
CREATE TEMP TABLE history_to_keep AS
WITH ranked_history AS (
    SELECT 
        id,
        resource_id,
        version,
        checked_at,
        -- Compare current version with previous version
        LAG(version) OVER (PARTITION BY resource_id ORDER BY checked_at) as prev_version,
        -- Assign row number for ordering
        ROW_NUMBER() OVER (PARTITION BY resource_id ORDER BY checked_at) as rn
    FROM resource_history
)
SELECT id
FROM ranked_history
WHERE 
    -- Keep first entry for each resource
    rn = 1
    OR
    -- Keep entries where version changed from previous
    (version IS DISTINCT FROM prev_version);

-- Show statistics BEFORE deletion
SELECT 
    'Before Cleanup' as status,
    COUNT(*) as total_history_entries,
    COUNT(DISTINCT resource_id) as resources_with_history,
    (SELECT COUNT(*) FROM history_to_keep) as entries_to_keep,
    COUNT(*) - (SELECT COUNT(*) FROM history_to_keep) as entries_to_delete
FROM resource_history;

-- Perform the deletion
DELETE FROM resource_history
WHERE id NOT IN (SELECT id FROM history_to_keep);

-- Show statistics AFTER deletion
SELECT 
    'After Cleanup' as status,
    COUNT(*) as total_history_entries,
    COUNT(DISTINCT resource_id) as resources_with_history
FROM resource_history;

-- Show detailed breakdown by resource (top 20 resources)
SELECT 
    r.platform,
    r.namespace,
    r.resource_name,
    COUNT(rh.id) as remaining_history_entries,
    MIN(rh.checked_at) as first_check,
    MAX(rh.checked_at) as last_check
FROM resources r
INNER JOIN resource_history rh ON rh.resource_id = r.id
GROUP BY r.platform, r.namespace, r.resource_name
ORDER BY remaining_history_entries DESC
LIMIT 20;

-- Clean up temp table
DROP TABLE history_to_keep;

COMMIT;

-- ============================================================================
-- To run this script:
-- ============================================================================
-- Option 1: From psql command line
--   psql -h <host> -U <user> -d <database> -f cleanup_duplicate_history.sql
--
-- Option 2: Copy and paste into psql interactive session
--   psql -h <host> -U <user> -d <database>
--   \i cleanup_duplicate_history.sql
--
-- Option 3: From Python script (see cleanup_duplicate_history.py)
-- ============================================================================

