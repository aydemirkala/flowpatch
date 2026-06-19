"""
Sync state management for tracking cancelled sync jobs.
Separate module to avoid circular imports between resources.py and refresh.py.
"""

# Track cancelled sync log IDs - checked by refresh loop to stop early
_cancelled_sync_ids: set[int] = set()


def is_sync_cancelled(sync_log_id: int) -> bool:
    """Check if a sync has been cancelled."""
    return sync_log_id in _cancelled_sync_ids


def mark_sync_cancelled(sync_log_id: int):
    """Mark a sync as cancelled."""
    _cancelled_sync_ids.add(sync_log_id)


def clear_cancelled_sync(sync_log_id: int):
    """Remove sync from cancelled set after completion."""
    _cancelled_sync_ids.discard(sync_log_id)

