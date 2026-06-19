from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from .config import settings

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    log_dir = settings.log_dir or "/tmp"
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "api.log")

    max_bytes = settings.log_max_size_mb * 1024 * 1024
    backup_count = settings.log_backup_count

    logger = logging.getLogger("patchmgmt.events")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _logger = logger
    return logger


def log_event(event: str, **fields: Any) -> None:
    """Write a single-line JSON log with UTC timestamp.

    Uses RotatingFileHandler for automatic size-based rotation.
    Rotation is controlled by LOG_MAX_SIZE_MB and LOG_BACKUP_COUNT env vars.
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        payload = {"ts": ts, "event": event}
        payload.update(fields)
        _get_logger().info(json.dumps(payload, separators=(",", ":")))
    except Exception:
        pass
