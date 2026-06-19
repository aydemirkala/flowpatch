"""
Federation service for secondary backends.
Secondary backends use this to validate tokens and fetch config from primary backend.
"""

import os
import httpx
from typing import Optional, Dict, Any
from ..logging_utils import log_event


class FederationClient:
    """Client for secondary backends to communicate with primary backend"""
    
    def __init__(self):
        self.primary_url = os.getenv("PRIMARY_BACKEND_URL", "").rstrip("/")
        self.is_primary = os.getenv("BACKEND_TYPE", "secondary") == "primary"
        self.backend_name = os.getenv("BACKEND_NAME", "default-backend")
        self.platform_name = os.getenv("BACKEND_PLATFORM", "default-platform")
        self.registration_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        # TLS verification (default: skip verification for ease of use, can be enabled via env)
        self.verify_tls = os.getenv("PRIMARY_BACKEND_VERIFY_TLS", "false").lower() == "true"
        self.timeout = 10.0  # seconds
        
        if not self.is_primary and not self.primary_url:
            log_event("federation.warning", 
                     message="PRIMARY_BACKEND_URL not set for secondary backend",
                     backend=self.backend_name)
    
    def is_enabled(self) -> bool:
        """Check if federation is enabled (this is a secondary backend with primary URL configured)"""
        return not self.is_primary and bool(self.primary_url)
    
    async def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate JWT token via primary backend.
        Returns user info if valid, None if invalid.
        """
        if self.is_primary:
            # Primary backend validates locally
            return None
        
        if not self.primary_url:
            log_event("federation.validate_token.no_primary_url", backend=self.backend_name)
            return None
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_tls) as client:
                resp = await client.post(
                    f"{self.primary_url}/api/federation/validate-token",
                    headers={"Authorization": f"Bearer {token}"}
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    log_event("federation.validate_token.success", 
                             username=data.get("username"), 
                             role=data.get("role"))
                    return data
                else:
                    log_event("federation.validate_token.failed", 
                             status=resp.status_code, 
                             error=resp.text[:200])
                    return None
        except Exception as e:
            log_event("federation.validate_token.error", error=str(e))
            return None
    
    async def fetch_shared_config(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch shared configuration from primary backend.
        Returns config dict if successful, None if failed.
        """
        if self.is_primary:
            # Primary backend reads from its own database
            return None
        
        if not self.primary_url:
            log_event("federation.fetch_config.no_primary_url", backend=self.backend_name)
            return None
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_tls) as client:
                resp = await client.get(
                    f"{self.primary_url}/api/federation/config",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"backend_name": self.backend_name}  # Include backend name for backend-specific config
                )
                
                if resp.status_code == 200:
                    config = resp.json()
                    log_event("federation.fetch_config.success", backend=self.backend_name,
                             has_backend_config=bool(config.get("backend_config")))
                    return config
                else:
                    log_event("federation.fetch_config.failed", 
                             status=resp.status_code, 
                             error=resp.text[:200])
                    return None
        except Exception as e:
            log_event("federation.fetch_config.error", error=str(e))
            return None
    
    async def self_register(self, api_url: str, skip_tls_verify: bool = False, description: str = "") -> bool:
        """
        Register this secondary backend with the primary backend.
        Returns True if successful, False otherwise.
        """
        if self.is_primary:
            log_event("federation.self_register.is_primary", 
                     message="Primary backend does not need to register")
            return False
        
        if not self.primary_url:
            log_event("federation.self_register.no_primary_url", backend=self.backend_name)
            return False
        
        if not self.registration_token:
            log_event("federation.self_register.no_token", 
                     backend=self.backend_name,
                     message="BACKEND_AUTH_TOKEN not set")
            return False
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self.verify_tls) as client:
                # SECURITY: Send registration_token in request body, not URL params
                # This prevents token exposure in logs
                resp = await client.post(
                    f"{self.primary_url}/api/federation/register",
                    json={
                        "backend_name": self.backend_name,
                        "platform": self.platform_name,
                        "api_url": api_url,
                        "registration_token": self.registration_token,
                        "description": description or "",
                        "skip_tls_verify": skip_tls_verify
                    }
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    log_event("federation.self_register.success", 
                             backend=self.backend_name,
                             platform=self.platform_name,
                             message=data.get("message"))
                    return True
                elif resp.status_code == 400 and "already registered" in resp.text.lower():
                    # Backend is already registered - this is fine, treat as success
                    log_event("federation.self_register.already_registered", 
                             backend=self.backend_name,
                             platform=self.platform_name,
                             message="Backend already registered and approved")
                    return True
                else:
                    log_event("federation.self_register.failed", 
                             status=resp.status_code, 
                             error=resp.text[:500])
                    return False
        except Exception as e:
            log_event("federation.self_register.error", error=str(e))
            return False


# Global singleton instance
_federation_client = None

def get_federation_client() -> FederationClient:
    """Get or create the global federation client instance"""
    global _federation_client
    if _federation_client is None:
        _federation_client = FederationClient()
    return _federation_client

