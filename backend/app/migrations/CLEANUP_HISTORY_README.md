# Resource History Cleanup

## 📋 What This Does

Removes **redundant consecutive duplicate history entries** from the `resource_history` table.

### Problem

Before the recent fix, every sync created a history entry even when nothing changed. This resulted in:

```
kafka-dapr-test history:
- 794445v | 10/15/2025, 12:01:31 PM
- 794445v | 10/15/2025, 2:35:35 PM  ← Duplicate
- 794445v | 10/15/2025, 5:00:59 PM  ← Duplicate
- 794445v | 10/15/2025, 5:14:47 PM  ← Duplicate
... (20 more duplicates)
```

**24 entries, all the same version = meaningless noise!**

### Solution

This cleanup removes consecutive duplicates, keeping only the **first occurrence** of each version:

```
kafka-dapr-test history:
- 794445v | 10/15/2025, 12:01:31 PM  ← Keep (first time this version appeared)
```

**1 entry = clean, meaningful history!**

---

## 🔍 What Gets Kept vs Deleted

### ✅ **KEPT (Meaningful Entries)**

```
- v1.0.0 | 10/15 12:00 PM  ← KEEP (first occurrence)
- v1.2.0 | 10/16 10:00 AM  ← KEEP (version changed)
- v1.0.0 | 10/17 08:00 AM  ← KEEP (rollback/downgrade)
```

### ❌ **DELETED (Redundant Duplicates)**

```
- v1.0.0 | 10/15 12:00 PM
- v1.0.0 | 10/15 02:00 PM  ← DELETE (same version, consecutive)
- v1.0.0 | 10/15 05:00 PM  ← DELETE (same version, consecutive)
- v1.2.0 | 10/16 10:00 AM
- v1.2.0 | 10/16 12:00 PM  ← DELETE (same version, consecutive)
- v1.0.0 | 10/17 08:00 AM
```

---

## 🚀 How to Run

### **Option 1: Python Script (Recommended)**

#### **Dry Run (Safe - No Changes)**
```bash
cd backend
python -m app.migrations.cleanup_duplicate_history
```

Shows what **would** be deleted without actually deleting.

#### **Execute Cleanup**
```bash
cd backend
python -m app.migrations.cleanup_duplicate_history --execute
```

Actually deletes the duplicate entries.

---

### **Option 2: SQL Script (Direct Database)**

#### **Step 1: Get Database Credentials**
```bash
# From your backend pod/container
echo $DATABASE_URL
# postgresql://user:pass@host:5432/dbname
```

#### **Step 2: Run SQL Script**
```bash
psql postgresql://user:pass@host:5432/dbname -f app/migrations/cleanup_duplicate_history.sql
```

Or copy/paste the SQL into your database client.

---

## 📊 Example Output

### Python Script (Dry Run)
```
================================================================================
Resource History Cleanup - DRY RUN
================================================================================
Started at: 2025-10-30T15:30:00

Found 150 resources with history

📦 prod-openshift/patchmgmt/kafka-dapr-test
   Total history: 24 entries
   Duplicates: 23 entries
   Remaining: 1 entries
   [DRY RUN] Would delete IDs: [1234, 1235, 1236, 1237, 1238...]

📦 staging-openshift/prod-api/user-service
   Total history: 15 entries
   Duplicates: 12 entries
   Remaining: 3 entries
   [DRY RUN] Would delete IDs: [5678, 5679, 5680...]

================================================================================
Cleanup Summary:
  - Resources processed: 150
  - Resources cleaned: 45
  - Duplicate entries would be removed: 523
================================================================================

⚠️  This was a DRY RUN - no data was deleted.
   Run with --execute to actually delete duplicates.
```

### Python Script (Execute)
```
================================================================================
Resource History Cleanup - LIVE MODE
================================================================================

📦 prod-openshift/patchmgmt/kafka-dapr-test
   Total history: 24 entries
   Duplicates: 23 entries
   Remaining: 1 entries
   ✅ Deleted 23 duplicate entries

================================================================================
Cleanup Summary:
  - Resources processed: 150
  - Resources cleaned: 45
  - Duplicate entries removed: 523
================================================================================

✅ Cleanup completed successfully!
```

### SQL Script Output
```
 status        | total_history_entries | resources_with_history | entries_to_keep | entries_to_delete 
---------------+-----------------------+------------------------+-----------------+-------------------
 Before Cleanup|                  1247 |                    150 |             724 |               523

DELETE 523

 status        | total_history_entries | resources_with_history 
---------------+-----------------------+------------------------
 After Cleanup |                   724 |                    150
```

---

## ⚠️ Important Notes

1. **Backup First (Optional)**
   ```sql
   -- Create backup table
   CREATE TABLE resource_history_backup AS 
   SELECT * FROM resource_history;
   ```

2. **Safe to Run Multiple Times**
   - Script is idempotent
   - Running again won't delete anything more (no duplicates left)

3. **No Impact on Current Data**
   - Only affects `resource_history` table
   - Does NOT touch `resources` table
   - Does NOT affect current versions or security info

4. **Future Syncs**
   - Backend now only adds history when version/image changes
   - This cleanup is a **one-time operation** for old data

---

## 🔧 Troubleshooting

### "Module not found" Error
```bash
# Make sure you're in the backend directory
cd backend

# Run as module
python -m app.migrations.cleanup_duplicate_history
```

### Database Connection Error
```bash
# Check DATABASE_URL is set
echo $DATABASE_URL

# Or set it manually
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
```

### Permission Denied
```bash
# Make script executable
chmod +x app/migrations/cleanup_duplicate_history.py

# Or run with python
python app/migrations/cleanup_duplicate_history.py
```

---

## 📈 Before vs After

### **Your `kafka-dapr-test` Example**

**Before Cleanup:**
```
Update History: View (24)

Version     | Checked At
------------|---------------------------
794445v     | 10/15/2025, 12:01:31 PM
794445v     | 10/15/2025, 2:35:35 PM
794445v     | 10/15/2025, 5:00:59 PM
... (21 more identical entries)
```

**After Cleanup:**
```
Update History: View (1)

Version     | Changed At
------------|---------------------------
794445v     | 10/15/2025, 12:01:31 PM
```

**After Next Upgrade (e.g., to 800123v):**
```
Update History: View (2)

Version     | Changed At
------------|---------------------------
794445v     | 10/15/2025, 12:01:31 PM
800123v     | 10/30/2025, 3:15:00 PM  ← New entry!
```

---

## ✅ Recommendation

1. **Run DRY RUN first** to see what would be deleted
2. **Review the output** - verify it makes sense
3. **Run with --execute** to actually clean up
4. **Check frontend** - history should now show only meaningful changes

---

## 🆘 Need Help?

If cleanup fails or you're unsure:
1. Check backend logs for errors
2. Verify database connectivity
3. Run dry-run mode first to preview changes
4. Create a backup of `resource_history` table before executing

