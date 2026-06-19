from __future__ import annotations

import json
import ssl
import time
import os
from typing import Any, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
from ..services.versioning import parse_image
from urllib.error import URLError, HTTPError

from ..config import settings
from ..db import SessionLocal
from ..models import ConfigKV
from ..logging_utils import log_event


def _retry_request(func, max_retries=3, delay=1.0):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except (URLError, HTTPError, TimeoutError) as e:
            if attempt == max_retries - 1:
                raise  # Last attempt, raise the error
            wait_time = delay * (2 ** attempt)  # Exponential backoff
            try:
                log_event("retry.attempt", attempt=attempt+1, max=max_retries, wait=wait_time, error=str(e))
            except Exception:
                pass
            time.sleep(wait_time)
    return None


def _request(url: str, *, method: str = "GET", headers: Optional[dict[str, str]] = None, data: Optional[bytes] = None, verify: bool = True) -> Optional[bytes]:
    def _do_request():
        req = Request(url=url, method=method, data=data)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        context = None if verify else ssl._create_unverified_context()  # nosec - user provided toggle
        with urlopen(req, context=context, timeout=10) as resp:  # nosec - external GET by config
            return resp.read()
    
    try:
        return _retry_request(_do_request, max_retries=3, delay=0.5)
    except (URLError, HTTPError) as e:
        try:
            log_event("twistlock.http_error", url=url, method=method, error=str(e))
        except Exception:
            pass
        return None


def _effective_config() -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """
    Get effective Twistlock configuration.
    For primary backends: reads from database.
    For secondary backends: uses cached config from primary.
    """
    url_cfg = settings.twistlock_url
    user_cfg = settings.twistlock_user
    pwd_cfg = settings.twistlock_user_password
    verify_cfg = bool(settings.twistlock_verify)
    
    # Check if this is a secondary backend
    backend_type = os.getenv("BACKEND_TYPE", "secondary")
    is_secondary = backend_type == "secondary"
    
    if is_secondary:
        # Secondary backend: use cached config from primary
        try:
            from ..services.config_cache import get_config_cache
            
            cache = get_config_cache()
            tw_config = cache.get_twistlock_config()
            
            if tw_config:
                url_cfg = tw_config.get("url") or url_cfg
                user_cfg = tw_config.get("username") or user_cfg
                pwd_cfg = tw_config.get("password") or pwd_cfg
                skip_tls = tw_config.get("skip_tls_verify", False)
                verify_cfg = not skip_tls  # Inverted logic
                
                log_event("twistlock.config.using_federated", 
                         has_url=bool(url_cfg),
                         has_credentials=bool(user_cfg and pwd_cfg),
                         skip_tls_verify=not verify_cfg,
                         verify_tls=verify_cfg)
                return url_cfg, user_cfg, pwd_cfg, verify_cfg
            else:
                log_event("twistlock.config.no_federated_config",
                         message="No federated config available, falling back to local database")
        except Exception as e:
            log_event("twistlock.config.federation_error", error=str(e))
    
    # Primary backend or fallback: read from local database
    try:
        db = SessionLocal()
        kv = {r.key: r.value for r in db.query(ConfigKV).filter(ConfigKV.key.in_(["twistlock_url", "twistlock_user", "twistlock_user_password", "twistlock_skip_tls_verify"]))}
        url_cfg = kv.get("twistlock_url", url_cfg)
        user_cfg = kv.get("twistlock_user", user_cfg)
        pwd_cfg = kv.get("twistlock_user_password", pwd_cfg)
        if kv.get("twistlock_skip_tls_verify") is not None:
            verify_cfg = str(kv.get("twistlock_skip_tls_verify")).lower() != "true"  # Inverted: skip=true means verify=false
    except Exception as e:
        try:
            log_event("twistlock.config.read_error", error=str(e))
        except Exception:
            pass
    return url_cfg, user_cfg, pwd_cfg, verify_cfg


def _get_token() -> Optional[str]:
    url_cfg, user_cfg, pwd_cfg, verify_cfg = _effective_config()
    if not (url_cfg and user_cfg and pwd_cfg):
        try:
            log_event("twistlock.auth.skip", reason="not_configured")
        except Exception:
            pass
        return None
    url = str(url_cfg).rstrip("/") + "/api/v1/authenticate"
    payload = {"username": user_cfg, "password": pwd_cfg, "token": ""}
    body = json.dumps(payload).encode("utf-8")
    resp = _request(url, method="POST", headers={"Content-Type": "application/json"}, data=body, verify=verify_cfg)
    if not resp:
        try:
            log_event("twistlock.auth.error", url=url, user=str(user_cfg))
        except Exception:
            pass
        return None
    try:
        data = json.loads(resp.decode("utf-8"))
        token = data.get("token")
        return token
    except Exception:
        try:
            log_event("twistlock.auth.parse_error")
        except Exception:
            pass
        return None


def fetch_image_report(image: str) -> Optional[dict[str, Any]]:
    """Fetch compact image report from Twistlock/Prisma Cloud.

    Returns a dict with keys: vulnerabilities, complianceIssues, vulnerabilityDistribution,
    vulnerabilityRiskScore, complianceRiskScore (subset) when available.
    """
    url_cfg, _, _, verify_cfg = _effective_config()
    token = _get_token()
    if not token:
        try:
            log_event("twistlock.fetch.skip", image=image, reason="no_token")
        except Exception:
            pass
        return None
    # Decide base URL from effective config
    base = url_cfg or ""
    if not base:
        return None
    base = base.rstrip("/")
    # Normalize image name to include docker.io and latest when missing
    normalized = image
    try:
        pi = parse_image(image)
        if pi is not None:
            repo = pi.repository
            if pi.registry == "docker.io" and not repo.startswith("library/") and "/" not in repo:
                repo = f"library/{repo}"
            normalized = f"{pi.registry}/{repo}:{pi.tag or 'latest'}"
    except Exception:
        pass
    enc_name = quote_plus(normalized)
    url = f"{base}/api/v1/images?name={enc_name}&compact=true"
    try:
        log_event("twistlock.fetch.start", image=normalized, url=base)
    except Exception:
        pass
    resp = _request(url, method="GET", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, verify=verify_cfg)
    if not resp:
        try:
            log_event("twistlock.fetch.error", url=url, image=normalized)
        except Exception:
            pass
        return None
    try:
        data = json.loads(resp.decode("utf-8"))
        if not isinstance(data, list) or not data:
            # Return only the jq-shaped subset
            default = {
                "vulnerabilities": None,
                "vulnerabilityDistribution": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
                "riskFactorCount": 0,
            }
            try:
                log_event("twistlock.fetch.empty_default", image=normalized)
            except Exception:
                pass
            return default
        first = data[0]
        rf = first.get("riskFactors")
        # riskFactors may be a list or an object; both should contribute to count
        risk_count = len(rf) if isinstance(rf, (list, dict)) else 0
        keep = {
            "vulnerabilities": first.get("vulnerabilities"),
            "vulnerabilityDistribution": first.get("vulnerabilityDistribution"),
            "riskFactorCount": risk_count,
        }
        try:
            log_event("twistlock.fetch.success", image=normalized, riskFactorCount=risk_count)
        except Exception:
            pass
        # Ensure distribution defaults exist
        keep.setdefault("vulnerabilityDistribution", {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0})
        keep.setdefault("vulnerabilities", None)
        keep.setdefault("riskFactorCount", 0)
        return keep
    except Exception:
        try:
            log_event("twistlock.fetch.parse_error", image=normalized)
        except Exception:
            pass
        return None


def fetch_twistlock_cves(image: str, db=None) -> Optional[dict[str, Any]]:
    """Fetch Twistlock CVE/vulnerability data for an image.
    
    This is a convenience wrapper around fetch_image_report.
    The db parameter is kept for backward compatibility but not used.
    """
    return fetch_image_report(image)


