"""
Image cache service for EOL and Twistlock data.
Prevents duplicate API calls for the same image across different platforms.

Only primary backend fetches from external APIs.
Secondary backends only read from cache.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import ImageCache, ConfigKV
from ..logging_utils import log_event


# Cache TTL settings (in hours)
EOL_CACHE_TTL_HOURS = 24  # EOL data changes rarely, cache for 24 hours
TWISTLOCK_CACHE_TTL_HOURS = 6  # Security data should be fresher, cache for 6 hours
TRIVY_CACHE_TTL_HOURS = 6  # Same freshness as Twistlock


def is_primary_backend() -> bool:
    """Check if this is the primary backend (allowed to fetch external data)."""
    backend_type = os.getenv("BACKEND_TYPE", "secondary")
    return backend_type == "primary"


def _is_cache_valid(fetched_at: Optional[datetime], ttl_hours: int) -> bool:
    """Check if cached data is still valid based on TTL."""
    if not fetched_at:
        return False
    
    # Handle string datetime (from DB)
    if isinstance(fetched_at, str):
        try:
            fetched_at = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
        except Exception:
            return False
    
    # Make sure we compare timezone-aware datetimes
    now = datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    
    age = now - fetched_at
    return age < timedelta(hours=ttl_hours)


def get_cached_eol(db: Session, image: str) -> Tuple[Optional[dict], bool]:
    """
    Get cached EOL data for an image.
    
    Returns:
        Tuple of (data, cache_hit)
        - data: The cached EOL data dict, or None if not cached/expired
        - cache_hit: True if valid cache was found
    """
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if cache and cache.eol_data and _is_cache_valid(cache.eol_fetched_at, EOL_CACHE_TTL_HOURS):
            data = json.loads(cache.eol_data)
            log_event("image_cache.eol.hit", image=image)
            return data, True
    except Exception as e:
        log_event("image_cache.eol.error", image=image, error=str(e))
    
    return None, False


def set_cached_eol(db: Session, image: str, data: Optional[dict], error: Optional[str] = None) -> None:
    """Store EOL data in cache."""
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        
        cache.eol_data = json.dumps(data) if data else None
        cache.eol_fetched_at = datetime.now(timezone.utc)
        cache.eol_error = error
        db.commit()
        
        log_event("image_cache.eol.stored", image=image, has_data=data is not None)
    except Exception as e:
        log_event("image_cache.eol.store_error", image=image, error=str(e))
        db.rollback()


def get_cached_twistlock(db: Session, image: str) -> Tuple[Optional[dict], bool]:
    """
    Get cached Twistlock data for an image.
    
    Returns:
        Tuple of (data, cache_hit)
        - data: The cached Twistlock data dict, or None if not cached/expired
        - cache_hit: True if valid cache was found
    """
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if cache and cache.twistlock_data and _is_cache_valid(cache.twistlock_fetched_at, TWISTLOCK_CACHE_TTL_HOURS):
            data = json.loads(cache.twistlock_data)
            log_event("image_cache.twistlock.hit", image=image)
            return data, True
    except Exception as e:
        log_event("image_cache.twistlock.error", image=image, error=str(e))
    
    return None, False


def set_cached_twistlock(db: Session, image: str, data: Optional[dict], error: Optional[str] = None) -> None:
    """Store Twistlock data in cache."""
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        
        cache.twistlock_data = json.dumps(data) if data else None
        cache.twistlock_fetched_at = datetime.now(timezone.utc)
        cache.twistlock_error = error
        db.commit()
        
        log_event("image_cache.twistlock.stored", image=image, has_data=data is not None)
    except Exception as e:
        log_event("image_cache.twistlock.store_error", image=image, error=str(e))
        db.rollback()


def get_cache_stats(db: Session) -> dict:
    """Get cache statistics."""
    try:
        total = db.query(ImageCache).count()
        
        now = datetime.now(timezone.utc)
        eol_valid = 0
        twistlock_valid = 0
        
        caches = db.query(ImageCache).all()
        for c in caches:
            if _is_cache_valid(c.eol_fetched_at, EOL_CACHE_TTL_HOURS):
                eol_valid += 1
            if _is_cache_valid(c.twistlock_fetched_at, TWISTLOCK_CACHE_TTL_HOURS):
                twistlock_valid += 1
        
        return {
            "total_images": total,
            "eol_cached": eol_valid,
            "twistlock_cached": twistlock_valid,
            "eol_ttl_hours": EOL_CACHE_TTL_HOURS,
            "twistlock_ttl_hours": TWISTLOCK_CACHE_TTL_HOURS,
        }
    except Exception as e:
        return {"error": str(e)}


def clear_cache(db: Session, image: Optional[str] = None) -> int:
    """
    Clear cache entries.
    
    Args:
        image: If provided, clear only this image's cache. Otherwise clear all.
    
    Returns:
        Number of entries deleted.
    """
    try:
        if image:
            deleted = db.query(ImageCache).filter(ImageCache.image == image).delete()
        else:
            deleted = db.query(ImageCache).delete()
        db.commit()
        log_event("image_cache.cleared", image=image, deleted=deleted)
        return deleted
    except Exception as e:
        log_event("image_cache.clear_error", error=str(e))
        db.rollback()
        return 0


def fetch_cache_from_primary(db: Session, image: str, product_name: str = None) -> Tuple[Optional[dict], Optional[dict]]:
    """
    Fetch cache data from primary backend via federation.
    If data is not in primary's cache, primary will fetch it on demand.
    
    Args:
        db: Database session
        image: Full image reference (e.g., docker.io/bitnami/kafka:3.1.0)
        product_name: Optional product name for EOL lookup (e.g., "kafka")
    
    Returns:
        Tuple of (eol_data, twistlock_data) - both can be None
    """
    if is_primary_backend():
        return None, None  # Primary doesn't fetch from itself
    
    try:
        from .config_cache import get_config_cache
        import httpx
        
        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        
        if not primary_url:
            log_event("image_cache.federation.no_primary_url")
            return None, None
        
        # Get auth token for primary
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("image_cache.federation.no_auth_token")
            return None, None
        
        # Fetch cache from primary (primary will fetch on demand if not cached)
        url = f"{primary_url.rstrip('/')}/api/federation/image-cache"
        headers = {"X-Backend-Token": auth_token}
        
        from urllib.parse import quote, urlencode
        params = {"image": image}
        if product_name:
            params["product_name"] = product_name
        
        query_string = urlencode(params)
        
        log_event("image_cache.federation.requesting", 
                 image=image, product_name=product_name, primary=primary_url)
        
        with httpx.Client(timeout=15.0, verify=False) as client:  # Increased timeout for on-demand fetch
            resp = client.get(f"{url}?{query_string}", headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                eol_data = data.get("eol_data")
                twistlock_data = data.get("twistlock_data")
                fetched_on_demand = data.get("fetched_on_demand", False)
                
                # Store in local cache for future use
                if eol_data:
                    set_cached_eol(db, image, eol_data)
                    log_event("image_cache.federation.eol.stored", image=image)
                if twistlock_data:
                    set_cached_twistlock(db, image, twistlock_data)
                    log_event("image_cache.federation.twistlock.stored", image=image)
                
                log_event("image_cache.federation.success", 
                         image=image, 
                         has_eol=eol_data is not None, 
                         has_twistlock=twistlock_data is not None,
                         fetched_on_demand=fetched_on_demand)
                return eol_data, twistlock_data
            elif resp.status_code == 404:
                log_event("image_cache.federation.not_found", image=image, product_name=product_name)
            else:
                log_event("image_cache.federation.error", image=image, status=resp.status_code)
                
    except Exception as e:
        log_event("image_cache.federation.error", image=image, error=str(e))
    
    return None, None


def get_latest_version_config(db: Session) -> dict:
    """
    Get latest version check configuration from federation cache.
    
    Priority:
    1. Per-backend setting (backend_config.latest_version_mode)
    2. Global setting (latest_version_config.mode)
    3. Default: 'local'
    
    Returns:
        dict with 'mode' ('local' or 'primary') and 'cache_hours'
    """
    is_primary = is_primary_backend()
    log_event("latest_version_config.check", is_primary=is_primary)
    
    if is_primary:
        # Primary always uses local
        return {"mode": "local", "cache_hours": 12}
    
    try:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        
        has_cache = bool(cache._cache)
        log_event("latest_version_config.cache_check", has_cache=has_cache)
        
        if cache._cache:
            # 1. Check per-backend setting first
            backend_config = cache._cache.get("backend_config", {})
            per_backend_mode = backend_config.get("latest_version_mode")
            
            log_event("latest_version_config.backend_config", 
                     has_backend_config=bool(backend_config),
                     per_backend_mode=per_backend_mode)
            
            if per_backend_mode:
                # Per-backend setting takes priority
                global_config = cache._cache.get("latest_version_config", {})
                log_event("latest_version_config.using_per_backend", mode=per_backend_mode)
                return {
                    "mode": per_backend_mode,
                    "cache_hours": global_config.get("cache_hours", 12)
                }
            
            # 2. Fall back to global setting
            if "latest_version_config" in cache._cache:
                global_cfg = cache._cache["latest_version_config"]
                log_event("latest_version_config.using_global", mode=global_cfg.get("mode"))
                return global_cfg
    except Exception as e:
        log_event("latest_version_config.get.error", error=str(e))
    
    # Default to local if not configured
    log_event("latest_version_config.using_default", mode="local")
    return {"mode": "local", "cache_hours": 12}


def fetch_latest_version_from_primary(db: Session, image: str) -> Optional[str]:
    """
    Fetch latest version from primary backend via federation.
    Used when latest_version_check_mode is 'primary' on secondary backends.
    
    Args:
        db: Database session
        image: Full image reference (e.g., quay.io/strimzi/kafka:0.36.0)
    
    Returns:
        Latest version string or None if failed
    """
    if is_primary_backend():
        return None  # Primary doesn't fetch from itself
    
    try:
        from .config_cache import get_config_cache
        import httpx
        
        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        
        if not primary_url:
            log_event("latest_version.federation.no_primary_url")
            return None
        
        # Get auth token for primary
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("latest_version.federation.no_auth_token")
            return None
        
        # Request latest version from primary
        url = f"{primary_url.rstrip('/')}/api/federation/latest-version"
        headers = {"X-Backend-Token": auth_token}
        
        from urllib.parse import urlencode
        params = {"image": image}
        query_string = urlencode(params)
        
        log_event("latest_version.federation.requesting", 
                 image=image, primary=primary_url)
        
        with httpx.Client(timeout=30.0, verify=False) as client:  # Longer timeout for registry fetch
            resp = client.get(f"{url}?{query_string}", headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                latest_version = data.get("latest_version")
                from_cache = data.get("from_cache", False)
                
                log_event("latest_version.federation.success", 
                         image=image, 
                         latest_version=latest_version,
                         from_cache=from_cache)
                return latest_version
            elif resp.status_code == 502:
                # Primary couldn't fetch from registry
                error = resp.json().get("detail", "Unknown error")
                log_event("latest_version.federation.primary_failed", 
                         image=image, error=error)
            else:
                log_event("latest_version.federation.error", 
                         image=image, status=resp.status_code)
                
    except Exception as e:
        log_event("latest_version.federation.error", image=image, error=str(e))
    
    return None


def get_eol_config(db: Session) -> dict:
    """
    Get EOL query configuration from federation cache.
    
    Priority:
    1. Per-backend setting (backend_config.eol_mode)
    2. Default: 'local'
    
    Returns:
        dict with 'mode' ('local' or 'primary')
    """
    if is_primary_backend():
        # Primary always uses local
        return {"mode": "local"}
    
    try:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        
        if cache._cache:
            # Check per-backend setting
            backend_config = cache._cache.get("backend_config", {})
            per_backend_mode = backend_config.get("eol_mode")
            
            if per_backend_mode:
                log_event("eol_config.using_per_backend", mode=per_backend_mode)
                return {"mode": per_backend_mode}
    except Exception as e:
        log_event("eol_config.get.error", error=str(e))
    
    # Default to local if not configured
    return {"mode": "local"}


def fetch_eol_from_primary(db: Session, image: str, product: str, version: str) -> Optional[dict]:
    """
    Fetch EOL data from primary backend via federation.
    Used when eol_mode is 'primary' on secondary backends.
    
    Args:
        db: Database session
        image: Full image reference (e.g., quay.io/fluent/fluent-bit:3.1.9)
        product: Product name for EOL lookup (e.g., 'fluent-bit')
        version: Current version (e.g., '3.1.9')
    
    Returns:
        EOL data dict or None if failed
    """
    if is_primary_backend():
        return None  # Primary doesn't fetch from itself
    
    try:
        from .config_cache import get_config_cache
        import httpx
        
        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        
        if not primary_url:
            log_event("eol.federation.no_primary_url")
            return None
        
        # Get auth token for primary
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("eol.federation.no_auth_token")
            return None
        
        # Request EOL data from primary
        url = f"{primary_url.rstrip('/')}/api/federation/eol"
        headers = {"X-Backend-Token": auth_token}
        
        from urllib.parse import urlencode
        params = {"image": image, "product": product, "version": version}
        query_string = urlencode(params)
        
        log_event("eol.federation.requesting", 
                 image=image, product=product, version=version, primary=primary_url)
        
        with httpx.Client(timeout=30.0, verify=False, follow_redirects=True) as client:
            resp = client.get(f"{url}?{query_string}", headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                eol_data = data.get("eol")
                from_cache = data.get("from_cache", False)
                
                if eol_data:
                    log_event("eol.federation.success", 
                             image=image, product=product,
                             eol_date=eol_data.get("eol"),
                             from_cache=from_cache)
                    # Store in local cache
                    set_cached_eol(db, image, eol_data)
                    return eol_data
                else:
                    log_event("eol.federation.no_data", image=image, product=product)
            elif resp.status_code == 404:
                log_event("eol.federation.not_found", image=image, product=product)
            else:
                log_event("eol.federation.error", 
                         image=image, status=resp.status_code)
                
    except Exception as e:
        log_event("eol.federation.error", image=image, error=str(e))
    
    return None


def get_twistlock_config(db: Session) -> dict:
    """
    Get Twistlock query configuration from federation cache.
    
    Priority:
    1. Per-backend setting (backend_config.twistlock_mode)
    2. Default: 'local'
    
    Returns:
        dict with 'mode' ('local' or 'primary')
    """
    if is_primary_backend():
        # Primary always uses local
        return {"mode": "local"}
    
    try:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        
        if cache._cache:
            # Check per-backend setting
            backend_config = cache._cache.get("backend_config", {})
            per_backend_mode = backend_config.get("twistlock_mode")
            
            if per_backend_mode:
                log_event("twistlock_config.using_per_backend", mode=per_backend_mode)
                return {"mode": per_backend_mode}
    except Exception as e:
        log_event("twistlock_config.get.error", error=str(e))
    
    # Default to local if not configured
    return {"mode": "local"}


def fetch_twistlock_from_primary(db: Session, image: str) -> Optional[dict]:
    """
    Fetch Twistlock data from primary backend via federation.
    Used when twistlock_mode is 'primary' on secondary backends.
    
    Args:
        db: Database session
        image: Full image reference (e.g., quay.io/strimzi/kafka:0.36.0)
    
    Returns:
        Twistlock data dict or None if failed
    """
    if is_primary_backend():
        return None  # Primary doesn't fetch from itself
    
    try:
        from .config_cache import get_config_cache
        import httpx
        
        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        
        if not primary_url:
            log_event("twistlock.federation.no_primary_url")
            return None
        
        # Get auth token for primary
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("twistlock.federation.no_auth_token")
            return None
        
        # Request Twistlock data from primary
        url = f"{primary_url.rstrip('/')}/api/federation/twistlock"
        headers = {"X-Backend-Token": auth_token}
        
        from urllib.parse import urlencode
        params = {"image": image}
        query_string = urlencode(params)
        
        log_event("twistlock.federation.requesting", 
                 image=image, primary=primary_url)
        
        with httpx.Client(timeout=30.0, verify=False) as client:
            resp = client.get(f"{url}?{query_string}", headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                twistlock_data = data.get("twistlock")
                from_cache = data.get("from_cache", False)
                
                if twistlock_data:
                    log_event("twistlock.federation.success", 
                             image=image,
                             vulnerabilities=twistlock_data.get("vulnerabilityDistribution", {}).get("total", 0),
                             from_cache=from_cache)
                    return twistlock_data
                else:
                    error = data.get("error", "No data returned")
                    log_event("twistlock.federation.no_data", image=image, error=error)
            else:
                log_event("twistlock.federation.error", 
                         image=image, status=resp.status_code)
                
    except Exception as e:
        log_event("twistlock.federation.error", image=image, error=str(e))
    
    return None


# ── Trivy cache & federation ──────────────────────────────────────────

def _trivy_cache_ttl_hours(db: Session) -> int:
    """Trivy image-cache TTL in hours. Admin-configurable (ConfigKV
    'trivy_cache_ttl_hours'); env fallback TRIVY_CACHE_TTL_HOURS; default 6. Bounded 1..720."""
    val = os.getenv("TRIVY_CACHE_TTL_HOURS", "")
    try:
        row = db.query(ConfigKV).filter(ConfigKV.key == "trivy_cache_ttl_hours").first()
        if row and row.value not in (None, ""):
            val = row.value
    except Exception:
        pass
    try:
        return max(1, min(720, int(val)))
    except (TypeError, ValueError):
        return TRIVY_CACHE_TTL_HOURS


def _twistlock_refresh_hours(db: Session) -> int:
    """How old stored Twistlock data may be before a sync re-fetches it, in hours.
    Admin-configurable (ConfigKV 'twistlock_refresh_hours'); env fallback
    TWISTLOCK_REFRESH_HOURS; default 24. Bounded 1..720."""
    val = os.getenv("TWISTLOCK_REFRESH_HOURS", "")
    try:
        row = db.query(ConfigKV).filter(ConfigKV.key == "twistlock_refresh_hours").first()
        if row and row.value not in (None, ""):
            val = row.value
    except Exception:
        pass
    try:
        return max(1, min(720, int(val)))
    except (TypeError, ValueError):
        return 24


def get_cached_trivy(db: Session, image: str) -> Tuple[Optional[dict], bool]:
    """Get cached Trivy data for an image. Returns (data, cache_hit)."""
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if cache and cache.trivy_data and _is_cache_valid(cache.trivy_fetched_at, _trivy_cache_ttl_hours(db)):
            data = json.loads(cache.trivy_data)
            log_event("image_cache.trivy.hit", image=image)
            return data, True
    except Exception as e:
        log_event("image_cache.trivy.error", image=image, error=str(e))
    return None, False


def set_cached_trivy(db: Session, image: str, data: Optional[dict], error: Optional[str] = None) -> None:
    """Store Trivy data in cache."""
    try:
        cache = db.query(ImageCache).filter(ImageCache.image == image).first()
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        cache.trivy_data = json.dumps(data) if data else None
        cache.trivy_fetched_at = datetime.now(timezone.utc)
        cache.trivy_error = error
        db.commit()
        log_event("image_cache.trivy.stored", image=image, has_data=data is not None)
    except Exception as e:
        log_event("image_cache.trivy.store_error", image=image, error=str(e))
        db.rollback()


def get_trivy_config(db: Session) -> dict:
    """Get Trivy query configuration. Returns dict with 'mode' ('local' or 'primary')."""
    if is_primary_backend():
        return {"mode": "local"}
    try:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        if cache._cache:
            backend_config = cache._cache.get("backend_config", {})
            per_backend_mode = backend_config.get("trivy_mode")
            if per_backend_mode:
                log_event("trivy_config.using_per_backend", mode=per_backend_mode)
                return {"mode": per_backend_mode}
    except Exception as e:
        log_event("trivy_config.get.error", error=str(e))
    return {"mode": "local"}


def fetch_trivy_from_primary(db: Session, image: str) -> Optional[dict]:
    """Fetch Trivy data from primary backend via federation."""
    if is_primary_backend():
        return None
    try:
        from .config_cache import get_config_cache
        import httpx

        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        if not primary_url:
            log_event("trivy.federation.no_primary_url")
            return None
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("trivy.federation.no_auth_token")
            return None

        from urllib.parse import urlencode
        url = f"{primary_url.rstrip('/')}/api/federation/trivy"
        headers = {"X-Backend-Token": auth_token}
        query_string = urlencode({"image": image})

        log_event("trivy.federation.requesting", image=image, primary=primary_url)

        with httpx.Client(timeout=60.0, verify=False) as client:
            resp = client.get(f"{url}?{query_string}", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                trivy_data = data.get("trivy")
                if trivy_data:
                    log_event("trivy.federation.success", image=image,
                              total=trivy_data.get("vulnerabilityDistribution", {}).get("total", 0),
                              from_cache=data.get("from_cache", False))
                    return trivy_data
                else:
                    log_event("trivy.federation.no_data", image=image)
            else:
                log_event("trivy.federation.error", image=image, status=resp.status_code)
    except Exception as e:
        log_event("trivy.federation.error", image=image, error=str(e))
    return None


def get_llm_config(db: Session) -> dict:
    """Get LLM query configuration. Returns dict with 'mode' ('local' or 'primary')."""
    if is_primary_backend():
        return {"mode": "local"}
    try:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        if cache._cache:
            backend_config = cache._cache.get("backend_config", {})
            per_backend_mode = backend_config.get("llm_mode")
            if per_backend_mode:
                log_event("llm_config.using_per_backend", mode=per_backend_mode)
                return {"mode": per_backend_mode}
    except Exception as e:
        log_event("llm_config.get.error", error=str(e))
    return {"mode": "local"}


def fetch_llm_from_primary(db: Session, image: str,
                           resource_name: str = "",
                           current_version: str = "",
                           latest_version: str = "",
                           version_diff: str = "",
                           eol_date: str = "",
                           advice_type: str = "security") -> Optional[str]:
    """Fetch LLM advice from primary backend via federation. Returns advice text or None.
    advice_type: 'security' (sync) or 'upgrade' (ask-ai)."""
    if is_primary_backend():
        return None
    try:
        from .config_cache import get_config_cache
        import httpx

        cache = get_config_cache()
        primary_url = cache.get_primary_url()
        if not primary_url:
            log_event("llm.federation.no_primary_url")
            return None
        auth_token = cache.get_auth_token()
        if not auth_token:
            log_event("llm.federation.no_auth_token")
            return None

        from urllib.parse import urlencode
        url = f"{primary_url.rstrip('/')}/api/federation/llm-advice"
        headers = {"X-Backend-Token": auth_token}
        params = urlencode({
            "image": image,
            "resource_name": resource_name,
            "current_version": current_version,
            "latest_version": latest_version,
            "version_diff": version_diff,
            "eol_date": eol_date,
            "advice_type": advice_type,
        })

        log_event("llm.federation.requesting", image=image, primary=primary_url)

        with httpx.Client(timeout=90.0, verify=False) as client:
            resp = client.get(f"{url}?{params}", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                advice = data.get("advice")
                if advice:
                    log_event("llm.federation.success", image=image,
                              from_cache=data.get("from_cache", False))
                    return advice
                else:
                    log_event("llm.federation.no_data", image=image)
            else:
                log_event("llm.federation.error", image=image, status=resp.status_code)
    except Exception as e:
        log_event("llm.federation.error", image=image, error=str(e))
    return None
