from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File, Header, Request
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from datetime import datetime, timezone
from jose import jwt
import secrets
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..db import get_db
from ..models import User, Role, ConfigKV, BackendEndpoint, EmailRecipient, Resource, ResourceHistory, ResourcePlan, SyncLog, ProductList, ManagedProduct, LdapConfig
from ..config import settings
from ..services.email_service import send_email_with_csv, test_smtp_connection
from ..logging_utils import log_event


router = APIRouter()

# Use PBKDF2-SHA256 to avoid system bcrypt backend issues/limits
pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
JWT_ALG = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "changeme-secret-PLEASE-SET-ENV")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# Pydantic models for request bodies
class BackendRegistrationRequest(BaseModel):
    """Request body for backend self-registration (secure - no tokens in URL)"""
    backend_name: str
    platform: str
    api_url: str
    registration_token: str  # Sent in body, not URL, for security
    description: Optional[str] = None
    skip_tls_verify: bool = False


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)


@router.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> dict:
    import os
    
    # Check if this backend should proxy LDAP auth to primary
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    this_backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).first()
    
    ldap_mode = "local"  # Default for primary
    if this_backend:
        ldap_mode = getattr(this_backend, 'ldap_mode', 'primary') or 'primary'
        # Primary backends always use local LDAP
        if this_backend.backend_type == "primary":
            ldap_mode = "local"
    
    # If ldap_mode is 'primary', proxy to primary backend
    if ldap_mode == "primary":
        proxy_result = _proxy_ldap_auth_to_primary(form.username, form.password, db)
        if proxy_result:
            return proxy_result
        # If proxy failed or primary not available, try local auth
    else:
        # Try local LDAP auth
        ldap_config = db.query(LdapConfig).first()
        if ldap_config and ldap_config.enabled:
            ldap_result = _try_ldap_auth(form.username, form.password, ldap_config, db)
            if ldap_result:
                return ldap_result
    
    # Fall back to local authentication
    user: Optional[User] = db.query(User).filter(User.username == form.username).one_or_none()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    role = db.query(Role).filter(Role.id == user.role_id).one()
    payload = {
        "sub": user.username,
        "role": role.name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    # Include user's theme preference in login response
    user_theme = getattr(user, "theme", "dark") or "dark"
    return {
        "access_token": token, 
        "token_type": "bearer", 
        "role": role.name, 
        "username": user.username, 
        "must_change": bool(getattr(user, "password_must_change", False)),
        "theme": user_theme
    }


def _proxy_ldap_auth_to_primary(username: str, password: str, db: Session) -> Optional[dict]:
    """Proxy LDAP authentication to primary backend."""
    import httpx
    from ..logging_utils import log_event
    
    # Find primary backend
    primary = db.query(BackendEndpoint).filter(
        BackendEndpoint.backend_type == "primary",
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).first()
    
    if not primary:
        log_event("ldap.proxy.no_primary", error="No primary backend found")
        return None
    
    try:
        # Call primary's LDAP proxy endpoint
        url = f"{primary.api_url.rstrip('/')}/api/auth/ldap-proxy"
        
        with httpx.Client(verify=not primary.skip_tls_verify, timeout=30.0) as client:
            resp = client.post(
                url,
                data={"username": username, "password": password},
                headers={"X-Backend-Token": primary.auth_token}
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    # Create/update local user based on primary's response
                    user_info = data.get("user_info", {})
                    role_name = user_info.get("role", "read-only")
                    
                    role = db.query(Role).filter(Role.name == role_name).one_or_none()
                    if not role:
                        role = db.query(Role).filter(Role.name == "read-only").one()
                    
                    user = db.query(User).filter(User.username == username).one_or_none()
                    if not user:
                        user = User(
                            username=username,
                            password_hash="LDAP_PROXY_USER",
                            role_id=role.id,
                            ldap_user=True,
                            password_must_change=False
                        )
                        db.add(user)
                        db.commit()
                        db.refresh(user)
                        log_event("ldap.proxy.user_created", username=username, role=role_name)
                    elif user.role_id != role.id:
                        user.role_id = role.id
                        db.commit()
                    
                    # Generate local token
                    payload = {
                        "sub": user.username,
                        "role": role_name,
                        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
                    }
                    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
                    user_theme = getattr(user, "theme", "dark") or "dark"
                    
                    log_event("ldap.proxy.success", username=username, primary=primary.name)
                    return {
                        "access_token": token,
                        "token_type": "bearer",
                        "role": role_name,
                        "username": user.username,
                        "must_change": False,
                        "theme": user_theme
                    }
                else:
                    log_event("ldap.proxy.auth_failed", username=username, message=data.get("message"))
                    return None
            else:
                log_event("ldap.proxy.request_failed", status=resp.status_code)
                return None
                
    except Exception as e:
        log_event("ldap.proxy.error", error=str(e))
        return None


def _try_ldap_auth(username: str, password: str, ldap_config: LdapConfig, db: Session) -> Optional[dict]:
    """Attempt LDAP authentication. Returns login response dict or None if failed."""
    from ..services.ldap_service import authenticate_user, determine_role_from_groups
    
    if not ldap_config.server_url or not ldap_config.bind_dn or not ldap_config.bind_password:
        return None  # LDAP not properly configured
    
    success, ldap_user, message = authenticate_user(
        server_url=ldap_config.server_url,
        port=ldap_config.port,
        use_ssl=ldap_config.use_ssl,
        bind_dn=ldap_config.bind_dn,
        bind_password=ldap_config.bind_password,
        base_dn=ldap_config.base_dn or "",
        user_search_filter=ldap_config.user_search_filter or "(&(objectClass=user)(sAMAccountName={username}))",
        user_search_base=ldap_config.user_search_base,
        username=username,
        password=password,
        username_attribute=ldap_config.username_attribute,
        email_attribute=ldap_config.email_attribute,
        display_name_attribute=ldap_config.display_name_attribute,
        ca_cert=ldap_config.ca_cert,
        skip_cert_verify=getattr(ldap_config, 'skip_cert_verify', False)
    )
    
    if not success or not ldap_user:
        return None  # LDAP auth failed, try local auth
    
    # Determine role from group membership
    role_name = determine_role_from_groups(
        ldap_user.groups,
        ldap_config.admin_group_dn,
        ldap_config.analyst_group_dn,
        ldap_config.readonly_group_dn
    )
    
    # Get or create user in local DB
    user = db.query(User).filter(User.username == username).one_or_none()
    role = db.query(Role).filter(Role.name == role_name).one_or_none()
    if not role:
        role = db.query(Role).filter(Role.name == "read-only").one()
    
    if not user:
        # Create new LDAP user
        user = User(
            username=username,
            password_hash="LDAP_USER",  # Placeholder, not used for LDAP users
            role_id=role.id,
            ldap_user=True,
            password_must_change=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        log_event("ldap.user.created", username=username, role=role_name)
    else:
        # Update existing user's role if it changed
        if user.role_id != role.id:
            user.role_id = role.id
            db.commit()
            log_event("ldap.user.role_updated", username=username, role=role_name)
    
    # Generate token
    payload = {
        "sub": user.username,
        "role": role_name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    user_theme = getattr(user, "theme", "dark") or "dark"
    
    log_event("ldap.auth.login", username=username, role=role_name)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": role_name,
        "username": user.username,
        "must_change": False,  # LDAP users don't need to change password locally
        "theme": user_theme
    }


def _decode_claims(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return {"username": str(payload.get("sub")), "role": str(payload.get("role"))}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def _decode_role(token: str) -> str:
    return _decode_claims(token)["role"]


def _get_config(db: Session, key: str, default: str = "") -> str:
    row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
    return row.value if row else default


def _set_config(db: Session, key: str, value: Optional[str]) -> None:
    row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
    if row is None:
        db.add(ConfigKV(key=key, value=value))
    else:
        row.value = value


def _trigger_sync_all_backends(db: Session, token: str, product_name: str) -> dict:
    """Trigger FULL sync for all backends (used by import)."""
    import os
    import httpx
    from ..logging_utils import log_event
    
    results = {"local": None, "remote": []}
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # 1. Trigger local full sync in background
    try:
        import threading
        from ..services.refresh import refresh_from_sources
        
        def _local_sync():
            from ..db import SessionLocal
            local_db = SessionLocal()
            try:
                refresh_from_sources(local_db, triggered_by=f"import:{product_name}", use_dynamic=True)
            except Exception as e:
                log_event("product.sync.local.error", product=product_name, error=str(e))
            finally:
                local_db.close()
        
        thread = threading.Thread(target=_local_sync, daemon=True)
        thread.start()
        results["local"] = "triggered"
        log_event("product.sync.local.triggered", product=product_name, backend=backend_name)
    except Exception as e:
        results["local"] = f"error: {str(e)}"
        log_event("product.sync.local.error", product=product_name, error=str(e))
    
    # 2. Trigger full sync on all remote backends
    remote_backends = db.query(BackendEndpoint).filter(
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True,
        BackendEndpoint.name != backend_name,
    ).all()
    
    for backend in remote_backends:
        try:
            verify_ssl = not backend.skip_tls_verify
            with httpx.Client(timeout=10.0, verify=verify_ssl) as client:
                url = f"{backend.api_url.rstrip('/')}/api/resources/refresh"
                resp = client.post(url, headers={"Authorization": f"Bearer {backend.auth_token}"})
                
                if resp.status_code == 200:
                    results["remote"].append({"backend": backend.name, "status": "triggered"})
                    log_event("product.sync.remote.triggered", product=product_name, backend=backend.name)
                else:
                    results["remote"].append({"backend": backend.name, "status": f"error: {resp.status_code}"})
        except Exception as e:
            results["remote"].append({"backend": backend.name, "status": f"error: {str(e)}"})
    
    return results


def _trigger_product_sync_all_backends(db: Session, product_name: str) -> dict:
    """Trigger sync for a SINGLE product on all backends (fast, targeted)."""
    import os
    import httpx
    from ..logging_utils import log_event
    
    results = {"local": None, "remote": []}
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    log_event("product.targeted_sync.start", product=product_name, backend=backend_name)
    
    # 1. Local: Discover and sync only this product
    try:
        import threading
        from ..services.refresh import refresh_single_product
        
        def _local_product_sync():
            from ..db import SessionLocal
            local_db = SessionLocal()
            try:
                result = refresh_single_product(local_db, product_name, triggered_by=f"product_added:{product_name}")
                log_event("product.targeted_sync.local.complete", product=product_name, **result)
            except Exception as e:
                log_event("product.targeted_sync.local.error", product=product_name, error=str(e))
            finally:
                local_db.close()
        
        thread = threading.Thread(target=_local_product_sync, daemon=True)
        thread.start()
        results["local"] = "triggered"
    except Exception as e:
        results["local"] = f"error: {str(e)}"
    
    # 2. Trigger product sync on remote backends
    remote_backends = db.query(BackendEndpoint).filter(
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True,
        BackendEndpoint.name != backend_name,
    ).all()
    
    for backend in remote_backends:
        try:
            verify_ssl = not backend.skip_tls_verify
            with httpx.Client(timeout=10.0, verify=verify_ssl) as client:
                # Use product-specific sync endpoint
                url = f"{backend.api_url.rstrip('/')}/api/resources/sync-product"
                resp = client.post(
                    url, 
                    json={"product_name": product_name},
                    headers={"Authorization": f"Bearer {backend.auth_token}"}
                )
                
                if resp.status_code == 200:
                    results["remote"].append({"backend": backend.name, "status": "triggered"})
                    log_event("product.targeted_sync.remote.triggered", product=product_name, backend=backend.name)
                else:
                    # Fallback: try full sync if product-specific not available
                    url_fallback = f"{backend.api_url.rstrip('/')}/api/resources/refresh"
                    resp2 = client.post(url_fallback, headers={"Authorization": f"Bearer {backend.auth_token}"})
                    if resp2.status_code == 200:
                        results["remote"].append({"backend": backend.name, "status": "triggered (full)"})
                    else:
                        results["remote"].append({"backend": backend.name, "status": f"error: {resp.status_code}"})
        except Exception as e:
            results["remote"].append({"backend": backend.name, "status": f"error: {str(e)}"})
    
    return results


@router.get("/admin/users")
def list_users(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[dict]:
    role = _decode_claims(token)["role"]
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = (
        db.query(User.username, Role.name)
        .join(Role, Role.id == User.role_id)
        .order_by(User.username)
        .all()
    )
    return [{"username": u, "role": r} for (u, r) in rows]


@router.get("/admin/roles")
def list_roles(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[str]:
    role = _decode_claims(token)["role"]
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    # Ensure roles exist before returning
    _ensure_roles(db)
    return [r.name for r in db.query(Role).order_by(Role.name).all()]


@router.get("/admin/twistlock")
def get_twistlock_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    def getv(k: str, default: Optional[str] = None) -> Optional[str]:
        row = db.query(ConfigKV).filter(ConfigKV.key == k).one_or_none()
        return row.value if row else default
    
    # Read skip_tls_verify and convert to verify (inverted logic)
    skip_tls = getv("twistlock_skip_tls_verify", "false")
    verify = "false" if skip_tls == "true" else "true"
    
    return {
        "url": getv("twistlock_url"),
        "user": getv("twistlock_user"),
        "verify": verify,
        "refresh_hours": int(getv("twistlock_refresh_hours", "24") or "24"),
        # Do not return password
    }


@router.post("/admin/twistlock")
def set_twistlock_config(
    url: Optional[str] = Form(None),
    user: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    verify: Optional[str] = Form("true"),
    refresh_hours: Optional[str] = Form(None),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    def upsert(key: str, value: Optional[str]):
        row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if row is None:
            row = ConfigKV(key=key, value=value)
            db.add(row)
        else:
            row.value = value
    upsert("twistlock_url", (url or None))
    upsert("twistlock_user", (user or None))
    if password is not None and password != "":
        upsert("twistlock_user_password", password)
    
    # The UI 'verify' parameter is about TLS certificate verification
    # verify="true" means verify TLS certificates (don't skip)
    # verify="false" means skip TLS certificate verification
    # Map this to twistlock_skip_tls_verify (inverted logic)
    skip_tls = "true" if verify == "false" else "false"
    upsert("twistlock_skip_tls_verify", skip_tls)

    if refresh_hours is not None and str(refresh_hours).strip() != "":
        # How old stored Twistlock data may be before a sync re-fetches it (1..720h).
        try:
            rh = max(1, min(720, int(str(refresh_hours).strip())))
        except (TypeError, ValueError):
            rh = 24
        upsert("twistlock_refresh_hours", str(rh))

    # Note: twistlock_verify is a separate setting (whether to use Twistlock at all)
    # and is not changed by this endpoint

    db.commit()
    return {"ok": True}


@router.get("/admin/trivy")
def get_trivy_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get Trivy server configuration"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    url = _get_config(db, "trivy_url", "")
    skip_tls = _get_config(db, "trivy_skip_tls_verify", "true")
    max_detail = _get_config(db, "trivy_max_detail_vulns", "20")
    dockerhub_proxy = _get_config(db, "trivy_dockerhub_proxy", "")
    return {"url": url, "skip_tls_verify": skip_tls, "max_detail_vulns": max_detail,
            "dockerhub_proxy": dockerhub_proxy,
            "scan_timeout_seconds": int(_get_config(db, "trivy_scan_timeout_seconds", "300")),
            "cache_ttl_hours": int(_get_config(db, "trivy_cache_ttl_hours", "6"))}


@router.post("/admin/trivy")
def set_trivy_config(
    url: str = Form(""),
    skip_tls_verify: str = Form("true"),
    max_detail_vulns: str = Form("20"),
    dockerhub_proxy: str = Form(""),
    scan_timeout_seconds: str = Form("300"),
    cache_ttl_hours: str = Form("6"),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Save Trivy server configuration.

    max_detail_vulns caps how many per-CVE entries are stored in the detailed
    report (0 = unlimited). Vulnerability distribution counts are always complete
    and unaffected by this cap.

    dockerhub_proxy (optional, e.g. "harbor.example.com/docker-proxy") makes Trivy
    pull docker.io images through that proxy to avoid Docker Hub rate limits; empty
    disables the rewrite."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    _set_config(db, "trivy_url", url)
    _set_config(db, "trivy_skip_tls_verify", skip_tls_verify)
    try:
        n = int(str(max_detail_vulns).strip() or "20")
        if n < 0:
            n = 0
    except (TypeError, ValueError):
        n = 20
    _set_config(db, "trivy_max_detail_vulns", str(n))
    _set_config(db, "trivy_dockerhub_proxy", (dockerhub_proxy or "").strip().rstrip("/"))
    try:
        st = max(30, min(1800, int(str(scan_timeout_seconds).strip() or "300")))
    except (TypeError, ValueError):
        st = 300
    _set_config(db, "trivy_scan_timeout_seconds", str(st))
    try:
        ttl = max(1, min(720, int(str(cache_ttl_hours).strip() or "6")))
    except (TypeError, ValueError):
        ttl = 6
    _set_config(db, "trivy_cache_ttl_hours", str(ttl))
    db.commit()
    log_event("admin.trivy.config_updated", url=url, skip_tls=skip_tls_verify,
              max_detail_vulns=n, dockerhub_proxy=(dockerhub_proxy or "").strip())
    return {"ok": True}


@router.get("/admin/llm")
def get_llm_providers(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get all LLM providers. api_key is masked in the response."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    raw = _get_config(db, "llm_providers", "[]")
    try:
        providers = json.loads(raw)
    except Exception:
        providers = []
    # Migration: convert old flat keys to provider array
    if not providers:
        old_url = _get_config(db, "llm_base_url", "")
        if old_url:
            providers = [{
                "id": "default",
                "name": "Default LLM",
                "base_url": old_url,
                "model": _get_config(db, "llm_model", "llm"),
                "api_key": _get_config(db, "llm_api_key", "dummy"),
                "max_tokens": int(_get_config(db, "llm_max_tokens", "1000")),
                "temperature": float(_get_config(db, "llm_temperature", "0.3")),
                "active": _get_config(db, "llm_enabled", "false") == "true",
            }]
    # Mask api_key before returning to frontend
    masked = [{**p, "api_key": "***" if p.get("api_key") else ""} for p in providers]
    return {"providers": masked}


class LLMProviderPayload(BaseModel):
    providers: list


@router.post("/admin/llm")
def set_llm_providers(
    payload: LLMProviderPayload,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Save all LLM providers. Encrypts api_key before storing."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    from ..services.crypto_utils import encrypt_value, decrypt_value

    # Load existing providers to preserve encrypted keys when api_key is blank
    raw_existing = _get_config(db, "llm_providers", "[]")
    try:
        existing = {p["id"]: p for p in json.loads(raw_existing)}
    except Exception:
        existing = {}

    to_save = []
    for p in payload.providers:
        provider = dict(p)
        raw_key = provider.get("api_key", "")
        if not raw_key or raw_key == "***":
            # Keep existing encrypted key
            prev = existing.get(provider.get("id", ""), {})
            provider["api_key"] = prev.get("api_key", "")
        else:
            provider["api_key"] = encrypt_value(raw_key)
        to_save.append(provider)

    _set_config(db, "llm_providers", json.dumps(to_save, ensure_ascii=False))
    db.commit()
    active = [p for p in to_save if p.get("active")]
    log_event("admin.llm.providers_updated", count=len(to_save),
              active=active[0].get("name") if active else "none")
    return {"ok": True}


@router.get("/admin/llm-prompts")
def get_llm_prompts(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get LLM system prompts (security and upgrade)."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    from ..services.llm_advice import (DEFAULT_SECURITY_PROMPT, DEFAULT_UPGRADE_PROMPT,
                                        DEFAULT_EOL_STATUS_PROMPT)
    security = _get_config(db, "llm_prompt_security", "")
    upgrade = _get_config(db, "llm_prompt_upgrade", "")
    eol_status = _get_config(db, "llm_prompt_eol_status", "")
    return {
        "security_prompt": security or "",
        "upgrade_prompt": upgrade or "",
        "eol_status_prompt": eol_status or "",
        "security_default": DEFAULT_SECURITY_PROMPT,
        "upgrade_default": DEFAULT_UPGRADE_PROMPT,
        "eol_status_default": DEFAULT_EOL_STATUS_PROMPT,
    }


class LLMPromptsPayload(BaseModel):
    security_prompt: str = ""
    upgrade_prompt: str = ""
    eol_status_prompt: str = ""


@router.post("/admin/llm-prompts")
def set_llm_prompts(
    payload: LLMPromptsPayload,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Save LLM system prompts. Empty value resets to default."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    _set_config(db, "llm_prompt_security", payload.security_prompt.strip() or None)
    _set_config(db, "llm_prompt_upgrade", payload.upgrade_prompt.strip() or None)
    _set_config(db, "llm_prompt_eol_status", payload.eol_status_prompt.strip() or None)
    db.commit()
    from ..services.llm_advice import _prompt_cache
    _prompt_cache.clear()
    log_event("admin.llm_prompts.updated")
    return {"ok": True}


@router.get("/admin/eol")
def get_eol_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    row = db.query(ConfigKV).filter(ConfigKV.key == "eol_api_url").one_or_none()
    return {"url": (row.value if row else None)}


@router.post("/admin/eol")
def set_eol_config(
    url: str = Form(""),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    key = "eol_api_url"
    row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
    if row is None:
        db.add(ConfigKV(key=key, value=(url or None)))
    else:
        row.value = (url or None)
    db.commit()
    return {"ok": True, "url": (url or None)}


@router.get("/admin/security-thresholds")
def get_security_thresholds_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get security status color thresholds from database."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    def getv(k: str, default: str) -> str:
        row = db.query(ConfigKV).filter(ConfigKV.key == k).one_or_none()
        return row.value if row and row.value else default
    
    return {
        "red": {
            "riskFactor": int(getv("security_red_risk_factor", os.getenv("SECURITY_RED_RISK_FACTOR", "10"))),
            "critical": int(getv("security_red_critical", os.getenv("SECURITY_RED_CRITICAL", "0"))),
        },
        "orange": {
            "riskFactorMin": int(getv("security_orange_risk_factor_min", os.getenv("SECURITY_ORANGE_RISK_FACTOR_MIN", "5"))),
            "riskFactorMax": int(getv("security_orange_risk_factor_max", os.getenv("SECURITY_ORANGE_RISK_FACTOR_MAX", "10"))),
            "critical": int(getv("security_orange_critical", os.getenv("SECURITY_ORANGE_CRITICAL", "0"))),
        },
        "green": {
            "riskFactor": int(getv("security_green_risk_factor", os.getenv("SECURITY_GREEN_RISK_FACTOR", "5"))),
            "critical": int(getv("security_green_critical", os.getenv("SECURITY_GREEN_CRITICAL", "0"))),
        },
    }


@router.post("/admin/security-thresholds")
def set_security_thresholds_config(
    red_risk_factor: int = Form(10),
    red_critical: int = Form(0),
    orange_risk_factor_min: int = Form(5),
    orange_risk_factor_max: int = Form(10),
    orange_critical: int = Form(0),
    green_risk_factor: int = Form(5),
    green_critical: int = Form(0),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Save security status color thresholds to database."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    def upsert(key: str, value: str):
        row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if row is None:
            row = ConfigKV(key=key, value=value)
            db.add(row)
        else:
            row.value = value
    
    upsert("security_red_risk_factor", str(red_risk_factor))
    upsert("security_red_critical", str(red_critical))
    upsert("security_orange_risk_factor_min", str(orange_risk_factor_min))
    upsert("security_orange_risk_factor_max", str(orange_risk_factor_max))
    upsert("security_orange_critical", str(orange_critical))
    upsert("security_green_risk_factor", str(green_risk_factor))
    upsert("security_green_critical", str(green_critical))
    
    db.commit()
    log_event("security_thresholds.updated", 
              red_rf=red_risk_factor, red_crit=red_critical,
              orange_rf_min=orange_risk_factor_min, orange_rf_max=orange_risk_factor_max, orange_crit=orange_critical,
              green_rf=green_risk_factor, green_crit=green_critical)
    return {"ok": True}


@router.get("/admin/latest-version-config")
def get_latest_version_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get latest version check configuration.
    
    Returns:
        mode: 'local' or 'primary'
        cache_hours: Cache duration in hours (0 = no cache)
    """
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    def getv(k: str, default: str) -> str:
        row = db.query(ConfigKV).filter(ConfigKV.key == k).one_or_none()
        return row.value if row and row.value else default
    
    return {
        "mode": getv("latest_version_check_mode", os.getenv("LATEST_VERSION_CHECK_MODE", "local")),
        "cache_hours": int(getv("latest_version_cache_hours", os.getenv("LATEST_VERSION_CACHE_HOURS", "12"))),
    }


@router.post("/admin/latest-version-config")
def set_latest_version_config(
    mode: str = Form("local"),
    cache_hours: int = Form(12),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Set latest version check configuration.
    
    Args:
        mode: 'local' (each backend fetches directly) or 'primary' (secondary backends use primary)
        cache_hours: Cache duration in hours (0-24, 0 = no cache)
    """
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if mode not in ["local", "primary"]:
        raise HTTPException(status_code=400, detail="Mode must be 'local' or 'primary'")
    
    if cache_hours < 0 or cache_hours > 24:
        raise HTTPException(status_code=400, detail="Cache hours must be between 0 and 24")
    
    def upsert(key: str, value: str):
        row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if row is None:
            row = ConfigKV(key=key, value=value)
            db.add(row)
        else:
            row.value = value
    
    upsert("latest_version_check_mode", mode)
    upsert("latest_version_cache_hours", str(cache_hours))
    
    db.commit()
    log_event("latest_version_config.updated", mode=mode, cache_hours=cache_hours)
    return {"ok": True, "mode": mode, "cache_hours": cache_hours}


@router.post("/admin/users")
def create_user(username: str, role: str, password: Optional[str] = None, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    current_role = _decode_claims(token)["role"]
    if current_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    if db.query(User).filter(User.username == username).one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")
    role_row = db.query(Role).filter(Role.name == role).one_or_none()
    if not role_row:
        raise HTTPException(status_code=400, detail="Role not found")
    pwd = password or secrets.token_urlsafe(10)
    user = User(username=username, password_hash=hash_password(pwd), role_id=role_row.id, password_must_change=True)
    db.add(user)
    db.commit()
    return {"ok": True, "username": username, "role": role, "password": pwd}


@router.put("/admin/users/{username}/role")
def update_user_role(username: str, role: str, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    current_role = _decode_role(token)
    if current_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user = db.query(User).filter(User.username == username).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    role_row = db.query(Role).filter(Role.name == role).one_or_none()
    if not role_row:
        raise HTTPException(status_code=400, detail="Role not found")
    user.role_id = role_row.id
    db.commit()
    return {"ok": True}


@router.delete("/admin/users/{username}")
def delete_user(username: str, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    current_role = _decode_role(token)
    if current_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user = db.query(User).filter(User.username == username).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/backend-endpoints")
def list_backend_endpoints_public(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[dict]:
    """
    Public endpoint: Returns backend configurations for ALL authenticated users.
    Used by frontend to fetch resources from multiple backends.
    Does NOT include sensitive information (auth tokens).
    """
    endpoints = db.query(BackendEndpoint).filter(
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).order_by(BackendEndpoint.is_default.desc(), BackendEndpoint.name).all()
    
    return [
        {
            "id": e.id,
            "name": e.name,
            "platform": e.platform,
            "api_url": e.api_url,
            "enabled": e.enabled,
            "is_default": e.is_default,
            "skip_tls_verify": e.skip_tls_verify,
            "approved": e.approved,  # Needed by frontend sync modal
        }
        for e in endpoints
    ]


@router.get("/admin/backend-endpoints")
def list_backend_endpoints(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[dict]:
    """
    Admin-only endpoint: Returns full backend configurations including sensitive data.
    """
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    endpoints = db.query(BackendEndpoint).order_by(BackendEndpoint.is_default.desc(), BackendEndpoint.name).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "platform": e.platform,
            "api_url": e.api_url,
            "enabled": e.enabled,
            "is_default": e.is_default,
            "skip_tls_verify": e.skip_tls_verify,
            "approved": e.approved,
            "approved_by": e.approved_by,
            "approved_at": e.approved_at,
            "last_seen_at": e.last_seen_at,
            "created_at": e.created_at,
            "auto_sync_schedule": e.auto_sync_schedule,  # Include schedule in response
            "latest_version_mode": e.latest_version_mode,  # Per-backend latest version check mode
            "twistlock_mode": e.twistlock_mode,  # Per-backend Twistlock query mode
            "trivy_mode": e.trivy_mode,  # Per-backend Trivy query mode
            "eol_mode": e.eol_mode,  # Per-backend EOL query mode
            "llm_mode": getattr(e, 'llm_mode', None),  # Per-backend LLM query mode
            "ldap_mode": getattr(e, 'ldap_mode', 'primary') or 'primary',  # LDAP auth mode
            "backend_type": e.backend_type,  # 'primary' or 'secondary'
            "auth_token_preview": e.auth_token[:8] + "..." if e.auth_token else ""  # Show first 8 chars only
        }
        for e in endpoints
    ]


@router.post("/admin/backend-endpoints")
def create_backend_endpoint(
    name: str = Form(...),
    platform: str = Form(...),
    api_url: str = Form(...),
    enabled: bool = Form(True),
    skip_tls_verify: bool = Form(False),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Check if name already exists
    existing = db.query(BackendEndpoint).filter(BackendEndpoint.name == name).one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Backend name already exists")
    
    # Generate unique auth token
    auth_token = secrets.token_urlsafe(32)
    
    # Get username from token
    claims = _decode_claims(token)
    username = claims.get("sub", "admin")
    
    endpoint = BackendEndpoint(
        name=name,
        platform=platform,
        api_url=api_url.rstrip("/"),
        auth_token=auth_token,
        enabled=enabled,
        is_default=False,
        skip_tls_verify=skip_tls_verify,
        approved=False,  # Pending until backend connects with token
        approved_by=username  # Who created this
    )
    db.add(endpoint)
    db.commit()
    return {"ok": True, "id": endpoint.id, "auth_token": auth_token}


@router.put("/admin/backend-endpoints/{endpoint_id}")
def update_backend_endpoint(
    endpoint_id: int,
    enabled: bool = Form(None),
    skip_tls_verify: bool = Form(None),
    approved: bool = Form(None),
    latest_version_mode: str = Form(None),
    twistlock_mode: str = Form(None),
    trivy_mode: str = Form(None),
    eol_mode: str = Form(None),
    llm_mode: str = Form(None),
    ldap_mode: str = Form(None),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    endpoint = db.query(BackendEndpoint).filter(BackendEndpoint.id == endpoint_id).one_or_none()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Backend not found")
    
    if enabled is not None:
        endpoint.enabled = enabled
    if skip_tls_verify is not None:
        endpoint.skip_tls_verify = skip_tls_verify
    if approved is not None:
        endpoint.approved = approved
        if approved:
            from datetime import datetime, timezone
            claims = _decode_claims(token)
            endpoint.approved_by = claims.get("sub", "admin")
            endpoint.approved_at = datetime.now(timezone.utc)
    if latest_version_mode is not None:
        if latest_version_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="latest_version_mode must be 'local', 'primary', or empty (use global)")
        endpoint.latest_version_mode = latest_version_mode if latest_version_mode else None
        log_event("backend.latest_version_mode.updated", 
                 backend=endpoint.name, mode=latest_version_mode or "global")
    if twistlock_mode is not None:
        if twistlock_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="twistlock_mode must be 'local', 'primary', or empty (use global)")
        endpoint.twistlock_mode = twistlock_mode if twistlock_mode else None
        log_event("backend.twistlock_mode.updated", 
                 backend=endpoint.name, mode=twistlock_mode or "global")
    if trivy_mode is not None:
        if trivy_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="trivy_mode must be 'local', 'primary', or empty (use global)")
        endpoint.trivy_mode = trivy_mode if trivy_mode else None
        log_event("backend.trivy_mode.updated",
                 backend=endpoint.name, mode=trivy_mode or "global")
    if eol_mode is not None:
        if eol_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="eol_mode must be 'local', 'primary', or empty (use global)")
        endpoint.eol_mode = eol_mode if eol_mode else None
        log_event("backend.eol_mode.updated", 
                 backend=endpoint.name, mode=eol_mode or "global")
    if llm_mode is not None:
        if llm_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="llm_mode must be 'local', 'primary', or empty (use global)")
        endpoint.llm_mode = llm_mode if llm_mode else None
        log_event("backend.llm_mode.updated",
                 backend=endpoint.name, mode=llm_mode or "global")
    if ldap_mode is not None:
        # Validate mode value
        if ldap_mode not in ["local", "primary", ""]:
            raise HTTPException(status_code=400, detail="ldap_mode must be 'local' or 'primary'")
        endpoint.ldap_mode = ldap_mode if ldap_mode else "primary"
        log_event("backend.ldap_mode.updated", 
                 backend=endpoint.name, mode=ldap_mode or "primary")
    db.commit()
    return {"ok": True}


@router.delete("/admin/backend-endpoints/{endpoint_id}")
def delete_backend_endpoint(
    endpoint_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    endpoint = db.query(BackendEndpoint).filter(BackendEndpoint.id == endpoint_id).one_or_none()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Backend not found")
    
    if endpoint.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete default backend")
    
    # Get the platform name before deleting
    platform_name = endpoint.platform
    backend_name = endpoint.name
    
    # Cascade delete all related data
    try:
        # Log warning about remote backend resources (if this is a federation setup)
        if not endpoint.is_default and endpoint.api_url:
            log_event("backend.delete.remote_warning", 
                     backend=backend_name, 
                     message="Remote backend resources may still exist in its own database")
        
        # 1. Delete sync logs for this backend
        sync_logs = db.query(SyncLog).filter(SyncLog.triggered_by == backend_name).all()
        for log in sync_logs:
            db.delete(log)
        
        # 2. Get all resources for this platform (from local/primary database)
        resources = db.query(Resource).filter(Resource.platform == platform_name).all()
        resource_ids = [r.id for r in resources]
        
        # 3. Count and delete resource plans for these resources
        plans_deleted = 0
        if resource_ids:
            plans_deleted = db.query(ResourcePlan).filter(ResourcePlan.resource_id.in_(resource_ids)).count()
            db.query(ResourcePlan).filter(ResourcePlan.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        
        # 4. Count and delete resource history for these resources
        history_deleted = 0
        if resource_ids:
            history_deleted = db.query(ResourceHistory).filter(ResourceHistory.resource_id.in_(resource_ids)).count()
            db.query(ResourceHistory).filter(ResourceHistory.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        
        # 5. Delete the resources themselves
        for resource in resources:
            db.delete(resource)
        
        # 6. Finally, delete the backend endpoint
        db.delete(endpoint)
        
        db.commit()
        
        message = f"Backend '{backend_name}' and all associated data deleted successfully"
        
        # Warn about remote backend resources if this is not the default backend
        if not endpoint.is_default and endpoint.api_url:
            message += f"\n\n⚠️ WARNING: This was a remote backend. Resources may still exist in its own database at {endpoint.api_url}. To fully clean up, either:\n1. Delete resources from the remote backend before removing it, OR\n2. Stop the remote backend pod/deployment"
        
        return {
            "ok": True,
            "message": message,
            "deleted": {
                "sync_logs": len(sync_logs),
                "resources_local": len(resources),
                "resource_plans": plans_deleted,
                "resource_history": history_deleted
            }
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete backend: {str(e)}")


@router.post("/admin/users/{username}/reset-password")
def reset_password(username: str, password: Optional[str] = None, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    claims = _decode_claims(token)
    current_role = claims["role"]
    if current_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    user = db.query(User).filter(User.username == username).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_pwd = password or secrets.token_urlsafe(10)
    if len(new_pwd) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    user.password_hash = hash_password(new_pwd)
    # Force password change on next login
    if hasattr(user, "password_must_change"):
        user.password_must_change = True
    db.commit()
    return {"ok": True, "password": new_pwd}


@router.post("/auth/reset-password")
def self_reset_password(
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    claims = _decode_claims(token)
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if len(new_password) < 8 or len(new_password) > 256:
        raise HTTPException(status_code=400, detail="Password length must be 8-256 characters")
    user = db.query(User).filter(User.username == claims["username"]).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(new_password)
    # Clear must-change flag after a successful self reset
    if hasattr(user, "password_must_change"):
        user.password_must_change = False
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# User Settings (theme, etc.) - accessible to all authenticated users
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auth/settings")
def get_user_settings(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Get current user's settings (theme, etc.)"""
    claims = _decode_claims(token)
    user = db.query(User).filter(User.username == claims["username"]).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "theme": getattr(user, "theme", "dark") or "dark",
        "font_size": getattr(user, "font_size", "medium") or "medium",
        "username": user.username
    }


@router.post("/auth/settings")
def update_user_settings(
    theme: Optional[str] = Form(None),
    font_size: Optional[str] = Form(None),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Update current user's settings"""
    claims = _decode_claims(token)
    user = db.query(User).filter(User.username == claims["username"]).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    valid_themes = ["dark", "light", "ocean", "forest", "sunset", 
                    "soft-ivory", "minimal-gray", "paper-white", "light-warm", "high-contrast"]
    if theme is not None:
        if theme not in valid_themes:
            raise HTTPException(status_code=400, detail=f"Invalid theme. Valid: {', '.join(valid_themes)}")
        if hasattr(user, "theme"):
            user.theme = theme
    
    valid_font_sizes = ["small", "medium", "large", "xlarge"]
    if font_size is not None:
        if font_size not in valid_font_sizes:
            raise HTTPException(status_code=400, detail=f"Invalid font_size. Valid: {', '.join(valid_font_sizes)}")
        if hasattr(user, "font_size"):
            user.font_size = font_size
    
    db.commit()
    return {
        "ok": True,
        "theme": getattr(user, "theme", "dark"),
        "font_size": getattr(user, "font_size", "medium"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LDAP/AD Integration
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/ldap")
def get_ldap_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get LDAP configuration (admin only)."""
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = db.query(LdapConfig).first()
    if not config:
        return {
            "enabled": False,
            "server_url": "",
            "port": 636,
            "use_ssl": True,
            "ca_cert": "",
            "skip_cert_verify": False,
            "bind_dn": "",
            "bind_password": "",
            "base_dn": "",
            "user_search_filter": "(&(objectClass=user)(sAMAccountName={username}))",
            "user_search_base": "",
            "username_attribute": "sAMAccountName",
            "email_attribute": "mail",
            "display_name_attribute": "displayName",
            "group_search_base": "",
            "group_search_filter": "",
            "admin_group_dn": "",
            "analyst_group_dn": "",
            "readonly_group_dn": ""
        }
    
    return {
        "enabled": config.enabled,
        "server_url": config.server_url or "",
        "port": config.port,
        "use_ssl": config.use_ssl,
        "ca_cert": config.ca_cert or "",
        "skip_cert_verify": getattr(config, 'skip_cert_verify', False),
        "bind_dn": config.bind_dn or "",
        "bind_password": "••••••••" if config.bind_password else "",  # Mask password
        "base_dn": config.base_dn or "",
        "user_search_filter": config.user_search_filter or "(&(objectClass=user)(sAMAccountName={username}))",
        "user_search_base": config.user_search_base or "",
        "username_attribute": config.username_attribute,
        "email_attribute": config.email_attribute,
        "display_name_attribute": config.display_name_attribute,
        "group_search_base": config.group_search_base or "",
        "group_search_filter": config.group_search_filter or "",
        "admin_group_dn": config.admin_group_dn or "",
        "analyst_group_dn": config.analyst_group_dn or "",
        "readonly_group_dn": config.readonly_group_dn or ""
    }


@router.post("/admin/ldap")
def save_ldap_config(
    enabled: bool = Form(False),
    server_url: str = Form(""),
    port: int = Form(636),
    use_ssl: bool = Form(True),
    ca_cert: str = Form(""),
    skip_cert_verify: bool = Form(False),
    bind_dn: str = Form(""),
    bind_password: str = Form(""),
    base_dn: str = Form(""),
    user_search_filter: str = Form("(&(objectClass=user)(sAMAccountName={username}))"),
    user_search_base: str = Form(""),
    username_attribute: str = Form("sAMAccountName"),
    email_attribute: str = Form("mail"),
    display_name_attribute: str = Form("displayName"),
    group_search_base: str = Form(""),
    group_search_filter: str = Form(""),
    admin_group_dn: str = Form(""),
    analyst_group_dn: str = Form(""),
    readonly_group_dn: str = Form(""),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Save LDAP configuration (admin only)."""
    from datetime import datetime, timezone
    
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = db.query(LdapConfig).first()
    if not config:
        config = LdapConfig()
        db.add(config)
    
    config.enabled = enabled
    config.server_url = server_url or None
    config.port = port
    config.use_ssl = use_ssl
    config.ca_cert = ca_cert or None
    config.skip_cert_verify = skip_cert_verify
    config.bind_dn = bind_dn or None
    # Only update password if not masked value
    if bind_password and bind_password != "••••••••":
        config.bind_password = bind_password
    config.base_dn = base_dn or None
    config.user_search_filter = user_search_filter or None
    config.user_search_base = user_search_base or None
    config.username_attribute = username_attribute or "sAMAccountName"
    config.email_attribute = email_attribute or "mail"
    config.display_name_attribute = display_name_attribute or "displayName"
    config.group_search_base = group_search_base or None
    config.group_search_filter = group_search_filter or None
    config.admin_group_dn = admin_group_dn or None
    config.analyst_group_dn = analyst_group_dn or None
    config.readonly_group_dn = readonly_group_dn or None
    config.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    log_event("ldap.config.saved", enabled=enabled)
    return {"ok": True}


@router.post("/admin/ldap/test-connection")
def test_ldap_connection(
    server_url: str = Form(...),
    port: int = Form(636),
    use_ssl: bool = Form(True),
    ca_cert: str = Form(""),
    skip_cert_verify: bool = Form(False),
    bind_dn: str = Form(...),
    bind_password: str = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Test LDAP connection with provided credentials."""
    from ..services.ldap_service import test_ldap_connection as ldap_test
    
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # If password is masked, get from saved config
    config = db.query(LdapConfig).first()
    if bind_password == "••••••••":
        if config and config.bind_password:
            bind_password = config.bind_password
        else:
            return {"success": False, "message": "No saved password found"}
    
    # Use saved CA cert if not provided
    if not ca_cert and config and config.ca_cert:
        ca_cert = config.ca_cert
    
    success, message = ldap_test(server_url, port, use_ssl, bind_dn, bind_password, ca_cert or None, skip_cert_verify)
    return {"success": success, "message": message}


@router.post("/admin/ldap/detect-base-dn")
def detect_ldap_base_dn(
    server_url: str = Form(...),
    port: int = Form(636),
    use_ssl: bool = Form(True),
    ca_cert: str = Form(""),
    skip_cert_verify: bool = Form(False),
    bind_dn: str = Form(...),
    bind_password: str = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Auto-detect base DN from LDAP server."""
    from ..services.ldap_service import detect_base_dn as ldap_detect
    
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = db.query(LdapConfig).first()
    if bind_password == "••••••••":
        if config and config.bind_password:
            bind_password = config.bind_password
        else:
            return {"success": False, "base_dn": "", "message": "No saved password found"}
    
    if not ca_cert and config and config.ca_cert:
        ca_cert = config.ca_cert
    
    success, result = ldap_detect(server_url, port, use_ssl, bind_dn, bind_password, ca_cert or None, skip_cert_verify)
    if success:
        return {"success": True, "base_dn": result, "message": "Base DN detected"}
    return {"success": False, "base_dn": "", "message": result}


@router.post("/admin/ldap/test-base-dn")
def test_ldap_base_dn(
    server_url: str = Form(...),
    port: int = Form(636),
    use_ssl: bool = Form(True),
    ca_cert: str = Form(""),
    skip_cert_verify: bool = Form(False),
    bind_dn: str = Form(...),
    bind_password: str = Form(...),
    base_dn: str = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Test if base DN is valid."""
    from ..services.ldap_service import test_base_dn as ldap_test_base
    
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = db.query(LdapConfig).first()
    if bind_password == "••••••••":
        if config and config.bind_password:
            bind_password = config.bind_password
        else:
            return {"success": False, "message": "No saved password found"}
    
    if not ca_cert and config and config.ca_cert:
        ca_cert = config.ca_cert
    
    success, message = ldap_test_base(server_url, port, use_ssl, bind_dn, bind_password, base_dn, ca_cert or None, skip_cert_verify)
    return {"success": success, "message": message}


@router.post("/admin/ldap/test-user-login")
def test_ldap_user_login(
    test_username: str = Form(...),
    test_password: str = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Test LDAP user authentication without actually logging in."""
    from ..services.ldap_service import authenticate_user, determine_role_from_groups
    
    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = db.query(LdapConfig).first()
    if not config or not config.enabled:
        return {"success": False, "message": "LDAP is not enabled", "user_info": None}
    
    if not config.server_url or not config.bind_dn or not config.bind_password:
        return {"success": False, "message": "LDAP not properly configured", "user_info": None}
    
    success, ldap_user, message = authenticate_user(
        server_url=config.server_url,
        port=config.port,
        use_ssl=config.use_ssl,
        bind_dn=config.bind_dn,
        bind_password=config.bind_password,
        base_dn=config.base_dn or "",
        user_search_filter=config.user_search_filter or "(&(objectClass=user)(sAMAccountName={username}))",
        user_search_base=config.user_search_base,
        username=test_username,
        password=test_password,
        username_attribute=config.username_attribute,
        email_attribute=config.email_attribute,
        display_name_attribute=config.display_name_attribute,
        ca_cert=config.ca_cert,
        skip_cert_verify=getattr(config, 'skip_cert_verify', False)
    )
    
    if not success or not ldap_user:
        return {"success": False, "message": message, "user_info": None}
    
    # Determine role from groups
    role_name = determine_role_from_groups(
        ldap_user.groups,
        config.admin_group_dn,
        config.analyst_group_dn,
        config.readonly_group_dn
    )
    
    return {
        "success": True,
        "message": "Authentication successful",
        "user_info": {
            "username": ldap_user.username,
            "email": ldap_user.email,
            "display_name": ldap_user.display_name,
            "groups": ldap_user.groups[:10] if ldap_user.groups else [],  # Limit groups shown
            "assigned_role": role_name
        }
    }


@router.post("/auth/ldap-proxy")
def ldap_proxy_auth(
    username: str = Form(...),
    password: str = Form(...),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db)
) -> dict:
    """LDAP proxy endpoint for secondary backends.
    
    Secondary backends can proxy LDAP auth requests to primary.
    Requires X-Backend-Token header for authentication.
    """
    from ..services.ldap_service import authenticate_user, determine_role_from_groups
    from ..logging_utils import log_event
    
    # Verify backend token
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing backend token")
    
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).first()
    
    if not backend:
        raise HTTPException(status_code=401, detail="Invalid backend token")
    
    # Check if LDAP is enabled on this (primary) backend
    ldap_config = db.query(LdapConfig).first()
    if not ldap_config or not ldap_config.enabled:
        return {"success": False, "message": "LDAP not enabled on primary", "user_info": None}
    
    if not ldap_config.server_url or not ldap_config.bind_dn or not ldap_config.bind_password:
        return {"success": False, "message": "LDAP not properly configured on primary", "user_info": None}
    
    # Attempt LDAP authentication
    success, ldap_user, message = authenticate_user(
        server_url=ldap_config.server_url,
        port=ldap_config.port,
        use_ssl=ldap_config.use_ssl,
        bind_dn=ldap_config.bind_dn,
        bind_password=ldap_config.bind_password,
        base_dn=ldap_config.base_dn or "",
        user_search_filter=ldap_config.user_search_filter or "(&(objectClass=user)(sAMAccountName={username}))",
        user_search_base=ldap_config.user_search_base,
        username=username,
        password=password,
        username_attribute=ldap_config.username_attribute,
        email_attribute=ldap_config.email_attribute,
        display_name_attribute=ldap_config.display_name_attribute,
        ca_cert=ldap_config.ca_cert,
        skip_cert_verify=getattr(ldap_config, 'skip_cert_verify', False)
    )
    
    if not success or not ldap_user:
        log_event("ldap.proxy.auth_failed", username=username, backend=backend.name, message=message)
        return {"success": False, "message": message, "user_info": None}
    
    # Determine role from groups
    role_name = determine_role_from_groups(
        ldap_user.groups,
        ldap_config.admin_group_dn,
        ldap_config.analyst_group_dn,
        ldap_config.readonly_group_dn
    )
    
    log_event("ldap.proxy.auth_success", username=username, backend=backend.name, role=role_name)
    
    return {
        "success": True,
        "message": "Authentication successful",
        "user_info": {
            "username": ldap_user.username,
            "email": ldap_user.email,
            "display_name": ldap_user.display_name,
            "role": role_name
        }
    }


def _ensure_roles(db: Session) -> None:
    # idempotent role seeding
    if not db.query(Role).filter(Role.name == "admin").one_or_none():
        db.add(Role(name="admin"))
    if not db.query(Role).filter(Role.name == "analyst").one_or_none():
        db.add(Role(name="analyst"))
    if not db.query(Role).filter(Role.name == "read-only").one_or_none():
        db.add(Role(name="read-only"))
    db.commit()


@router.get("/auth/setup-state")
def setup_state(db: Session = Depends(get_db)) -> dict:
    _ensure_roles(db)
    admin_role = db.query(Role).filter(Role.name == "admin").one_or_none()
    if not admin_role:
        # Create admin role if seeding failed previously
        db.add(Role(name="admin"))
        db.commit()
        admin_role = db.query(Role).filter(Role.name == "admin").one()
    has_admin = db.query(User).filter(User.role_id == admin_role.id).first() is not None
    return {"setup_required": not has_admin}


@router.post("/auth/setup-admin")
def setup_admin(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    # Only allow when no admin exists
    _ensure_roles(db)
    admin_role = db.query(Role).filter(Role.name == "admin").one()
    has_admin = db.query(User).filter(User.role_id == admin_role.id).first() is not None
    if has_admin:
        raise HTTPException(status_code=400, detail="Admin already configured")
    if db.query(User).filter(User.username == username).one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")
    # enforce sane password limits to avoid backend limitations
    if len(password.encode("utf-8")) > 256 or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password length must be 8-256 characters")
    user = User(username=username, password_hash=hash_password(password), role_id=admin_role.id)
    db.add(user)
    db.commit()
    return {"ok": True}


@router.get("/admin/auto-sync")
def get_auto_sync_config(
    backend_name: str = None,  # Optional query param: ?backend_name=poc-backend
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # If backend_name not provided, use THIS backend's config
    if not backend_name:
        import os
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    
    schedule = backend.auto_sync_schedule if backend.auto_sync_schedule else ""
    return {"interval_seconds": schedule, "backend_name": backend_name}  # Return as string (cron or seconds)


@router.post("/admin/auto-sync")
def save_auto_sync_config(
    interval_seconds: str = Form(...),
    backend_name: str = Form(None),  # Optional: if not provided, use current backend
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # If backend_name not provided, use THIS backend's config
    if not backend_name:
        import os
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    
    # Handle schedule update/removal
    schedule_value = interval_seconds.strip() if interval_seconds else ""
    if schedule_value in ("0", ""):
        # Remove schedule (set to None)
        backend.auto_sync_schedule = None
        message = f"Auto-sync schedule removed for backend '{backend_name}'."
    else:
        # Set/update schedule
        backend.auto_sync_schedule = schedule_value
        message = f"Auto-sync schedule updated for backend '{backend_name}' to '{schedule_value}'. Changes will apply within 1 minute."
    
    db.commit()
    return {"ok": True, "message": message}


@router.get("/admin/sync-force")
def get_sync_force_config(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    # Read from THIS backend's config
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    enabled = backend.force_sync_enabled if backend else False
    return {"enabled": enabled}


@router.post("/admin/sync-force")
def save_sync_force_config(
    enabled: bool = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    # Save to THIS backend's config
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    if not backend:
        raise HTTPException(status_code=404, detail="Backend not found")
    backend.force_sync_enabled = enabled
    db.commit()
    return {"ok": True, "enabled": enabled}


@router.get("/admin/cleanup-force")
def get_cleanup_force_config(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Read the per-backend cleanup-force toggle.

    When enabled, the sync cleanup phase bypasses the 10% mass-delete safety
    guard. Use ONLY when you have intentionally shrunk the source list and
    accept that many existing resources will be removed.
    """
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    enabled = bool(getattr(backend, "cleanup_force_enabled", False)) if backend else False
    return {"enabled": enabled}


@router.post("/admin/cleanup-force")
def save_cleanup_force_config(
    enabled: bool = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Toggle the per-backend cleanup-force flag (admin only)."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    if not backend:
        raise HTTPException(status_code=404, detail="Backend not found")
    backend.cleanup_force_enabled = enabled
    db.commit()
    log_event("admin.cleanup_force.toggled", backend=backend_name, enabled=enabled)
    return {"ok": True, "enabled": enabled}


# ===== SMTP Configuration =====

@router.get("/admin/smtp")
def get_smtp_config(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get SMTP configuration."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = {}
    for key in ["smtp_server", "smtp_port", "smtp_from_email", "smtp_username", "smtp_use_tls", "smtp_schedule"]:
        kv = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if kv and kv.value:
            config[key] = kv.value
    
    # Don't return password for security
    return config


@router.post("/admin/smtp")
def save_smtp_config(
    smtp_server: str = Form(...),
    smtp_port: int = Form(...),
    smtp_from_email: str = Form(...),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_use_tls: bool = Form(True),
    smtp_schedule: str = Form(""),  # Cron expression for scheduled emails
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Save SMTP configuration."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    configs = {
        "smtp_server": smtp_server,
        "smtp_port": str(smtp_port),
        "smtp_from_email": smtp_from_email,
        "smtp_username": smtp_username,
        "smtp_use_tls": "true" if smtp_use_tls else "false",
        "smtp_schedule": smtp_schedule
    }
    
    # Only update password if provided
    if smtp_password:
        configs["smtp_password"] = smtp_password
    
    for key, value in configs.items():
        kv = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if kv is None:
            kv = ConfigKV(key=key)
            db.add(kv)
        kv.value = value
    
    db.commit()
    return {"ok": True, "message": "SMTP configuration saved successfully"}


@router.post("/admin/smtp/test")
def test_smtp(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Test SMTP connection with current configuration."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Load config
    server_kv = db.query(ConfigKV).filter(ConfigKV.key == "smtp_server").one_or_none()
    port_kv = db.query(ConfigKV).filter(ConfigKV.key == "smtp_port").one_or_none()
    username_kv = db.query(ConfigKV).filter(ConfigKV.key == "smtp_username").one_or_none()
    password_kv = db.query(ConfigKV).filter(ConfigKV.key == "smtp_password").one_or_none()
    tls_kv = db.query(ConfigKV).filter(ConfigKV.key == "smtp_use_tls").one_or_none()
    
    if not server_kv or not port_kv:
        raise HTTPException(status_code=400, detail="SMTP server and port must be configured")
    
    success, message = test_smtp_connection(
        smtp_server=server_kv.value,
        smtp_port=int(port_kv.value),
        username=username_kv.value if username_kv else None,
        password=password_kv.value if password_kv else None,
        use_tls=(tls_kv.value == "true") if tls_kv else True
    )
    
    return {"ok": success, "message": message}


# ===== Compare Settings =====

@router.get("/admin/compare-settings")
def get_compare_settings(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get compare settings."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    config = {}
    for key in ["max_comparison_reports"]:
        kv = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if kv and kv.value:
            config[key] = kv.value
        else:
            # Default values
            if key == "max_comparison_reports":
                config[key] = "20"
    
    return config


@router.post("/admin/compare-settings")
def save_compare_settings(
    max_comparison_reports: int = Form(20),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Save compare settings."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Validate range
    if max_comparison_reports < 1:
        max_comparison_reports = 1
    if max_comparison_reports > 100:
        max_comparison_reports = 100
    
    configs = {
        "max_comparison_reports": str(max_comparison_reports)
    }
    
    for key, value in configs.items():
        kv = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if kv:
            kv.value = value
        else:
            kv = ConfigKV(key=key, value=value)
            db.add(kv)
    
    db.commit()
    log_event("admin.compare_settings.saved", max_comparison_reports=max_comparison_reports)
    return {"message": "Compare settings saved"}


# ===== Email Recipients =====

@router.get("/admin/email-recipients")
def list_email_recipients(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[dict]:
    """List all email recipients."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    recipients = db.query(EmailRecipient).order_by(EmailRecipient.email).all()
    return [
        {
            "id": r.id,
            "email": r.email,
            "name": r.name,
            "enabled": r.enabled,
            "created_at": r.created_at
        }
        for r in recipients
    ]


@router.post("/admin/email-recipients")
def create_email_recipient(
    email: str = Form(...),
    name: str = Form(""),
    enabled: bool = Form(True),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Add a new email recipient."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Check if email already exists
    existing = db.query(EmailRecipient).filter(EmailRecipient.email == email).one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")
    
    recipient = EmailRecipient(
        email=email,
        name=name if name else None,
        enabled=enabled
    )
    db.add(recipient)
    db.commit()
    return {"ok": True, "id": recipient.id}


@router.put("/admin/email-recipients/{recipient_id}")
def update_email_recipient(
    recipient_id: int,
    enabled: bool = Form(None),
    name: str = Form(None),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Update email recipient."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    recipient = db.query(EmailRecipient).filter(EmailRecipient.id == recipient_id).one_or_none()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    if enabled is not None:
        recipient.enabled = enabled
    if name is not None:
        recipient.name = name if name else None
    
    db.commit()
    return {"ok": True}


@router.delete("/admin/email-recipients/{recipient_id}")
def delete_email_recipient(
    recipient_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Delete email recipient."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    recipient = db.query(EmailRecipient).filter(EmailRecipient.id == recipient_id).one_or_none()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    db.delete(recipient)
    db.commit()
    return {"ok": True}


# ============================================================================
# Product Responsibility Management
# ============================================================================

@router.get("/admin/products/list")
def get_product_list(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Get all products from product_list_table"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    products = db.query(ProductList).order_by(ProductList.product_name).all()
    return {
        "products": [{"id": p.id, "name": p.product_name, "updated_at": str(p.updated_at)} for p in products],
        "count": len(products)
    }


@router.post("/admin/products/refresh")
def refresh_products(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Manually refresh product list from endoflife.date API"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from ..services.product_service import refresh_product_list
    
    result = refresh_product_list(db)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to refresh"))
    
    return result


@router.get("/admin/products/managed")
def get_managed_products(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Get products that this backend is responsible for"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Get this backend's name
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Only return products for THIS backend
    products = db.query(ManagedProduct).filter(
        ManagedProduct.backend_name == backend_name
    ).order_by(ManagedProduct.product_name).all()
    
    return {
        "products": [
            {
                "id": p.id,
                "name": p.product_name,
                "backend": p.backend_name,
                "is_manual": p.is_manual,
                "image_pattern": p.image_pattern or "",
                "added_by": p.added_by,
                "created_at": str(p.created_at)
            }
            for p in products
        ],
        "count": len(products),
        "backend": backend_name
    }


@router.get("/admin/products/managed/export")
def export_managed_products(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """Export managed products as JSON file"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    import os
    import json
    from fastapi.responses import Response
    
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    products = db.query(ManagedProduct).filter(
        ManagedProduct.backend_name == backend_name
    ).order_by(ManagedProduct.product_name).all()
    
    export_data = {
        "version": "1.0",
        "backend": backend_name,
        "exported_at": str(datetime.now(timezone.utc)),
        "products": [p.product_name for p in products],
        "image_patterns": {p.product_name: p.image_pattern for p in products if p.image_pattern},
    }
    
    content = json.dumps(export_data, indent=2)
    
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=managed_products_{backend_name}.json"
        }
    )


@router.post("/admin/products/managed/import")
async def import_managed_products(
    file: UploadFile = File(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Import managed products from JSON file. Skips duplicates, adds new ones."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    import os
    import json
    from ..logging_utils import log_event
    
    claims = _decode_claims(token)
    username = claims.get("sub", "unknown")
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Read and parse file
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")
    
    # Validate structure
    if "products" not in data or not isinstance(data["products"], list):
        raise HTTPException(status_code=400, detail="Invalid format: 'products' array required")
    
    # Get existing products for this backend
    existing_products = {
        p.product_name 
        for p in db.query(ManagedProduct).filter(ManagedProduct.backend_name == backend_name).all()
    }
    
    # Import new products
    added = []
    skipped = []
    imported_patterns = data.get("image_patterns") or {}
    
    for product_name in data["products"]:
        if not isinstance(product_name, str) or not product_name.strip():
            continue
            
        product_name = product_name.strip()
        
        if product_name in existing_products:
            skipped.append(product_name)
            continue
        
        # Check if product exists in product_list_table
        in_product_list = db.query(ProductList).filter(ProductList.product_name == product_name).one_or_none()
        is_manual = in_product_list is None
        
        pattern = (imported_patterns.get(product_name) or "").strip() or None
        
        managed = ManagedProduct(
            backend_name=backend_name,
            product_name=product_name,
            image_pattern=pattern,
            is_manual=is_manual,
            added_by=f"{username} (import)"
        )
        db.add(managed)
        added.append(product_name)
        existing_products.add(product_name)
    
    db.commit()
    
    log_event(
        "products.import",
        backend=backend_name,
        imported_by=username,
        added_count=len(added),
        skipped_count=len(skipped),
    )
    
    # Trigger sync if any products were added
    sync_triggered = False
    if added:
        try:
            _trigger_sync_all_backends(db, token, f"import:{len(added)}_products")
            sync_triggered = True
        except Exception as e:
            log_event("products.import.sync_error", error=str(e))
    
    return {
        "ok": True,
        "added": added,
        "added_count": len(added),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "sync_triggered": sync_triggered,
    }


@router.post("/admin/products/managed")
def add_managed_product(
    product_name: str = Form(...),
    image_pattern: str = Form(""),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Add a product to managed list for this backend"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Get username and backend name
    claims = _decode_claims(token)
    username = claims.get("sub", "unknown")
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Check if already managed by THIS backend
    existing = db.query(ManagedProduct).filter(
        ManagedProduct.backend_name == backend_name,
        ManagedProduct.product_name == product_name
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Product already managed by backend '{backend_name}'")
    
    # Check if product exists in product_list_table
    in_product_list = db.query(ProductList).filter(ProductList.product_name == product_name).one_or_none()
    is_manual = in_product_list is None
    
    pattern = image_pattern.strip() or None
    
    # Add to managed list for THIS backend
    managed = ManagedProduct(
        backend_name=backend_name,
        product_name=product_name,
        image_pattern=pattern,
        is_manual=is_manual,
        added_by=username
    )
    db.add(managed)
    db.commit()

    from ..logging_utils import log_event
    log_event("product.added", product=product_name, backend=backend_name, added_by=username)

    # Let the real-time watch pick up the new product immediately (no 5-min wait)
    try:
        from ..services.kube_watch import reload_managed_patterns
        reload_managed_patterns()
    except Exception:
        pass

    # Trigger product-specific sync (fast, not full sync)
    sync_results = _trigger_product_sync_all_backends(db, product_name)
    
    return {
        "ok": True, 
        "id": managed.id, 
        "is_manual": is_manual, 
        "backend": backend_name,
        "sync_triggered": sync_results,
    }


@router.patch("/admin/products/managed/{product_id}")
def update_managed_product(
    product_id: int,
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Update image_pattern for a managed product."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    product = db.query(ManagedProduct).filter(ManagedProduct.id == product_id).one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    new_pattern = (body.get("image_pattern") or "").strip() or None
    product.image_pattern = new_pattern
    db.commit()
    log_event("product.updated", product=product.product_name, image_pattern=new_pattern or "")
    try:
        from ..services.kube_watch import reload_managed_patterns
        reload_managed_patterns()
    except Exception:
        pass
    return {"ok": True, "product": product.product_name, "image_pattern": new_pattern or ""}


@router.delete("/admin/products/managed/{product_id}")
def remove_managed_product(
    product_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Remove a product from managed list and delete its resources."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    product = db.query(ManagedProduct).filter(ManagedProduct.id == product_id).one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product_name = product.product_name
    
    resource_ids = [
        r.id for r in db.query(Resource.id).filter(Resource.product_name == product_name).all()
    ]
    deleted_resources = len(resource_ids)
    if resource_ids:
        db.query(ResourceHistory).filter(ResourceHistory.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        db.query(ResourcePlan).filter(ResourcePlan.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        db.query(Resource).filter(Resource.id.in_(resource_ids)).delete(synchronize_session=False)
    
    db.delete(product)
    db.commit()

    log_event("product.removed", product=product_name, resources_deleted=deleted_resources)
    try:
        from ..services.kube_watch import reload_managed_patterns
        reload_managed_patterns()
    except Exception:
        pass
    return {"ok": True, "product": product_name, "resources_deleted": deleted_resources}


@router.get("/admin/products/discover")
def discover_products(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Dynamically discover resources from Kubernetes matching managed products for THIS backend"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from ..services.dynamic_sources import generate_dynamic_sources
    import os
    
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    platform_name = os.getenv("BACKEND_PLATFORM", "default-platform")
    sources = generate_dynamic_sources(db, backend_name, platform_name)
    
    return {
        "ok": True,
        "backend": backend_name,
        "platform": platform_name,
        "discovered": len(sources),
        "sources": sources
    }


@router.get("/admin/products/dynamic-mode")
def get_dynamic_mode(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Get dynamic discovery mode setting for THIS backend"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Read from backend_endpoints table
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    enabled = backend.use_dynamic_discovery if backend else False
    
    return {"enabled": enabled, "backend": backend_name}


@router.post("/admin/products/dynamic-mode")
def set_dynamic_mode(
    enabled: bool = Form(...),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Enable/disable dynamic discovery mode for THIS backend"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    import os
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Store in backend_endpoints table (per-backend setting)
    backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
    
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    
    # Set dynamic discovery mode
    backend.use_dynamic_discovery = enabled
    db.commit()
    
    mode = "enabled" if enabled else "disabled"
    return {
        "ok": True,
        "enabled": enabled,
        "backend": backend_name,
        "message": f"Dynamic discovery {mode} for backend '{backend_name}'. Resources will be discovered based on managed products."
    }


# ============================================================================
# MULTI-BACKEND FEDERATION ENDPOINTS (Primary Backend Only)
# ============================================================================

@router.post("/federation/validate-token")
def validate_token_for_secondary(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Validate JWT token for secondary backends.
    Secondary backends call this endpoint to validate user tokens without database access.
    Returns user info if valid, raises 401 if invalid.
    """
    try:
        claims = _decode_claims(token)
        return {
            "valid": True,
            "username": claims.get("sub"),
            "role": claims.get("role")
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


@router.get("/federation/config")
def get_shared_config(
    backend_name: str = None,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Get shared configuration for secondary backends.
    Returns Twistlock, EOL configs, managed products, and backend-specific settings.
    
    Authentication:
    - Accepts both JWT user tokens AND backend auth tokens
    - Backend auth tokens are validated against backend_endpoints table
    
    Query parameter:
    - backend_name: Optional. If provided, includes backend-specific config (auto_sync_schedule, etc.)
    
    Note: SMTP is NOT shared because:
    - Only primary backend sends emails
    - Secondary backends never use SMTP
    - Email endpoint aggregates data from all backends on primary
    """
    # Validate token - accepts both user JWT tokens and backend auth tokens
    is_backend_token = False
    try:
        # Try as JWT user token first
        _decode_claims(token)
    except:
        # If not a valid JWT, check if it's a backend auth token
        if backend_name:
            backend = db.query(BackendEndpoint).filter(
                BackendEndpoint.name == backend_name,
                BackendEndpoint.auth_token == token
            ).one_or_none()
            
            if backend:
                is_backend_token = True
                log_event("federation.config.backend_auth", backend=backend_name)
            else:
                log_event("federation.config.invalid_backend_token", backend=backend_name)
                raise HTTPException(status_code=401, detail="Invalid backend authentication token")
        else:
            log_event("federation.config.invalid_token")
            raise HTTPException(status_code=401, detail="Invalid token")
    
    # Fetch configs from database
    configs = db.query(ConfigKV).all()
    config_dict = {c.key: c.value for c in configs}
    
    # Get skip TLS setting for Twistlock
    skip_tls_value = config_dict.get("twistlock_skip_tls_verify", "false")
    skip_tls_bool = skip_tls_value == "true"
    
    log_event("federation.config.twistlock_tls", 
             raw_value=skip_tls_value, 
             skip_tls_verify=skip_tls_bool)
    
    # Get ALL managed products (shared across all backends)
    # Each backend discovers these products in its own cluster
    managed_products_list = db.query(ManagedProduct).all()
    # Get unique product names (remove duplicates if multiple backends manage same product)
    unique_products = {}
    for p in managed_products_list:
        if p.product_name not in unique_products:
            unique_products[p.product_name] = {
                "product_name": p.product_name,
                "is_manual": p.is_manual,
                "image_pattern": p.image_pattern or "",
            }
    managed_products = list(unique_products.values())
    
    # Get product list
    products_list = db.query(ProductList).all()
    products = [p.product_name for p in products_list]
    
    # Get backend-specific configuration if backend_name is provided
    backend_config = {}
    if backend_name:
        backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
        if backend:
            backend_config = {
                "auto_sync_schedule": backend.auto_sync_schedule,
                "force_sync_enabled": backend.force_sync_enabled,
                "cleanup_force_enabled": getattr(backend, "cleanup_force_enabled", False),
                "use_dynamic_discovery": backend.use_dynamic_discovery,
                "latest_version_mode": backend.latest_version_mode,  # Per-backend mode (None = use global)
                "twistlock_mode": backend.twistlock_mode,  # Per-backend Twistlock query mode (None = use global)
                "trivy_mode": backend.trivy_mode,  # Per-backend Trivy query mode (None = use global)
                "eol_mode": backend.eol_mode,  # Per-backend EOL query mode (None = use global)
                "llm_mode": getattr(backend, 'llm_mode', None),
            }
    
    # Security thresholds (from DB or env defaults)
    def get_threshold(key: str, env_key: str, default: str) -> int:
        try:
            val = config_dict.get(key)
            if val and val.strip():
                return int(val)
            return int(os.getenv(env_key, default))
        except (ValueError, TypeError):
            return int(default)
    
    security_thresholds = {
        "red": {
            "riskFactor": get_threshold("security_red_risk_factor", "SECURITY_RED_RISK_FACTOR", "10"),
            "critical": get_threshold("security_red_critical", "SECURITY_RED_CRITICAL", "0"),
        },
        "orange": {
            "riskFactorMin": get_threshold("security_orange_risk_factor_min", "SECURITY_ORANGE_RISK_FACTOR_MIN", "5"),
            "riskFactorMax": get_threshold("security_orange_risk_factor_max", "SECURITY_ORANGE_RISK_FACTOR_MAX", "10"),
            "critical": get_threshold("security_orange_critical", "SECURITY_ORANGE_CRITICAL", "0"),
        },
        "green": {
            "riskFactor": get_threshold("security_green_risk_factor", "SECURITY_GREEN_RISK_FACTOR", "5"),
            "critical": get_threshold("security_green_critical", "SECURITY_GREEN_CRITICAL", "0"),
        },
    }
    
    # Latest version check config
    def get_config_str(key: str, env_key: str, default: str) -> str:
        val = config_dict.get(key)
        if val and val.strip():
            return val
        return os.getenv(env_key, default)
    
    latest_version_config = {
        "mode": get_config_str("latest_version_check_mode", "LATEST_VERSION_CHECK_MODE", "local"),
        "cache_hours": get_threshold("latest_version_cache_hours", "LATEST_VERSION_CACHE_HOURS", "12"),
    }
    
    # Get skip TLS setting for Trivy
    trivy_skip_tls_value = config_dict.get("trivy_skip_tls_verify", "true")
    trivy_skip_tls_bool = trivy_skip_tls_value == "true"
    
    return {
        "twistlock": {
            "url": config_dict.get("twistlock_url"),
            "username": config_dict.get("twistlock_user"),
            "password": config_dict.get("twistlock_user_password"),
            "skip_tls_verify": skip_tls_bool
        },
        "trivy_config": {
            "url": config_dict.get("trivy_url"),
            "skip_tls_verify": trivy_skip_tls_bool
        },
        "eol_api_enabled": config_dict.get("eol_api_enabled") == "true",
        "managed_products": managed_products,
        "product_list": products,
        "backend_config": backend_config,  # Backend-specific settings (auto_sync_schedule, etc.)
        "security_thresholds": security_thresholds,  # Security status color thresholds
        "latest_version_config": latest_version_config,  # Latest version check mode (local/primary)
        "llm_prompts": {
            "security": config_dict.get("llm_prompt_security", ""),
            "upgrade": config_dict.get("llm_prompt_upgrade", ""),
            "eol_status": config_dict.get("llm_prompt_eol_status", ""),
        },
        "image_update_settings": {
            "stuck_detection_seconds": int(config_dict.get("stuck_detection_seconds", "300")),
            "crash_tolerance_seconds": int(config_dict.get("crash_tolerance_seconds", "120")),
            "patch_batch_size": int(config_dict.get("patch_batch_size", "10")),
            "patch_batch_pause_seconds": int(config_dict.get("patch_batch_pause_seconds", "2")),
            "stuck_job_timeout_minutes": int(config_dict.get("stuck_job_timeout_minutes", "60")),
        },
        # NOTE: SMTP intentionally NOT included - only primary backend sends emails
    }


@router.post("/federation/register")
def register_secondary_backend(
    request: BackendRegistrationRequest,
    db: Session = Depends(get_db)
) -> dict:
    """
    Self-registration endpoint for secondary backends.
    Backend provides its registration token (pre-generated by admin) to register itself.
    
    SECURITY: Uses request body instead of query params to prevent token exposure in logs.
    """
    # Find the pre-authorized backend entry
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == request.backend_name,
        BackendEndpoint.auth_token == request.registration_token
    ).one_or_none()
    
    if not backend:
        raise HTTPException(
            status_code=403,
            detail="Invalid backend name or registration token. Please contact admin to pre-authorize this backend."
        )
    
    if backend.approved:
        raise HTTPException(
            status_code=400,
            detail=f"Backend '{request.backend_name}' is already registered and approved."
        )
    
    # Update backend with its details
    backend.platform = request.platform
    backend.api_url = request.api_url
    backend.skip_tls_verify = request.skip_tls_verify
    if request.description:
        backend.description = request.description
    backend.approved = True
    backend.approved_by = "self-registration"
    backend.approved_at = datetime.now(timezone.utc)
    backend.last_seen_at = datetime.now(timezone.utc)
    # Federation backends MUST use dynamic discovery (fetch managed products from primary)
    backend.use_dynamic_discovery = True
    
    db.commit()
    log_event("backend.self_registered", backend=request.backend_name, platform=request.platform, url=request.api_url, use_dynamic_discovery=True)
    
    return {
        "ok": True,
        "message": f"Backend '{request.backend_name}' successfully registered and approved",
        "backend": {
            "name": backend.name,
            "platform": backend.platform,
            "api_url": backend.api_url,
            "backend_type": backend.backend_type
        }
    }


@router.get("/federation/image-cache")
def get_image_cache_for_secondary(
    image: str,
    product_name: str = None,
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token")
) -> dict:
    """
    Get cached EOL and Twistlock data for an image.
    Called by secondary backends to fetch cache from primary.
    If data is not in cache, fetches it on demand from external APIs.
    """
    # Validate backend token
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")
    
    # Import cache functions
    from ..services.image_cache import get_cached_eol, get_cached_twistlock, set_cached_eol, set_cached_twistlock
    from ..services.twistlock import fetch_twistlock_cves
    import json
    from ..models import ImageCache
    
    # Get cache data directly from database
    cache = db.query(ImageCache).filter(ImageCache.image == image).first()
    
    eol_data = None
    twistlock_data = None
    fetched_eol = False
    fetched_twistlock = False
    
    # Try to get from cache first
    if cache:
        if cache.eol_data:
            try:
                eol_data = json.loads(cache.eol_data)
            except:
                pass
        
        if cache.twistlock_data:
            try:
                twistlock_data = json.loads(cache.twistlock_data)
            except:
                pass
    
    # Fetch EOL on demand if not in cache and product_name is provided
    if eol_data is None and product_name:
        try:
            import httpx
            # Parse version from image tag
            tag = image.split(":")[-1] if ":" in image else None
            if tag:
                # Strip 'v' prefix from version
                clean_version = tag.lstrip('vV')
                version_parts = clean_version.split(".")
                if len(version_parts) >= 2:
                    cycle = f"{version_parts[0]}.{version_parts[1]}"
                    product_slug = product_name.lower().replace(" ", "-")
                    eol_url = f"https://endoflife.date/api/{product_slug}/{cycle}.json"
                    
                    log_event("federation.image_cache.eol.fetch_on_demand",
                             image=image, product=product_slug, cycle=cycle,
                             requesting_backend=backend.name)
                    
                    with httpx.Client(timeout=5.0, follow_redirects=True) as http_client:
                        eol_resp = http_client.get(eol_url)
                        if eol_resp.status_code == 200:
                            eol_raw = eol_resp.json()
                            eol_data = {
                                "eol": eol_raw.get("eol"),
                                "support": eol_raw.get("support"),
                                "latest_in_cycle": eol_raw.get("latest"),
                                "cycle": cycle,
                            }
                            # Store in cache
                            set_cached_eol(db, image, eol_data)
                            fetched_eol = True
                            log_event("federation.image_cache.eol.fetched",
                                     image=image, product=product_slug, cycle=cycle)
                        else:
                            log_event("federation.image_cache.eol.not_found",
                                     image=image, product=product_slug, cycle=cycle,
                                     status=eol_resp.status_code)
        except Exception as e:
            log_event("federation.image_cache.eol.fetch_error", image=image, error=str(e))
    
    # Fetch Twistlock on demand if not in cache
    if twistlock_data is None:
        try:
            log_event("federation.image_cache.twistlock.fetch_on_demand",
                     image=image, requesting_backend=backend.name)
            
            twistlock_result = fetch_twistlock_cves(image, db)
            if twistlock_result:
                twistlock_data = twistlock_result
                # Store in cache
                set_cached_twistlock(db, image, twistlock_data)
                fetched_twistlock = True
                log_event("federation.image_cache.twistlock.fetched", image=image)
        except Exception as e:
            log_event("federation.image_cache.twistlock.fetch_error", image=image, error=str(e))
    
    # If still no data at all, return 404
    if eol_data is None and twistlock_data is None:
        log_event("federation.image_cache.not_found", 
                 image=image, 
                 requesting_backend=backend.name,
                 product_name=product_name)
        raise HTTPException(status_code=404, detail="Image not in cache and could not fetch")
    
    # Reload cache to get timestamps
    cache = db.query(ImageCache).filter(ImageCache.image == image).first()
    
    log_event("federation.image_cache.served", 
             image=image, 
             requesting_backend=backend.name,
             has_eol=eol_data is not None,
             has_twistlock=twistlock_data is not None,
             fetched_eol=fetched_eol,
             fetched_twistlock=fetched_twistlock)
    
    return {
        "image": image,
        "eol_data": eol_data,
        "twistlock_data": twistlock_data,
        "eol_fetched_at": cache.eol_fetched_at.isoformat() if cache and cache.eol_fetched_at else None,
        "twistlock_fetched_at": cache.twistlock_fetched_at.isoformat() if cache and cache.twistlock_fetched_at else None,
        "fetched_on_demand": fetched_eol or fetched_twistlock
    }


@router.get("/federation/latest-version")
def get_latest_version_for_secondary(
    image: str,
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token")
) -> dict:
    """
    Fetch latest version for an image.
    Called by secondary backends when latest_version_check_mode is 'primary'.
    Primary backend fetches from external registries and returns the result.
    Results are cached for configurable duration (default 12 hours).
    """
    # Validate backend token
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")
    
    from ..services.versioning import parse_image, fetch_latest_version
    from ..models import ImageCache
    from datetime import datetime, timezone, timedelta
    import json
    
    # Get cache duration from config
    cache_hours_kv = db.query(ConfigKV).filter(ConfigKV.key == "latest_version_cache_hours").one_or_none()
    cache_hours = int(cache_hours_kv.value) if cache_hours_kv and cache_hours_kv.value else 12
    
    # Check cache first
    cache = db.query(ImageCache).filter(ImageCache.image == image).first()
    
    if cache and cache.latest_version_data and cache.latest_version_fetched_at:
        # Check if cache is still valid
        cache_age = datetime.now(timezone.utc) - cache.latest_version_fetched_at.replace(tzinfo=timezone.utc)
        if cache_age < timedelta(hours=cache_hours):
            try:
                cached_data = json.loads(cache.latest_version_data)
                log_event("federation.latest_version.from_cache",
                         image=image, requesting_backend=backend.name,
                         cache_age_hours=round(cache_age.total_seconds() / 3600, 2))
                return {
                    "image": image,
                    "latest_version": cached_data.get("latest_version"),
                    "from_cache": True,
                    "fetched_at": cache.latest_version_fetched_at.isoformat()
                }
            except:
                pass
    
    # Fetch latest version from registry
    log_event("federation.latest_version.fetch",
             image=image, requesting_backend=backend.name)
    
    latest_version = None
    error_msg = None
    
    try:
        parsed = parse_image(image)
        if parsed:
            latest_version = fetch_latest_version(parsed)
            log_event("federation.latest_version.success",
                     image=image, latest_version=latest_version)
    except Exception as e:
        error_msg = str(e)
        log_event("federation.latest_version.error",
                 image=image, error=error_msg)
    
    # Store in cache
    if latest_version or error_msg:
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        
        cache.latest_version_data = json.dumps({
            "latest_version": latest_version,
            "error": error_msg
        })
        cache.latest_version_fetched_at = datetime.now(timezone.utc)
        db.commit()
    
    if latest_version is None and error_msg:
        raise HTTPException(status_code=502, detail=f"Failed to fetch latest version: {error_msg}")
    
    return {
        "image": image,
        "latest_version": latest_version,
        "from_cache": False,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/federation/twistlock")
def get_twistlock_for_secondary(
    image: str,
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token")
) -> dict:
    """
    Fetch Twistlock data for an image.
    Called by secondary backends when twistlock_mode is 'primary'.
    Primary backend fetches from Twistlock API and returns the result.
    Results are cached for 12 hours.
    """
    # Validate backend token
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")
    
    from ..models import ImageCache
    from ..services.twistlock import fetch_image_report
    from datetime import datetime, timezone, timedelta
    import json
    
    cache_hours = 12  # Fixed 12 hour cache for Twistlock data
    
    # Check cache first
    cache = db.query(ImageCache).filter(ImageCache.image == image).first()
    
    if cache and cache.twistlock_data and cache.twistlock_fetched_at:
        # Check if cache is still valid
        cache_age = datetime.now(timezone.utc) - cache.twistlock_fetched_at.replace(tzinfo=timezone.utc)
        if cache_age < timedelta(hours=cache_hours):
            try:
                cached_data = json.loads(cache.twistlock_data)
                log_event("federation.twistlock.from_cache",
                         image=image, requesting_backend=backend.name,
                         cache_age_hours=round(cache_age.total_seconds() / 3600, 2))
                return {
                    "image": image,
                    "twistlock": cached_data,
                    "from_cache": True,
                    "fetched_at": cache.twistlock_fetched_at.isoformat()
                }
            except:
                pass
    
    # Fetch Twistlock data from API
    log_event("federation.twistlock.fetch",
             image=image, requesting_backend=backend.name)
    
    twistlock_data = None
    error_msg = None
    
    try:
        # fetch_image_report reads config internally via _effective_config()
        # It will use database config or environment variables automatically
        twistlock_data = fetch_image_report(image)
        if twistlock_data:
            log_event("federation.twistlock.success",
                     image=image, 
                     vulnerabilities=twistlock_data.get("vulnerabilityDistribution", {}).get("total", 0))
        else:
            error_msg = "No Twistlock data returned (check Twistlock configuration on primary)"
            log_event("federation.twistlock.no_data", image=image)
    except Exception as e:
        error_msg = str(e)
        log_event("federation.twistlock.error",
                 image=image, error=error_msg)
    
    # Store in cache
    if twistlock_data is not None:
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        
        cache.twistlock_data = json.dumps(twistlock_data)
        cache.twistlock_fetched_at = datetime.now(timezone.utc)
        db.commit()
    
    if twistlock_data is None and error_msg:
        return {
            "image": image,
            "twistlock": None,
            "error": error_msg,
            "from_cache": False
        }
    
    return {
        "image": image,
        "twistlock": twistlock_data,
        "from_cache": False,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/federation/trivy")
def get_trivy_for_secondary(
    image: str,
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token")
) -> dict:
    """Fetch Trivy data for an image. Called by secondary backends."""
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")
    
    from ..services.image_cache import get_cached_trivy
    cached_data, cache_hit = get_cached_trivy(db, image)
    if cache_hit:
        return {"image": image, "trivy": cached_data, "from_cache": True}

    # Not cached → submit a BACKGROUND scan and return immediately. Scanning inline here
    # blocked the requesting secondary for the whole scan (slow/rate-limited docker.io
    # pulls timed out → 'read operation timed out' on the secondary, dragging out every
    # federated refresh). The result lands in the cache for the secondary's next refresh.
    from ..services.trivy import submit_scan
    submit_scan(image)
    return {
        "image": image,
        "trivy": None,
        "from_cache": False,
        "pending": True,
    }


@router.get("/federation/llm-advice")
def get_llm_advice_for_secondary(
    image: str,
    resource_name: str = "",
    current_version: str = "",
    latest_version: str = "",
    version_diff: str = "",
    eol_date: str = "",
    advice_type: str = "security",
    product: str = "",
    version: str = "",
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
) -> dict:
    """Generate or return cached LLM advice. Called by secondary backends.
    advice_type: 'security' (default, used by sync), 'upgrade' (ask-ai), or 'eol_status'
    (support status when no public EOL data)."""
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")

    if advice_type == "eol_status":
        from ..services.llm_advice import generate_eol_status
        result = generate_eol_status(product or resource_name, version or current_version, image)
        if result and result.get("status"):
            return {"status": result["status"], "note": result.get("note", ""), "from_cache": False}
        return {"status": None, "note": ""}

    is_upgrade = advice_type == "upgrade"
    ts_key = "llm_upgrade_at" if is_upgrade else "llm_advice_at"
    text_key = "llm_upgrade_advice" if is_upgrade else "advice"

    from ..models import Resource
    from datetime import datetime, timezone
    cached_res = (
        db.query(Resource)
        .filter(Resource.image == image)
        .filter(Resource.security_info.isnot(None))
        .first()
    )
    if cached_res and cached_res.security_info:
        si = cached_res.security_info
        llm_ts = si.get(ts_key)
        advice_text = si.get(text_key)
        if llm_ts and advice_text:
            try:
                age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(llm_ts)).total_seconds() / 3600
                if age_h < 24:
                    return {"image": image, "advice": advice_text, "from_cache": True, "age_hours": round(age_h, 1)}
            except Exception:
                pass

    twistlock = None
    trivy = None
    if cached_res and cached_res.security_info:
        twistlock = cached_res.security_info.get("twistlock")
        trivy = cached_res.security_info.get("trivy")

    if is_upgrade:
        from ..services.llm_advice import generate_upgrade_advice
        advice = generate_upgrade_advice(
            resource_name or image, image, current_version, latest_version,
            version_diff, twistlock, trivy, eol_date or None
        )
    else:
        from ..services.llm_advice import generate_security_advice
        advice = generate_security_advice(
            resource_name or image, image, current_version, latest_version,
            version_diff, twistlock, trivy, eol_date or None
        )
    return {
        "image": image,
        "advice": advice,
        "from_cache": False,
    }


@router.get("/federation/eol")
def get_eol_for_secondary(
    image: str,
    product: str,
    version: str,
    db: Session = Depends(get_db),
    x_backend_token: str = Header(None, alias="X-Backend-Token")
) -> dict:
    """
    Fetch EOL data for a product version.
    Called by secondary backends when eol_mode is 'primary'.
    Primary backend fetches from endoflife.date API and returns the result.
    Results are cached for 24 hours.
    """
    # Validate backend token
    if not x_backend_token:
        raise HTTPException(status_code=401, detail="Missing X-Backend-Token header")
    
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.auth_token == x_backend_token,
        BackendEndpoint.approved == True
    ).one_or_none()
    
    if not backend:
        raise HTTPException(status_code=403, detail="Invalid backend token")
    
    from ..models import ImageCache
    from datetime import datetime, timezone, timedelta
    import json
    import httpx
    
    cache_hours = 24  # 24 hour cache for EOL data
    
    # Check cache first
    cache = db.query(ImageCache).filter(ImageCache.image == image).first()
    
    if cache and cache.eol_data and cache.eol_fetched_at:
        # Check if cache is still valid
        cache_age = datetime.now(timezone.utc) - cache.eol_fetched_at.replace(tzinfo=timezone.utc)
        if cache_age < timedelta(hours=cache_hours):
            try:
                cached_data = json.loads(cache.eol_data)
                log_event("federation.eol.from_cache",
                         image=image, product=product, version=version,
                         requesting_backend=backend.name,
                         cache_age_hours=round(cache_age.total_seconds() / 3600, 2))
                return {
                    "image": image,
                    "product": product,
                    "version": version,
                    "eol": cached_data,
                    "from_cache": True,
                    "fetched_at": cache.eol_fetched_at.isoformat()
                }
            except:
                pass
    
    # Fetch EOL data from API
    log_event("federation.eol.fetch",
             image=image, product=product, version=version,
             requesting_backend=backend.name)
    
    eol_data = None
    error_msg = None
    
    try:
        # Get EOL API URL from config
        eol_api_kv = db.query(ConfigKV).filter(ConfigKV.key == "eol_api_url").one_or_none()
        base_url = eol_api_kv.value if eol_api_kv and eol_api_kv.value else "https://endoflife.date"
        
        # Try to get EOL data for the product
        eol_url = f"{base_url.rstrip('/')}/api/{product}.json"
        
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(eol_url)
            
            if resp.status_code == 200:
                cycles = resp.json()
                
                if isinstance(cycles, list) and cycles:
                    # Parse version to find matching cycle
                    from ..services.versioning import parse_version_tuple
                    ver_candidates = []
                    try:
                        t = parse_version_tuple(version)
                        if t:
                            maj, minr, patch = t
                            ver_candidates = [f"{maj}.{minr}.{patch}", f"{maj}.{minr}", f"{maj}"]
                    except:
                        ver_candidates = [version]
                    
                    # Find matching cycle
                    chosen = None
                    for vc in ver_candidates:
                        for cycle in cycles:
                            cycle_name = str(cycle.get("cycle") or cycle.get("name") or "")
                            if cycle_name == vc or cycle_name.startswith(vc):
                                chosen = cycle
                                break
                        if chosen:
                            break
                    
                    if chosen:
                        eol_data = {
                            "eol": chosen.get("eol"),
                            "support": chosen.get("support"),
                            "cycle": chosen.get("cycle") or chosen.get("name"),
                            "latest_in_cycle": chosen.get("latest"),
                            "release_date": chosen.get("releaseDate")
                        }
                        log_event("federation.eol.success",
                                 image=image, product=product, version=version,
                                 eol_date=eol_data.get("eol"))
                    else:
                        error_msg = f"No matching cycle found for version {version}"
                        log_event("federation.eol.no_match",
                                 image=image, product=product, version=version)
                else:
                    error_msg = "Empty or invalid response from endoflife.date"
            elif resp.status_code == 404:
                error_msg = f"Product '{product}' not found on endoflife.date"
                log_event("federation.eol.product_not_found",
                         image=image, product=product)
            else:
                error_msg = f"API returned status {resp.status_code}"
                log_event("federation.eol.api_error",
                         image=image, product=product, status=resp.status_code)
                
    except Exception as e:
        error_msg = str(e)
        log_event("federation.eol.error",
                 image=image, product=product, error=error_msg)
    
    # Store in cache (even if None to prevent repeated requests)
    if eol_data is not None:
        if not cache:
            cache = ImageCache(image=image)
            db.add(cache)
        
        cache.eol_data = json.dumps(eol_data)
        cache.eol_fetched_at = datetime.now(timezone.utc)
        db.commit()
    
    if eol_data is None:
        return {
            "image": image,
            "product": product,
            "version": version,
            "eol": None,
            "error": error_msg,
            "from_cache": False
        }
    
    return {
        "image": image,
        "product": product,
        "version": version,
        "eol": eol_data,
        "from_cache": False,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/federation/sync-resource")
def sync_resource_proxy(
    request: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Proxy endpoint to sync a resource on a remote backend.
    Called by frontend when syncing resources that belong to remote backends.
    Primary backend forwards the request to the appropriate remote backend.
    Admin role required.
    
    Request body:
    - platform: The platform where the resource is hosted
    - namespace: Resource namespace
    - resource_name: Resource name
    - kind: Resource kind (deployment, statefulset, daemonset)
    - backend_url: (optional) Direct URL to backend API
    """
    import httpx
    
    # Admin role required for sync operations
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to perform sync")
    
    platform = request.get("platform", "").strip()
    namespace = request.get("namespace", "").strip()
    resource_name = request.get("resource_name", "").strip()
    kind = request.get("kind", "").strip()
    backend_url = request.get("backend_url", "").strip()
    
    if not platform or not namespace or not resource_name or not kind:
        return {
            "ok": False,
            "error": "platform, namespace, resource_name, and kind are required"
        }
    
    log_event("federation.sync_resource.start",
             platform=platform, namespace=namespace, 
             resource=resource_name, kind=kind)
    
    # Find the backend for this platform
    backend = None
    if backend_url:
        # Try to find by URL
        base_url = backend_url.rstrip('/').replace('/api', '')
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.api_url.contains(base_url.split('//')[1].split('/')[0]),
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True
        ).first()
    
    if not backend:
        # Try to find by platform
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.platform == platform,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True
        ).first()
    
    if not backend:
        log_event("federation.sync_resource.no_backend",
                 platform=platform)
        return {
            "ok": False,
            "error": f"No backend found for platform: {platform}"
        }
    
    # Check if this is the local backend
    import os
    local_backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend.name == local_backend_name:
        # Resource is on this backend - sync locally
        from ..models import Resource
        local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
        
        res = db.query(Resource).filter(
            Resource.platform == local_platform,
            Resource.namespace == namespace,
            Resource.resource_name == resource_name,
            Resource.kind.ilike(kind)
        ).first()
        
        if not res:
            return {"ok": False, "error": "Resource not found locally"}

        # Import and call the sync function. sync_single_resource is async, but this
        # proxy is a sync endpoint (FastAPI runs it in a threadpool), so the coroutine
        # must be driven on a fresh event loop — the same pattern used elsewhere (e.g.
        # image_update.py / main.py). Returning the coroutine without awaiting it makes
        # FastAPI fail to serialize it → 500 "Internal Server Error" (this is why the
        # primary's own/local resources error while remote ones, which take the forward
        # path below, work).
        from .resources import sync_single_resource
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(sync_single_resource(res.id, token, db))
        finally:
            loop.close()
    
    # Forward request to remote backend
    verify_ssl = not backend.skip_tls_verify
    
    try:
        url = f"{backend.api_url.rstrip('/')}/api/resources/sync-by-key"
        headers = {
            "X-Backend-Token": backend.auth_token,
            "Content-Type": "application/json"
        }
        body = {
            "namespace": namespace,
            "resource_name": resource_name,
            "kind": kind
        }
        
        log_event("federation.sync_resource.forwarding",
                 backend=backend.name, url=url)
        
        with httpx.Client(timeout=180.0, verify=verify_ssl) as client:
            resp = client.post(url, json=body, headers=headers)
            
            if resp.status_code == 200:
                result = resp.json()
                log_event("federation.sync_resource.success",
                         backend=backend.name, resource=resource_name)
                return result
            else:
                error_text = resp.text[:200]
                log_event("federation.sync_resource.error",
                         backend=backend.name, status=resp.status_code, 
                         error=error_text)
                return {
                    "ok": False,
                    "error": f"Remote backend returned {resp.status_code}: {error_text}"
                }
                
    except Exception as e:
        log_event("federation.sync_resource.exception",
                 backend=backend.name, error=str(e))
        return {
            "ok": False,
            "error": f"Failed to reach backend: {str(e)}"
        }


@router.post("/federation/trigger-sync")
def trigger_sync_proxy(
    request: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Proxy trigger-sync to local or remote backend by backend_name."""
    import httpx
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    backend_name = request.get("backend_name", "")
    if not backend_name:
        raise HTTPException(status_code=400, detail="backend_name required")

    local_name = os.getenv("BACKEND_NAME", "default-backend")
    be = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()

    is_local = (backend_name == local_name) or (be and getattr(be, 'backend_type', '') == "primary")

    if is_local:
        url = f"http://localhost:{os.getenv('PORT', '8000')}/api/resources/refresh"
        headers_dict = {"Authorization": f"Bearer {token}"}
    elif be and be.enabled and be.approved:
        url = f"{be.api_url.rstrip('/')}/api/resources/refresh"
        headers_dict = {"X-Backend-Token": be.auth_token}
    else:
        raise HTTPException(status_code=404, detail=f"Backend {backend_name} not found or not approved")

    try:
        with httpx.Client(timeout=30, verify=not (be.skip_tls_verify if be else False)) as client:
            resp = client.post(url, headers=headers_dict)
            if resp.status_code == 200:
                return resp.json()
            log_event("trigger_sync.remote_error", backend=backend_name, status=resp.status_code)
            raise HTTPException(status_code=502, detail=f"Backend {backend_name} returned {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        log_event("trigger_sync.remote_error", backend=backend_name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Cannot reach {backend_name}: {e}")


@router.post("/federation/cancel-sync")
def cancel_sync_proxy(
    request: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Proxy cancel-sync to a remote backend by backend_name and sync_log_id."""
    import httpx
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    backend_name = request.get("backend_name", "")
    sync_log_id = request.get("sync_log_id")
    if not backend_name or not sync_log_id:
        raise HTTPException(status_code=400, detail="backend_name and sync_log_id required")

    local_name = os.getenv("BACKEND_NAME", "default-backend")
    if backend_name == local_name or backend_name == "default-backend":
        from .resources import cancel_sync_job
        return cancel_sync_job(sync_log_id, token, db)

    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == backend_name,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True,
    ).one_or_none()
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend {backend_name} not found")

    try:
        url = f"{backend.api_url.rstrip('/')}/api/sync-logs/{sync_log_id}/cancel"
        headers = {"X-Backend-Token": backend.auth_token}
        with httpx.Client(timeout=15, verify=not backend.skip_tls_verify) as client:
            resp = client.post(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return {"ok": False, "detail": f"Remote returned {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


# ============================================================================
# EXCLUSION MANAGEMENT (Registries & Namespaces)
# ============================================================================

@router.get("/exclusions")
def get_exclusions(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Get current exclusion lists for registries and namespaces"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from ..services.exclusions import get_excluded_registries, get_excluded_namespaces
    
    return {
        "registries": get_excluded_registries(db),
        "namespaces": get_excluded_namespaces(db)
    }


@router.post("/exclusions/registries")
def update_excluded_registries(registries: list[str], token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Update excluded image registries list"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from ..services.exclusions import set_excluded_registries
    set_excluded_registries(db, registries)
    
    return {"ok": True, "count": len(registries)}


@router.post("/exclusions/namespaces")
def update_excluded_namespaces(namespaces: list[str], token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Update excluded namespaces list"""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from ..services.exclusions import set_excluded_namespaces
    set_excluded_namespaces(db, namespaces)
    
    return {"ok": True, "count": len(namespaces)}


# ─────────────────────────────────────────────────────────────────────────────
# Federation Proxy: aggregate data from all backends (server-side)
# Frontend calls these instead of directly hitting remote backends (no CORS)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_remote_backends(db: Session) -> list[BackendEndpoint]:
    """Get all enabled, approved, non-default remote backends."""
    return db.query(BackendEndpoint).filter(
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True,
        BackendEndpoint.backend_type != "primary",
    ).all()


def _proxy_get_json(backend: BackendEndpoint, path: str, timeout: float = 30.0) -> list:
    """Server-side GET request to a remote backend. Returns list or []."""
    import httpx
    url = f"{backend.api_url.rstrip('/')}{path}"
    headers = {"X-Backend-Token": backend.auth_token}
    try:
        with httpx.Client(timeout=timeout, verify=not backend.skip_tls_verify) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
            log_event("federation.proxy.error", backend=backend.name, path=path, status=resp.status_code)
    except Exception as e:
        log_event("federation.proxy.error", backend=backend.name, path=path, error=str(e))
    return []


def _slim_security_info(si: dict | None) -> dict | None:
    """Strip heavy CVE lists from security_info, keep only summaries."""
    if not si or not isinstance(si, dict):
        return si
    slim = {}
    for key in ("advice", "llm_advice", "llm_advice_at", "llm_upgrade_advice",
                "llm_upgrade_at", "exists_in_cluster", "trivy_fetched_at"):
        if key in si:
            slim[key] = si[key]
    tw = si.get("twistlock")
    if tw and isinstance(tw, dict):
        slim["twistlock"] = {
            "vulnerabilityDistribution": tw.get("vulnerabilityDistribution"),
            "riskFactorCount": tw.get("riskFactorCount"),
        }
    trv = si.get("trivy")
    if trv and isinstance(trv, dict):
        slim["trivy"] = {
            "vulnerabilityDistribution": trv.get("vulnerabilityDistribution"),
        }
    containers = si.get("containers")
    if containers and isinstance(containers, list):
        slim_containers = []
        for c in containers:
            sc = {k: c.get(k) for k in ("name", "image", "current_version", "latest_version",
                                         "version_diff", "is_main")}
            c_tw = c.get("twistlock")
            if c_tw and isinstance(c_tw, dict):
                sc["twistlock"] = {
                    "vulnerabilityDistribution": c_tw.get("vulnerabilityDistribution"),
                    "riskFactorCount": c_tw.get("riskFactorCount"),
                }
            c_trv = c.get("trivy")
            if c_trv and isinstance(c_trv, dict):
                sc["trivy"] = {"vulnerabilityDistribution": c_trv.get("vulnerabilityDistribution")}
            slim_containers.append(sc)
        slim["containers"] = slim_containers
    return slim


def _slim_resource(d: dict) -> dict:
    """Reduce a resource dict for listing (strip heavy security_info fields)."""
    if "security_info" in d and d["security_info"]:
        d["security_info"] = _slim_security_info(d["security_info"])
    return d


@router.get("/proxy-config")
def get_proxy_config(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Lightweight endpoint for frontend to read proxy timeout."""
    _decode_claims(token)
    timeout_s = int(_get_config(db, "proxy_timeout_seconds", "60"))
    return {"proxy_timeout_ms": timeout_s * 1000}


@router.get("/admin/proxy-settings")
def get_proxy_settings(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) not in ("admin",):
        raise HTTPException(status_code=403, detail="Admin only")
    return {
        "proxy_timeout_seconds": int(_get_config(db, "proxy_timeout_seconds", "60")),
        "stuck_job_timeout_minutes": int(_get_config(db, "stuck_job_timeout_minutes", "60")),
        "stuck_detection_seconds": int(_get_config(db, "stuck_detection_seconds", "300")),
        "crash_tolerance_seconds": int(_get_config(db, "crash_tolerance_seconds", "120")),
        "patch_batch_size": int(_get_config(db, "patch_batch_size", "10")),
        "patch_batch_pause_seconds": int(_get_config(db, "patch_batch_pause_seconds", "2")),
    }


@router.post("/admin/proxy-settings")
def set_proxy_settings(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if _decode_role(token) not in ("admin",):
        raise HTTPException(status_code=403, detail="Admin only")
    val = body.get("proxy_timeout_seconds")
    if val is not None:
        # Ceiling raised 300 -> 3600 so the streaming proxy can use a long OpenShift
        # route timeout (e.g. 3000s) for large multi-backend syncs instead of giving up at 5 min.
        clamped = max(10, min(3600, int(val)))
        _set_config(db, "proxy_timeout_seconds", str(clamped))
    stuck_val = body.get("stuck_job_timeout_minutes")
    if stuck_val is not None:
        clamped = max(10, min(1440, int(stuck_val)))
        _set_config(db, "stuck_job_timeout_minutes", str(clamped))
    stuck_det = body.get("stuck_detection_seconds")
    if stuck_det is not None:
        clamped = max(60, min(1800, int(stuck_det)))
        _set_config(db, "stuck_detection_seconds", str(clamped))
    crash_tol = body.get("crash_tolerance_seconds")
    if crash_tol is not None:
        clamped = max(30, min(600, int(crash_tol)))
        _set_config(db, "crash_tolerance_seconds", str(clamped))
    batch_size = body.get("patch_batch_size")
    if batch_size is not None:
        clamped = max(1, min(100, int(batch_size)))
        _set_config(db, "patch_batch_size", str(clamped))
    batch_pause = body.get("patch_batch_pause_seconds")
    if batch_pause is not None:
        clamped = max(0, min(60, int(batch_pause)))
        _set_config(db, "patch_batch_pause_seconds", str(clamped))
    db.commit()
    return {"ok": True, **get_proxy_settings(token, db)}


@router.get("/proxy/{backend_name}/{path:path}")
async def proxy_to_backend(
    backend_name: str,
    path: str,
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """Streaming reverse-proxy to a named backend. No JSON parsing, minimal memory."""
    import httpx
    from starlette.responses import StreamingResponse
    _decode_claims(token)

    local_name = os.getenv("BACKEND_NAME", "default")
    is_local = (backend_name == local_name)

    if is_local:
        base = f"http://localhost:{os.getenv('PORT', '8000')}"
        headers_dict: dict[str, str] = {"Authorization": f"Bearer {token}"}
    else:
        be = db.query(BackendEndpoint).filter(
            BackendEndpoint.name == backend_name,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True,
        ).one_or_none()
        if not be:
            raise HTTPException(status_code=404, detail=f"Backend {backend_name} not found")
        base = be.api_url.rstrip("/")
        headers_dict = {"X-Backend-Token": be.auth_token}

    qs = str(request.query_params)
    target_url = f"{base}/{path}" + (f"?{qs}" if qs else "")

    proxy_timeout = int(_get_config(db, "proxy_timeout_seconds", "60"))
    db.close()
    try:
        client = httpx.AsyncClient(timeout=proxy_timeout, verify=False)
        req = client.build_request("GET", target_url, headers=headers_dict)
        resp = await client.send(req, stream=True)

        async def stream():
            try:
                async for chunk in resp.aiter_bytes(8192):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except Exception as e:
        log_event("proxy.stream.error", backend=backend_name, path=path, error=str(e))
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {backend_name}")


@router.post("/proxy/{backend_name}/{path:path}")
async def proxy_post_to_backend(
    backend_name: str,
    path: str,
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """POST reverse-proxy to a named backend."""
    import httpx
    from starlette.responses import StreamingResponse
    _decode_claims(token)

    local_name = os.getenv("BACKEND_NAME", "default")
    is_local = (backend_name == local_name)

    if is_local:
        base = f"http://localhost:{os.getenv('PORT', '8000')}"
        headers_dict: dict[str, str] = {"Authorization": f"Bearer {token}"}
    else:
        be = db.query(BackendEndpoint).filter(
            BackendEndpoint.name == backend_name,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True,
        ).one_or_none()
        if not be:
            raise HTTPException(status_code=404, detail=f"Backend {backend_name} not found")
        base = be.api_url.rstrip("/")
        headers_dict = {"X-Backend-Token": be.auth_token}

    content_type = request.headers.get("content-type", "application/json")
    headers_dict["content-type"] = content_type

    qs = str(request.query_params)
    target_url = f"{base}/{path}" + (f"?{qs}" if qs else "")
    body = await request.body()

    proxy_timeout = int(_get_config(db, "proxy_timeout_seconds", "60"))
    db.close()
    try:
        client = httpx.AsyncClient(timeout=proxy_timeout, verify=False)
        req = client.build_request("POST", target_url, headers=headers_dict, content=body)
        resp = await client.send(req, stream=True)

        async def stream():
            try:
                async for chunk in resp.aiter_bytes(8192):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except Exception as e:
        log_event("proxy.post.error", backend=backend_name, path=path, error=str(e))
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {backend_name}")


@router.get("/federation/all-resources")
def get_all_resources(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Aggregate resources from local + all remote backends (slim payload)."""
    _decode_claims(token)

    from .resources import list_resources
    local_items = list_resources(db, slim=True)
    backend_name = os.getenv("BACKEND_NAME", "default")
    local_dicts = []
    for item in local_items:
        d = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, 'model_dump') else dict(item))
        d["_backend"] = backend_name
        local_dicts.append(d)

    import concurrent.futures
    remote_backends = _fetch_remote_backends(db)

    def _fetch_one(be: BackendEndpoint) -> list[dict]:
        items = _proxy_get_json(be, "/api/resources?slim=true", timeout=45.0)
        for item in items:
            item["_backend"] = be.name
            _slim_resource(item)
        return items

    remote_results: list[dict] = []
    if remote_backends:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(remote_backends), 6)) as pool:
            futures = {pool.submit(_fetch_one, be): be for be in remote_backends}
            for future in concurrent.futures.as_completed(futures, timeout=60):
                try:
                    remote_results.extend(future.result())
                except Exception as e:
                    be = futures[future]
                    log_event("federation.proxy.timeout", backend=be.name, error=str(e))

    log_event("federation.all_resources", local=len(local_dicts), remote=len(remote_results),
              backends=len(remote_backends))
    return local_dicts + remote_results


@router.get("/federation/resource-detail")
def get_resource_detail(
    resource_id: int,
    backend_name: str = "",
    token: Optional[str] = Depends(oauth2_scheme_optional),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db),
) -> dict:
    """Return full security_info for a single resource (local or remote)."""
    if x_backend_token:
        be = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == x_backend_token,
            BackendEndpoint.approved == True,
        ).one_or_none()
        if not be:
            raise HTTPException(status_code=403, detail="Invalid backend token")
    elif token:
        _decode_claims(token)
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    local_name = os.getenv("BACKEND_NAME", "default")

    if not backend_name or backend_name == local_name:
        res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
        if not res:
            raise HTTPException(status_code=404, detail="Resource not found")
        return {
            "security_info": res.security_info,
            "advice": (res.security_info or {}).get("advice"),
            # Helm drift (Phase 1A) — lazy-loaded by the "What drifted?" modal. drift detail
            # is already redacted (env values masked) at compute time.
            "helm_status": res.helm_status,
            "helm_release_name": res.helm_release_name,
            "helm_release_namespace": res.helm_release_namespace,
            "helm_drift_detail": res.helm_drift_detail,
        }

    be = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == backend_name,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True,
    ).one_or_none()
    if not be:
        raise HTTPException(status_code=404, detail=f"Backend {backend_name} not found")

    import httpx
    url = f"{be.api_url.rstrip('/')}/api/federation/resource-detail?resource_id={resource_id}"
    headers = {"X-Backend-Token": be.auth_token}
    try:
        with httpx.Client(timeout=15, verify=not be.skip_tls_verify) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        log_event("federation.resource_detail.error", backend=backend_name, error=str(e))
    raise HTTPException(status_code=502, detail="Failed to fetch from remote backend")


@router.get("/federation/all-sync-logs")
def get_all_sync_logs(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Aggregate sync logs from local + all remote backends. No CORS needed."""
    _decode_claims(token)

    from .resources import get_sync_logs
    local_logs = get_sync_logs(limit=50, db=db)

    import concurrent.futures
    remote_backends = _fetch_remote_backends(db)

    def _fetch_one(be: BackendEndpoint) -> list[dict]:
        items = _proxy_get_json(be, "/api/sync-logs?limit=50", timeout=15.0)
        for item in items:
            item["_backend"] = be.name
        return items

    remote_results: list[dict] = []
    if remote_backends:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(remote_backends), 6)) as pool:
            futures = {pool.submit(_fetch_one, be): be for be in remote_backends}
            for future in concurrent.futures.as_completed(futures, timeout=30):
                try:
                    remote_results.extend(future.result())
                except Exception as e:
                    be = futures[future]
                    log_event("federation.proxy.timeout", backend=be.name, error=str(e))

    return local_logs + remote_results


