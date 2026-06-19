"""
Configuration cache for secondary backends.
Secondary backends cache shared config from primary at startup and periodically refresh.
"""

import os
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from ..logging_utils import log_event


class ConfigCache:
    """
    Cache for shared configuration fetched from primary backend.
    Used by secondary backends to store Twistlock, SMTP, and other shared settings.
    """
    
    def __init__(self):
        self.is_primary = os.getenv("BACKEND_TYPE", "secondary") == "primary"
        self._cache: Dict[str, Any] = {}
        self._last_fetch: Optional[datetime] = None
        self._fetch_interval = timedelta(minutes=15)  # Refresh every 15 minutes
        self._lock = asyncio.Lock()
    
    async def get_config(self, token: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get cached configuration, fetching from primary if needed.
        
        Args:
            token: JWT token for authenticating with primary
            force_refresh: Force fetch from primary even if cache is fresh
            
        Returns:
            Configuration dictionary with 'twistlock', 'smtp', etc.
        """
        # Primary backends don't use cache
        if self.is_primary:
            return {}
        
        # Check if cache is fresh
        now = datetime.now()
        cache_is_fresh = (
            self._last_fetch is not None and 
            (now - self._last_fetch) < self._fetch_interval
        )
        
        if cache_is_fresh and not force_refresh and self._cache:
            return self._cache.copy()
        
        # Fetch from primary
        async with self._lock:
            # Double-check after acquiring lock
            if cache_is_fresh and not force_refresh and self._cache:
                return self._cache.copy()
            
            try:
                from ..services.federation import get_federation_client
                
                fed_client = get_federation_client()
                if not fed_client.is_enabled():
                    log_event("config_cache.federation_disabled", 
                             message="Federation not enabled, returning empty config")
                    return {}
                
                if not token:
                    log_event("config_cache.no_token", 
                             message="No token provided, cannot fetch config from primary")
                    # Return cached config if available, empty otherwise
                    return self._cache.copy() if self._cache else {}
                
                # Fetch from primary
                config = await fed_client.fetch_shared_config(token)
                
                if config:
                    self._cache = config
                    self._last_fetch = now
                    backend_config = config.get("backend_config", {})
                    security_thresholds = config.get("security_thresholds", {})
                    latest_version_config = config.get("latest_version_config", {})
                    log_event("config_cache.updated", 
                             backend=fed_client.backend_name,
                             has_twistlock=bool(config.get("twistlock", {}).get("url")),
                             has_smtp=bool(config.get("smtp", {}).get("server")),
                             managed_products=len(config.get("managed_products", [])),
                             product_list=len(config.get("product_list", [])),
                             auto_sync_schedule=backend_config.get("auto_sync_schedule"),
                             force_sync_enabled=backend_config.get("force_sync_enabled"),
                             cleanup_force_enabled=backend_config.get("cleanup_force_enabled"),
                             use_dynamic_discovery=backend_config.get("use_dynamic_discovery"),
                             latest_version_mode=backend_config.get("latest_version_mode"),
                             latest_version_global_mode=latest_version_config.get("mode"),
                             latest_version_cache_hours=latest_version_config.get("cache_hours"),
                             has_security_thresholds=bool(security_thresholds))
                    return config.copy()
                else:
                    log_event("config_cache.fetch_failed", 
                             message="Failed to fetch config from primary, using cached if available")
                    return self._cache.copy() if self._cache else {}
                    
            except Exception as e:
                log_event("config_cache.error", error=str(e))
                # Return cached config if available
                return self._cache.copy() if self._cache else {}
    
    def clear(self):
        """Clear the configuration cache."""
        self._cache = {}
        self._last_fetch = None
        log_event("config_cache.cleared")
    
    def get_backend_config(self) -> Dict[str, Any]:
        """Get backend-specific configuration from cache (synchronous)."""
        return self._cache.get("backend_config", {})
    
    def get_twistlock_config(self) -> Dict[str, Any]:
        """Get Twistlock configuration from cache (synchronous)."""
        return self._cache.get("twistlock", {})
    
    def get_trivy_config(self) -> Dict[str, Any]:
        """Get Trivy configuration from cache (synchronous)."""
        return self._cache.get("trivy_config", {})
    
    def get_smtp_config(self) -> Dict[str, Any]:
        """
        Get SMTP configuration from cache (synchronous).
        
        Note: Secondary backends should NOT use this.
        SMTP is only used by primary backend to send emails.
        """
        return self._cache.get("smtp", {})
    
    def get_managed_products(self, backend_name: Optional[str] = None) -> list:
        """
        Get managed products from cache (synchronous).
        
        In federation mode, returns ALL managed products (not filtered by backend).
        All backends share the same product list and discover them in their own clusters.
        
        Args:
            backend_name: Optional filter by backend name (ignored in federation mode)
            
        Returns list of managed products.
        """
        all_products = self._cache.get("managed_products", [])
        # Return ALL products - each backend discovers these products in its own cluster
        return all_products
    
    def get_product_list(self) -> list:
        """Get full product list from cache (synchronous)."""
        return self._cache.get("product_list", [])
    
    def get_security_thresholds(self) -> Dict[str, Any]:
        """
        Get security status thresholds from cache (synchronous).
        Returns empty dict if not available (caller should use defaults).
        """
        return self._cache.get("security_thresholds", {})
    
    def get_llm_prompts(self) -> Dict[str, str]:
        """Get LLM prompts from cache (synchronous). Returns dict with 'security' and 'upgrade' keys."""
        return self._cache.get("llm_prompts", {})

    def get_primary_url(self) -> Optional[str]:
        """Get the primary backend URL (from environment variable)."""
        if self.is_primary:
            return None  # Primary doesn't need this
        return os.getenv("PRIMARY_BACKEND_URL", "").rstrip("/") or None
    
    def get_auth_token(self) -> Optional[str]:
        """Get the auth token for this backend (from environment variable)."""
        if self.is_primary:
            return None
        return os.getenv("BACKEND_AUTH_TOKEN", "") or None


# Global singleton instance
_config_cache = None

def get_config_cache() -> ConfigCache:
    """Get or create the global configuration cache instance."""
    global _config_cache
    if _config_cache is None:
        _config_cache = ConfigCache()
    return _config_cache

