from typing import List, Optional
import os
from jose import jwt

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from io import BytesIO
from openpyxl import Workbook
from sqlalchemy.orm import Session, joinedload
from concurrent.futures import ThreadPoolExecutor

from ..db import get_db
from ..models import Resource, ResourcePlan, ResourceHistory, SyncLog, UserChart, ConfigKV, EmailRecipient, BackendEndpoint, ManagedProduct
from ..services.versioning import parse_image
from ..schemas import ResourceOut, RefreshResponse, PlanUpdate, HistoryItem
from ..services.refresh import refresh_from_sources
from ..services.email_service import send_email_with_csv
from ..logging_utils import log_event

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET", "changeme-secret-PLEASE-SET-ENV")
JWT_ALG = "HS256"


def _decode_role(token: str) -> str:
    """Decode JWT token and return user role."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return str(payload.get("role", ""))
    except Exception:
        return ""


# Shared thread pool for running blocking sync operations
# Using max_workers=2 to allow 2 concurrent syncs (one manual + one auto, or 2 backends)
_sync_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sync_worker")

# Track which backends are currently syncing to prevent concurrent syncs
_syncing_backends = set()


# Import sync state functions from separate module to avoid circular imports
from ..services.sync_state import is_sync_cancelled, mark_sync_cancelled, clear_cancelled_sync


class UserChartCreate(BaseModel):
    chart_name: str
    group_by: str
    count_by: str
    filter_by: Optional[str] = None  # DEPRECATED: use filters
    filter_value: Optional[str] = None  # DEPRECATED: use filters
    filters: Optional[List[dict]] = None  # [{"column": "platform", "value": "prod"}]
    chart_type: str = "bar"  # 'bar' or 'pie'


def _get_managed_platforms():
    from ..services.sources_reader import read_sources
    try:
        items = read_sources()
        return set(item.platform for item in items)
    except Exception:
        return set()


def _list_resources_slim(db: Session) -> list[dict]:
    """Fast path: raw SQL with JSONB operators. No Pydantic, no full security_info load."""
    from sqlalchemy import text
    managed = _get_managed_platforms()
    platform_filter = ""
    params: dict = {}
    if managed:
        placeholders = ", ".join(f":p{i}" for i in range(len(managed)))
        platform_filter = f"WHERE r.platform IN ({placeholders})"
        for i, p in enumerate(managed):
            params[f"p{i}"] = p

    # Reads the precomputed compact `sec_summary` instead of extracting 11 paths from
    # the large `security_info` JSONB per row (which detoasted the big blob repeatedly
    # and spilled the sort to disk). sec_summary is small → fast, in-memory sort.
    sql = text(f"""
        SELECT r.id, r.platform, r.namespace, r.resource_name, r.kind,
               r.replicas, r.image, r.current_version, r.latest_version,
               r.version_diff, r.last_updated_at, r.last_checked_at,
               r.product_name, r.eol_date,
               r.eol_support_status, r.eol_support_note,
               r.helm_status, r.helm_release_name,
               r.sec_summary,
               p.note, p.planned_upgrade_at,
               hc.history_count
        FROM resources r
        LEFT JOIN resource_plans p ON p.resource_id = r.id
        LEFT JOIN (
            -- Cheap per-resource count of distinct (container, version) change points.
            -- Full history is fetched on demand by GET /api/resources/{{id}}/history.
            SELECT resource_id,
                   COUNT(DISTINCT COALESCE(container_name, '_main') || '|' || version) AS history_count
            FROM resource_history
            WHERE version IS NOT NULL
            GROUP BY resource_id
        ) hc ON hc.resource_id = r.id
        {platform_filter}
        ORDER BY r.namespace, r.resource_name
    """)

    rows = db.execute(sql, params).mappings().all()
    result = []
    for row in rows:
        # Precomputed compact summary (written during refresh). Same shape the grid
        # used to get from the inline JSONB extraction — just read straight from the
        # small column. psycopg2 returns JSONB as a parsed dict.
        si = row["sec_summary"] or {}
        containers = si.get("containers") or []

        # Fallback for rows not yet backfilled (sec_summary NULL/empty): synthesize a
        # container from the image so the Image/Container columns stay populated.
        if not containers and row["image"]:
            img = row["image"]
            tag = img.rsplit(":", 1)[-1] if ":" in img else "latest"
            name = img.split("/")[-1].split(":")[0]
            containers = [{"name": name, "image": img, "current_version": tag}]

        advice_text = si.get("advice") if isinstance(si.get("advice"), str) else None
        if not (si.get("exists_in_cluster", True) if "exists_in_cluster" in si else True):
            advice_text = (advice_text or "") + " | Resource missing in cluster - review or delete"

        result.append({
            "id": row["id"], "platform": row["platform"],
            "namespace": row["namespace"], "resource_name": row["resource_name"],
            "kind": row["kind"], "replicas": row["replicas"],
            "image": row["image"], "current_version": row["current_version"],
            "latest_version": row["latest_version"], "version_diff": row["version_diff"],
            "last_updated_at": str(row["last_updated_at"]) if row["last_updated_at"] else None,
            "last_checked_at": str(row["last_checked_at"]) if row["last_checked_at"] else None,
            "product_name": row["product_name"], "eol_date": row["eol_date"],
            "eol_support_status": row["eol_support_status"], "eol_support_note": row["eol_support_note"],
            "helm_status": row["helm_status"], "helm_release_name": row["helm_release_name"],
            "advice": advice_text,
            "note": row["note"],
            "planned_upgrade_at": str(row["planned_upgrade_at"]) if row["planned_upgrade_at"] else None,
            "security_info": si if si else None,
            "containers": containers if containers else None,
            "vulnerability_count": None, "vulnerabilities": None,
            # Full history is lazy-loaded via GET /api/resources/{id}/history when the
            # Change History modal opens — keeps the initial grid payload small/fast.
            "container_name": None, "update_history": None,
            "history_count": row["history_count"] or 0,
        })
    return result


@router.get("/resources")
def list_resources(db: Session = Depends(get_db), slim: bool = False):
    if slim:
        return _list_resources_slim(db)
    return _list_resources_full(db)


def _list_resources_full(db: Session) -> List[ResourceOut]:
    managed_platforms = _get_managed_platforms()

    if managed_platforms:
        resources = db.query(Resource).options(
            joinedload(Resource.plan)
        ).filter(
            Resource.platform.in_(managed_platforms)
        ).order_by(Resource.namespace, Resource.resource_name).all()
    else:
        resources = db.query(Resource).options(
            joinedload(Resource.plan)
        ).order_by(Resource.namespace, Resource.resource_name).all()

    enriched: List[ResourceOut] = []
    for r in resources:
        item = ResourceOut.model_validate(r)
        if getattr(r, "security_info", None):
            try:
                si = r.security_info or {}
                item.security_info = si
                try:
                    if isinstance(si.get("containers"), list) and si["containers"]:
                        item.containers = si["containers"]
                except Exception:
                    pass
                item.vulnerabilities = si.get("vulnerabilities") or None
                item.vulnerability_count = si.get("vulnerability_count") or 0
                item.advice = si.get("advice") or None
                if not si.get("exists_in_cluster", True):
                    item.advice = (item.advice or "") + (" | Resource missing in cluster - review or delete")
            except Exception:
                pass
        if not item.containers and r.image:
            img = r.image
            tag = img.rsplit(":", 1)[-1] if ":" in img else "latest"
            parts = img.split("/")
            name = parts[-1].split(":")[0]
            item.containers = [{"name": name, "image": img, "current_version": tag}]
        if getattr(r, "plan", None) is not None:
            item.note = r.plan.note
            item.planned_upgrade_at = r.plan.planned_upgrade_at
        item.product_name = getattr(r, "product_name", None)
        item.eol_date = getattr(r, "eol_date", None)
        try:
            hist_rows = (
                db.query(ResourceHistory)
                .filter(ResourceHistory.resource_id == r.id)
                .order_by(ResourceHistory.checked_at.desc())
                .limit(100)
                .all()
            )
            container_history: dict[str, list] = {}
            for h in hist_rows:
                container_key = h.container_name or "_main"
                if container_key not in container_history:
                    container_history[container_key] = []
                existing_versions = {item.version for item in container_history[container_key]}
                if h.version and h.version not in existing_versions:
                    container_history[container_key].append(HistoryItem(
                        version=h.version,
                        checked_at=h.checked_at,
                        image=h.image,
                        latest_version=h.latest_version,
                        version_diff=h.version_diff,
                        container_name=h.container_name,
                        source=getattr(h, 'source', None),
                        job_id=getattr(h, 'job_id', None),
                    ))
            for key in container_history:
                container_history[key].reverse()
            history_items: list[HistoryItem] = []
            for container_name, items in sorted(container_history.items()):
                history_items.extend(items)
            if history_items:
                item.update_history = history_items
        except Exception:
            pass
        enriched.append(item)

    return enriched


@router.get("/resources/{resource_id}/history")
def get_resource_history(resource_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Full change history for one resource — lazy-loaded by the Change History modal.

    Returns distinct (container, version) change points grouped per container,
    oldest-first within each container (the shape the frontend modal expects).
    Kept out of the initial /resources?slim=true payload so the grid loads fast.
    """
    hist_rows = (
        db.query(ResourceHistory)
        .filter(ResourceHistory.resource_id == resource_id)
        .order_by(ResourceHistory.checked_at.desc())
        .limit(200)
        .all()
    )
    container_history: dict[str, list] = {}
    for h in hist_rows:
        key = h.container_name or "_main"
        bucket = container_history.setdefault(key, [])
        seen = {e["version"] for e in bucket}
        if h.version and h.version not in seen:
            bucket.append({
                "version": h.version,
                "checked_at": str(h.checked_at) if h.checked_at else None,
                "image": h.image,
                "latest_version": h.latest_version,
                "version_diff": h.version_diff,
                "container_name": h.container_name,
                "source": getattr(h, "source", None),
                "job_id": getattr(h, "job_id", None),
            })
    for key in container_history:
        container_history[key].reverse()
    out: list[dict] = []
    for _, items in sorted(container_history.items()):
        out.extend(items)
    return out


@router.post("/resources/refresh")
async def refresh_resources(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Trigger async refresh and return immediately with sync log ID.
    Accepts JWT (Authorization: Bearer) or X-Backend-Token for federation.
    """
    import os
    from ..logging_utils import log_event
    import asyncio

    if x_backend_token:
        my_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        if x_backend_token != my_token:
            valid = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True,
            ).one_or_none()
            if not valid:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif token:
        pass
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    backend_type = os.getenv("BACKEND_TYPE", "primary")
    is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
    
    # For secondary backends in federation mode, skip local DB check
    # They've already been approved via federation registration with primary
    if is_federation_secondary:
        backend_endpoint = None  # Federation secondary doesn't have local backend_endpoints table
        log_event("refresh.federation_mode", backend=backend_name)
    else:
        backend_endpoint = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
        
        if not backend_endpoint:
            log_event("refresh.blocked.not_registered", backend=backend_name)
            raise HTTPException(
                status_code=403,
                detail=f"Backend '{backend_name}' is not registered. Please contact admin to register this backend."
            )
        
        if not backend_endpoint.approved:
            log_event("refresh.blocked.not_approved", backend=backend_name)
            raise HTTPException(
                status_code=403,
                detail=f"Backend '{backend_name}' is pending approval. Please contact admin to approve database writes."
            )
    
    # Decode username from token
    from jose import jwt
    import traceback
    # Use module-level JWT_SECRET from env var
    username = "unknown"
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username = claims.get("sub", "unknown")
    except Exception as e:
        log_event("refresh.jwt_decode_error", error=str(e))
        username = "api"
    
    # Check if this backend is already syncing
    if backend_name in _syncing_backends:
        raise HTTPException(
            status_code=409,
            detail=f"Backend '{backend_name}' is already syncing. Please wait for the current sync to complete."
        )
    
    # Check if dynamic discovery is enabled for this backend
    # For federation secondary backends, use dynamic discovery by default (they get managed products from primary)
    if is_federation_secondary:
        use_dynamic = True  # Federation backends always use dynamic discovery
        platform_name = os.getenv("BACKEND_PLATFORM", "unknown")
    else:
        use_dynamic = backend_endpoint.use_dynamic_discovery if backend_endpoint else False
        platform_name = backend_endpoint.platform if backend_endpoint else os.getenv("BACKEND_PLATFORM", "unknown")
    
    # Mark backend as syncing
    _syncing_backends.add(backend_name)
    
    try:
        # Create sync log entry immediately
        from datetime import datetime, timezone
        sync_log = SyncLog(
            backend_name=backend_name,
            platform=platform_name,
            triggered_by=username,
            status="running",
            started_at=datetime.now(timezone.utc)
        )
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)
        sync_log_id = sync_log.id
        
        log_event("refresh.async_start", 
                 backend=backend_name, 
                 sync_log_id=sync_log_id,
                 triggered_by=username,
                 use_dynamic=use_dynamic)
    except Exception as e:
        # If sync log creation fails, remove from syncing set
        _syncing_backends.discard(backend_name)
        log_event("refresh.sync_log_create_error", error=str(e), backend=backend_name)
        raise HTTPException(status_code=500, detail=f"Failed to create sync log: {str(e)}")
    
    # Start refresh in background
    async def _do_refresh():
        """Background task to run refresh"""
        # Create new DB session for background task
        from ..db import SessionLocal
        bg_db = SessionLocal()
        try:
            # For secondary backends: fetch shared config from primary
            backend_type = os.getenv("BACKEND_TYPE", "secondary")
            if backend_type == "secondary":
                try:
                    from ..services.config_cache import get_config_cache
                    cache = get_config_cache()
                    await cache.get_config(token=token, force_refresh=True)
                    log_event("refresh.config_cache_updated", backend=backend_name)
                except Exception as e:
                    log_event("refresh.config_cache_error", error=str(e))
            
            # Run the actual refresh in a thread pool to avoid blocking the event loop
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _sync_executor,
                refresh_from_sources,
                bg_db,
                username,  # triggered_by
                use_dynamic,
                sync_log_id
            )
            log_event("refresh.async_complete", 
                     sync_log_id=sync_log_id,
                     refreshed=result.get("refreshed", 0),
                     errors=result.get("errors", 0))
        except Exception as exc:
            log_event("refresh.async_error", 
                     sync_log_id=sync_log_id,
                     error=str(exc))
            # Update sync log to error status
            try:
                from datetime import datetime, timezone
                sync_log_obj = bg_db.query(SyncLog).filter(SyncLog.id == sync_log_id).one_or_none()
                if sync_log_obj:
                    now = datetime.now(timezone.utc)
                    sync_log_obj.finished_at = now
                    sync_log_obj.status = "error"
                    sync_log_obj.error_message = str(exc)
                    # Calculate duration
                    if sync_log_obj.started_at:
                        try:
                            delta = now - sync_log_obj.started_at
                            sync_log_obj.duration_seconds = int(delta.total_seconds())
                        except Exception:
                            pass
                    bg_db.commit()
            except Exception as e:
                log_event("refresh.sync_log_update_error", error=str(e))
        finally:
            bg_db.close()
            # Remove backend from syncing set
            _syncing_backends.discard(backend_name)
    
    # Launch background task
    asyncio.create_task(_do_refresh())
    
    # Return immediately
    return {
        "status": "started",
        "sync_log_id": sync_log_id,
        "message": f"Sync started in background. Check sync logs (ID: {sync_log_id}) for status."
    }


@router.post("/resources/sync-product")
async def sync_single_product(
    request: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Sync resources for a single product only.
    Much faster than full sync - only discovers resources matching this product.
    Admin role required.
    """
    # Admin role required for sync operations
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to perform sync")
    
    import os
    import asyncio
    from ..logging_utils import log_event
    from ..services.refresh import refresh_single_product
    
    product_name = request.get("product_name", "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="product_name is required")
    
    backend_name = os.getenv("BACKEND_NAME", "default-backend")
    log_event("sync_product.api.start", product=product_name, backend=backend_name)
    
    async def _do_product_sync():
        from ..db import SessionLocal
        local_db = SessionLocal()
        try:
            result = refresh_single_product(local_db, product_name, triggered_by="api")
            log_event("sync_product.api.complete", product=product_name, **result)
        except Exception as e:
            log_event("sync_product.api.error", product=product_name, error=str(e))
        finally:
            local_db.close()
    
    # Run in background
    asyncio.create_task(_do_product_sync())
    
    return {
        "status": "started",
        "product": product_name,
        "message": f"Sync started for product '{product_name}'"
    }


@router.get("/resources.csv")
def export_resources_csv(db: Session = Depends(get_db), sep: str = ",") -> StreamingResponse:
    resources = list_resources(db)
    header = [
        "platform","namespace","name","kind","container","image","current","latest","diff",
        "vulnerability_count","cves","advice","note","upgrade_plan","eol","product_name",
        "twistlock_critical","twistlock_high","twistlock_medium","twistlock_low","twistlock_total","twistlock_risk_factors",
        "checked"
    ]
    rows = [header]
    for r in resources:  # type: ignore
        # Extract Twistlock data
        tw = (r.security_info or {}).get("twistlock") if getattr(r, "security_info", None) else None
        dist = (tw or {}).get("vulnerabilityDistribution") or {}
        tw_crit = str(dist.get("critical", 0))
        tw_high = str(dist.get("high", 0))
        tw_med = str(dist.get("medium", 0))
        tw_low = str(dist.get("low", 0))
        tw_total = str(dist.get("total", 0))
        tw_risk = str((tw or {}).get("riskFactorCount", 0))
        
        rows.append([
            r.platform,
            r.namespace,
            r.resource_name,
            r.kind,
            getattr(r, "container_name", None) or "",
            r.image or "",
            r.current_version or "",
            r.latest_version or "",
            r.version_diff or "",
            str(r.vulnerability_count or 0),
            ";".join(r.vulnerabilities or []) if getattr(r, "vulnerabilities", None) else "",
            r.advice or "",
            getattr(r, "note", None) or "",
            (r.planned_upgrade_at.isoformat() if getattr(r, "planned_upgrade_at", None) else ""),
            # EOL: real date if known, else the LLM-derived support status + note (marked AI).
            (r.eol_date or (
                f"{r.eol_support_status} (AI): {r.eol_support_note or ''}".strip()
                if getattr(r, "eol_support_status", None) else "")),
            (getattr(r, "product_name", None) or ""),
            tw_crit,
            tw_high,
            tw_med,
            tw_low,
            tw_total,
            tw_risk,
            (r.last_checked_at.isoformat() if r.last_checked_at else ""),
        ])
    # Normalize separator (support 'semicolon'/'tab' aliases)
    if sep.lower() in {"semicolon", ";"}:
        sep = ";"
    elif sep.lower() in {"tab", "\t"}:
        sep = "\t"
    else:
        sep = ","

    # Build CSV with UTF-8 BOM and Excel 'sep=' hint so Excel uses our delimiter
    def q(v: str) -> str:
        s = str(v)
        if '"' in s:
            s = s.replace('"', '""')
        if any(d in s for d in [sep, '\n', '\r']):
            return f'"{s}"'
        return s

    csv_lines = []
    for row in rows:
        safe = [q(v) for v in row]
        csv_lines.append(sep.join(safe))
    data = ("\ufeff" + "\n".join(csv_lines)).encode("utf-8-sig")
    return StreamingResponse(BytesIO(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=resources.csv"})


@router.get("/resources.xlsx")
def export_resources_excel(db: Session = Depends(get_db)) -> StreamingResponse:
    resources = list_resources(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resources"
    headers = [
        "Platform","Namespace","Name","Kind","Image","Current","Latest","Diff","Vulns","CVEs","Advice","Note","Upgrade Plan","EOL","Product","Checked"
    ]
    ws.append(headers)
    for r in resources:  # type: ignore
        ws.append([
            r.platform,
            r.namespace,
            r.resource_name,
            r.kind,
            r.image or "",
            r.current_version or "",
            r.latest_version or "",
            r.version_diff or "",
            (r.vulnerability_count or 0),
            ";".join(r.vulnerabilities or []) if getattr(r, "vulnerabilities", None) else "",
            r.advice or "",
            getattr(r, "note", None) or "",
            (r.planned_upgrade_at.isoformat() if getattr(r, "planned_upgrade_at", None) else ""),
            # EOL: real date if known, else the LLM-derived support status + note (marked AI).
            (r.eol_date or (
                f"{r.eol_support_status} (AI): {r.eol_support_note or ''}".strip()
                if getattr(r, "eol_support_status", None) else "")),
            (getattr(r, "product_name", None) or ""),
            (r.last_checked_at.isoformat() if r.last_checked_at else ""),
        ])
    # Autosize columns
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(60, max(12, max_len + 2))
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=resources.xlsx"})


@router.delete("/resources/{resource_id}")
def delete_resource(resource_id: int, db: Session = Depends(get_db)) -> dict:
    res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
    if not res:
        raise HTTPException(status_code=404, detail="Resource not found")
    db.delete(res)
    db.commit()
    return {"ok": True}


@router.post("/resources/{resource_id}/ask-ai")
def ask_ai_upgrade(
    resource_id: int,
    force: bool = False,
    token: Optional[str] = Depends(oauth2_scheme_optional),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db)
) -> dict:
    """Ask AI for upgrade advice on a specific resource. Result is cached.
    Accepts JWT (Authorization: Bearer) or X-Backend-Token for federation."""
    if x_backend_token:
        my_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        if x_backend_token != my_token:
            valid = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True,
            ).one_or_none()
            if not valid:
                raise HTTPException(status_code=401, detail="Invalid backend token")
    elif token:
        if _decode_role(token) not in ("admin", "editor"):
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
    if not res:
        raise HTTPException(status_code=404, detail="Resource not found")

    si = res.security_info or {}

    # Return cached if fresh (24h) and not forced
    if not force and si.get("llm_upgrade_at"):
        try:
            from datetime import datetime, timezone
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(si["llm_upgrade_at"])).total_seconds() / 3600
            if age_h < 24 and si.get("llm_upgrade_advice"):
                log_event("llm.upgrade.cache_hit", resource=res.resource_name, age_hours=round(age_h, 1))
                return {"advice": si["llm_upgrade_advice"], "cached": True}
        except Exception:
            pass

    from ..services.image_cache import get_llm_config, fetch_llm_from_primary, is_primary_backend
    llm_cfg = get_llm_config(db)
    advice = None

    if llm_cfg.get("mode") == "primary" and not is_primary_backend():
        advice = fetch_llm_from_primary(
            db, res.image or "",
            resource_name=res.resource_name,
            current_version=res.current_version or "",
            latest_version=res.latest_version or "",
            version_diff=res.version_diff or "",
            eol_date=str(res.eol_date) if res.eol_date else "",
            advice_type="upgrade",
        )
    else:
        from ..services.llm_advice import generate_upgrade_advice
        advice = generate_upgrade_advice(
            product_name=res.product_name or res.resource_name,
            image=res.image or "",
            current_version=res.current_version or "",
            latest_version=res.latest_version or "",
            version_diff=res.version_diff or "",
            twistlock=si.get("twistlock"),
            trivy=si.get("trivy"),
            eol_date=str(res.eol_date) if res.eol_date else None,
        )

    if advice is None:
        raise HTTPException(status_code=503, detail="LLM not available or disabled")

    from datetime import datetime, timezone
    si_new = dict(si)
    si_new["llm_upgrade_advice"] = advice
    si_new["llm_upgrade_at"] = datetime.now(timezone.utc).isoformat()
    res.security_info = si_new
    # Keep the grid's precomputed summary in sync with security_info.
    try:
        from ..services.refresh import build_resource_summary
        res.sec_summary = build_resource_summary(si_new, res.image)
    except Exception:
        pass
    db.commit()

    return {"advice": advice, "cached": False}


@router.post("/resources/{resource_id}/sync")
async def sync_single_resource(
    resource_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Sync a single resource: fetch current state from K8s and latest version from registry.
    Supports federation - forwards to remote backend if resource is on a different platform.
    Admin role required."""
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to perform sync")
    
    from ..services.kube import KubeClient
    from ..logging_utils import log_event
    import os
    import aiohttp
    import ssl
    
    res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
    if not res:
        raise HTTPException(status_code=404, detail="Resource not found")
    
    # Check if this resource is managed by this backend or a remote one
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    if res.platform != local_platform:
        # Resource is on a remote platform - forward the request
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.platform == res.platform,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True,
        ).first()
        
        if not backend:
            log_event("resource.sync.no_backend", resource_id=resource_id, platform=res.platform)
            return {
                "ok": False,
                "error": f"No backend found for platform: {res.platform}",
                "resource_id": resource_id,
            }
        
        log_event("resource.sync.forward", resource_id=resource_id, backend=backend.name, platform=res.platform)
        
        try:
            # Forward to remote backend using unique identifier (not ID which differs per backend)
            ssl_context = None
            if backend.skip_tls_verify:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
            
            async with aiohttp.ClientSession(connector=connector) as session:
                # Use sync-by-key endpoint instead of sync-by-id (IDs differ across backends)
                url = f"{backend.api_url.rstrip('/')}/api/resources/sync-by-key"
                headers = {
                    "X-Backend-Token": backend.auth_token,
                    "Content-Type": "application/json",
                }
                payload = {
                    "namespace": res.namespace,
                    "resource_name": res.resource_name,
                    "kind": res.kind,
                }
                
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                    data = await resp.json()
                    
                    if data.get("ok"):
                        log_event("resource.sync.forward.success", resource_id=resource_id, backend=backend.name)
                        return data
                    else:
                        log_event("resource.sync.forward.failed", resource_id=resource_id, error=data.get("error"))
                        return data
                        
        except Exception as e:
            log_event("resource.sync.forward.error", resource_id=resource_id, backend=backend.name, error=str(e))
            return {
                "ok": False,
                "error": f"Failed to forward to backend {backend.name}: {str(e)}",
                "resource_id": resource_id,
            }
    
    # Local resource - sync ONLY the clicked resource (single-row "Sync"). Previously this
    # discovered the whole product in the namespace and refreshed every resource of it, so
    # one row's Sync fanned out to N resources — slow and surprising. New product resources
    # are still picked up by the nightly cron and the real-time watch.
    try:
        from ..services.refresh import refresh_from_sources
        from ..services.sources_reader import SourceItem

        target_items = [SourceItem(
            platform=res.platform,
            namespace=res.namespace,
            kind=res.kind,
            resource_name=res.resource_name,
            product_name=res.product_name or "",
        )]

        log_event("resource.sync.filtered_start",
                 resource_id=resource_id,
                 product=res.product_name,
                 namespace=res.namespace,
                 items_count=len(target_items))
        
        # Run the same full sync logic as nightly, but with filtered items and no cleanup
        result = refresh_from_sources(
            db,
            triggered_by="row-sync",
            use_dynamic=False,
            target_items=target_items,
            skip_cleanup=True,
        )
        
        discovered_new = max(0, result.get("refreshed", 0) - 1)
        
        return {
            "ok": True,
            "resource_id": resource_id,
            "refreshed": result.get("refreshed", 0),
            "discovered_new": discovered_new,
            "errors": result.get("errors"),
        }
        
    except Exception as e:
        log_event("resource.sync.error", resource_id=resource_id, error=str(e))
        return {
            "ok": False,
            "error": str(e),
            "resource_id": resource_id,
        }


@router.post("/resources/sync-by-key")
def sync_resource_by_key(
    request: dict,
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> dict:
    """Sync a resource by unique key (namespace, resource_name, kind).
    
    This endpoint is used for federation - when primary backend forwards
    sync requests to secondary backends, IDs differ so we use unique keys.
    Accepts either admin JWT token (Authorization header) or X-Backend-Token for federation.
    """
    import os
    import traceback
    
    # Log entry point for debugging
    log_event("resource.sync_by_key.entry", 
              has_backend_token=bool(x_backend_token),
              has_authorization=bool(authorization),
              request_keys=list(request.keys()) if request else [])
    
    try:
        # Check for backend-to-backend authentication first (federation)
        if x_backend_token:
            # On secondary backends: verify against this backend's own auth token (env var)
            # On primary backend: verify against backend_endpoints table
            my_auth_token = os.getenv("BACKEND_AUTH_TOKEN", "")
            backend_type = os.getenv("BACKEND_TYPE", "secondary")
            
            if backend_type == "secondary" and my_auth_token:
                # Secondary backend: check if incoming token matches our own token
                if x_backend_token != my_auth_token:
                    raise HTTPException(status_code=403, detail="Invalid backend token")
                log_event("resource.sync_by_key.backend_auth", source="federation")
            else:
                # Primary backend: look up in database (for completeness, though primary usually doesn't receive these)
                backend = db.query(BackendEndpoint).filter(
                    BackendEndpoint.auth_token == x_backend_token,
                    BackendEndpoint.approved == True
                ).one_or_none()
                if not backend:
                    raise HTTPException(status_code=403, detail="Invalid backend token")
                log_event("resource.sync_by_key.backend_auth", backend=backend.name)
        elif authorization:
            # Extract token from "Bearer <token>" format
            token = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
            # Check for admin role in JWT
            if _decode_role(token) != "admin":
                raise HTTPException(status_code=403, detail="Admin role required to perform sync")
        else:
            # No authentication provided
            raise HTTPException(status_code=401, detail="Authentication required")
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as auth_error:
        log_event("resource.sync_by_key.auth_error", error=str(auth_error), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Authentication error: {str(auth_error)}")
    
    import traceback
    
    namespace = request.get("namespace", "").strip()
    resource_name = request.get("resource_name", "").strip()
    kind = request.get("kind", "").strip()
    
    if not namespace or not resource_name or not kind:
        return {
            "ok": False,
            "error": "namespace, resource_name, and kind are required",
        }
    
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    # Find resource by unique key on this backend's platform
    res = db.query(Resource).filter(
        Resource.platform == local_platform,
        Resource.namespace == namespace,
        Resource.resource_name == resource_name,
        Resource.kind.ilike(kind),
    ).first()
    
    if not res:
        log_event("resource.sync_by_key.not_found", 
                  namespace=namespace, resource_name=resource_name, kind=kind, platform=local_platform)
        return {
            "ok": False,
            "error": f"Resource not found: {namespace}/{resource_name} ({kind}) on platform {local_platform}",
        }
    
    log_event("resource.sync_by_key.start", resource_id=res.id, name=res.resource_name)
    
    # Sync ONLY the clicked resource. Previously this discovered the whole product in the
    # namespace and refreshed every resource of it, so a single-row "Sync" fanned out to N
    # resources — slow (each does version/twistlock/trivy lookups) and surprising. New
    # product resources are still picked up by the nightly cron and the real-time watch.
    try:
        from ..services.refresh import refresh_from_sources
        from ..services.sources_reader import SourceItem

        target_items = [SourceItem(
            platform=res.platform,
            namespace=res.namespace,
            kind=res.kind,
            resource_name=res.resource_name,
            product_name=res.product_name or "",
        )]

        log_event("resource.sync_by_key.filtered_start",
                 resource_id=res.id,
                 product=res.product_name,
                 namespace=res.namespace,
                 items_count=len(target_items))
        
        result = refresh_from_sources(
            db,
            triggered_by="row-sync",
            use_dynamic=False,
            target_items=target_items,
            skip_cleanup=True,
        )
        
        discovered_new = max(0, result.get("refreshed", 0) - 1)
        
        return {
            "ok": True,
            "resource_id": res.id,
            "resource_name": res.resource_name,
            "refreshed": result.get("refreshed", 0),
            "discovered_new": discovered_new,
            "errors": result.get("errors"),
        }
        
    except Exception as e:
        log_event("resource.sync_by_key.error", 
                  resource_id=res.id,
                  error=str(e),
                  traceback=traceback.format_exc())
        return {
            "ok": False,
            "error": str(e),
            "resource_id": res.id,
        }


@router.post("/resources/cleanup-internal-registry")
def cleanup_internal_registry(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Remove all resources with excluded registry images (admin only)"""
    from ..routers.auth import _decode_role
    from ..services.exclusions import should_skip_image
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Find resources with excluded registry images (across all backends/platforms)
    deleted_count = 0
    deleted_by_platform = {}
    resources = db.query(Resource).all()
    
    for res in resources:
        should_delete = False
        
        # Check main image
        if res.image and should_skip_image(res.image, db):
            should_delete = True
        
        # Check containers in security_info
        if not should_delete and res.security_info:
            containers = res.security_info.get("containers", [])
            if isinstance(containers, list):
                for container in containers:
                    if isinstance(container, dict):
                        img = container.get("image", "")
                        if img and should_skip_image(img, db):
                            should_delete = True
                            break
        
        if should_delete:
            log_event("cleanup.excluded_registry.delete", 
                     resource=res.resource_name, 
                     namespace=res.namespace,
                     platform=res.platform,
                     image=res.image)
            db.delete(res)
            deleted_count += 1
            # Track deletions per platform
            deleted_by_platform[res.platform] = deleted_by_platform.get(res.platform, 0) + 1
    
    db.commit()
    log_event("cleanup.excluded_registry.done", 
             deleted=deleted_count,
             platforms=list(deleted_by_platform.keys()),
             per_platform=deleted_by_platform)
    
    return {
        "ok": True,
        "deleted": deleted_count,
        "message": f"Removed {deleted_count} excluded registry resources",
        "per_platform": deleted_by_platform
    }


@router.post("/sync-logs/{sync_log_id}/cancel")
def cancel_sync_job(
    sync_log_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Cancel a running sync job.
    The sync will stop at the next checkpoint and mark as 'cancelled'.
    """
    from ..routers.auth import _decode_role
    from datetime import datetime, timezone
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Find the sync log
    sync_log = db.query(SyncLog).filter(SyncLog.id == sync_log_id).one_or_none()
    if not sync_log:
        raise HTTPException(status_code=404, detail="Sync log not found")
    
    if sync_log.status != "running":
        raise HTTPException(status_code=400, detail=f"Cannot cancel sync with status '{sync_log.status}'. Only 'running' syncs can be cancelled.")
    
    # Mark as cancelled in the tracking set
    mark_sync_cancelled(sync_log_id)
    
    # Update the sync log status
    sync_log.status = "cancelled"
    sync_log.finished_at = datetime.now(timezone.utc)
    sync_log.error_message = "Cancelled by user"
    
    # Calculate duration
    if sync_log.started_at:
        try:
            delta = sync_log.finished_at - sync_log.started_at
            sync_log.duration_seconds = int(delta.total_seconds())
        except Exception:
            pass
    
    db.commit()
    
    log_event("synclog.cancelled", sync_log_id=sync_log_id, backend=sync_log.backend_name)
    
    return {
        "ok": True,
        "sync_log_id": sync_log_id,
        "message": "Sync job cancelled. It will stop at the next checkpoint."
    }


@router.post("/sync-logs/cleanup-stuck")
def cleanup_stuck_sync_logs(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Clean up stuck sync logs that are marked as 'running' (admin only).
    Useful after backend restarts or crashes.
    """
    from ..routers.auth import _decode_role
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from datetime import datetime, timezone, timedelta
    
    # Mark all running logs as timeout
    stuck_logs = db.query(SyncLog).filter(SyncLog.status == "running").all()
    cleaned_count = 0
    
    for log in stuck_logs:
        log.status = "timeout"
        if not log.finished_at:
            # Set finished_at to 5 minutes after start, or now if started_at is missing
            if log.started_at:
                log.finished_at = log.started_at + timedelta(minutes=5)
            else:
                log.finished_at = datetime.now(timezone.utc)
        if not log.error_message:
            log.error_message = "Sync did not complete (backend restart or crash)"
        # Calculate duration if not set
        if log.started_at and log.finished_at and not log.duration_seconds:
            try:
                delta = log.finished_at - log.started_at
                log.duration_seconds = int(delta.total_seconds())
            except Exception:
                pass
        cleaned_count += 1
    
    db.commit()
    log_event("synclog.cleanup.manual", cleaned=cleaned_count)
    
    return {
        "ok": True,
        "cleaned": cleaned_count,
        "message": f"Cleaned up {cleaned_count} stuck sync logs"
    }


@router.post("/resources/cleanup-all")
def cleanup_all_resources(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Remove ALL resources from the database (admin only) - USE WITH CAUTION!"""
    from ..routers.auth import _decode_role
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Count resources before deletion
    total_count = db.query(Resource).count()
    
    # Count by platform
    from sqlalchemy import func
    platform_counts = db.query(
        Resource.platform, 
        func.count(Resource.id)
    ).group_by(Resource.platform).all()
    
    platforms_summary = {platform: count for platform, count in platform_counts}
    
    # Delete all resources
    db.query(Resource).delete()
    db.commit()
    
    log_event("cleanup.all_resources.done", 
             deleted=total_count,
             platforms=list(platforms_summary.keys()),
             per_platform=platforms_summary)
    
    return {
        "ok": True,
        "deleted": total_count,
        "message": f"Removed ALL {total_count} resources from database",
        "per_platform": platforms_summary
    }


@router.post("/resources/cleanup-orphaned")
def cleanup_orphaned_resources(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Remove resources whose platform doesn't match any registered backend (admin only)"""
    from ..routers.auth import _decode_role
    import os
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Check if this is a secondary backend in federation mode
    backend_type = os.getenv("BACKEND_TYPE", "primary")
    is_federation_secondary = (backend_type == "secondary")
    
    # On secondary backends, only clean up resources that don't match THIS backend's platform
    # On primary backend, check against ALL registered backends
    if is_federation_secondary:
        # Secondary backend: only clean up if resource platform doesn't match our own platform
        backend_platform = os.getenv("BACKEND_PLATFORM", "")
        if not backend_platform:
            raise HTTPException(status_code=500, detail="BACKEND_PLATFORM not configured")
        
        valid_platforms = {backend_platform}
        log_event("cleanup.orphaned.start", 
                 backend_type="secondary",
                 own_platform=backend_platform,
                 valid_platforms=list(valid_platforms))
    else:
        # Primary backend: check against all registered backends
        registered_platforms = db.query(BackendEndpoint.platform).filter(
            BackendEndpoint.enabled == True
        ).all()
        valid_platforms = {p[0] for p in registered_platforms}
        
        # Safety check: if no backends registered, abort to prevent accidental deletion
        if not valid_platforms:
            log_event("cleanup.orphaned.abort", reason="No registered backends found")
            raise HTTPException(
                status_code=400, 
                detail="No registered backends found. Cannot determine valid platforms. Aborting to prevent data loss."
            )
        
        log_event("cleanup.orphaned.start", 
                 backend_type="primary",
                 valid_platforms=list(valid_platforms))
    
    # Find all resources with platforms that don't match any backend
    all_resources = db.query(Resource).all()
    orphaned_resources = [r for r in all_resources if r.platform not in valid_platforms]
    
    deleted_count = 0
    deleted_by_platform = {}
    
    for resource in orphaned_resources:
        platform = resource.platform
        deleted_by_platform[platform] = deleted_by_platform.get(platform, 0) + 1
        db.delete(resource)
        deleted_count += 1
    
    db.commit()
    
    log_event("cleanup.orphaned.done", 
             backend_type=backend_type,
             deleted=deleted_count,
             orphaned_platforms=list(deleted_by_platform.keys()),
             per_platform=deleted_by_platform)
    
    return {
        "ok": True,
        "deleted": deleted_count,
        "message": f"Removed {deleted_count} orphaned resources from {len(deleted_by_platform)} unregistered platforms",
        "orphaned_platforms": list(deleted_by_platform.keys()),
        "per_platform": deleted_by_platform
    }


@router.post("/resources/cleanup-by-product")
def cleanup_resources_by_product(
    body: dict,
    token: Optional[str] = Depends(oauth2_scheme_optional),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db),
) -> dict:
    """Delete all resources matching a given product_name (admin or federation)."""
    import os
    from ..routers.auth import _decode_role

    if x_backend_token:
        my_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        if x_backend_token != my_token:
            valid = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True,
            ).one_or_none()
            if not valid:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif token:
        if _decode_role(token) != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    product_name = (body.get("product_name") or "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="product_name is required")

    resource_ids = [
        r.id for r in db.query(Resource.id).filter(Resource.product_name == product_name).all()
    ]
    deleted = len(resource_ids)
    if resource_ids:
        db.query(ResourceHistory).filter(ResourceHistory.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        db.query(ResourcePlan).filter(ResourcePlan.resource_id.in_(resource_ids)).delete(synchronize_session=False)
        db.query(Resource).filter(Resource.id.in_(resource_ids)).delete(synchronize_session=False)
    db.commit()
    log_event("cleanup.by_product.done", product=product_name, deleted=deleted)
    return {"ok": True, "deleted": deleted, "product": product_name}


@router.post("/resources/cleanup-unmanaged")
def cleanup_unmanaged_product_resources(
    body: Optional[dict] = None,
    token: Optional[str] = Depends(oauth2_scheme_optional),
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    db: Session = Depends(get_db)
) -> dict:
    """Remove resources whose product_name is not in the managed products list (admin or federation)."""
    from ..routers.auth import _decode_role
    import os

    if x_backend_token:
        my_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        if x_backend_token != my_token:
            valid = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True,
            ).one_or_none()
            if not valid:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif token:
        if _decode_role(token) != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Prefer managed_names from request body (sent by primary frontend)
    body_names = body.get("managed_names") if isinstance(body, dict) else None
    if body_names and isinstance(body_names, list) and len(body_names) > 0:
        managed_names = {n.lower() for n in body_names if isinstance(n, str)}
    else:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        managed = db.query(ManagedProduct).filter(
            ManagedProduct.backend_name == backend_name
        ).all()
        managed_names = {p.product_name.lower() for p in managed}

    if not managed_names:
        log_event("cleanup.unmanaged.aborted", reason="empty_managed_list")
        return {
            "ok": True,
            "deleted": 0,
            "message": "No managed products found — aborting to prevent data loss",
            "per_product": {},
        }

    all_resources = db.query(Resource).all()
    to_delete = [
        r for r in all_resources
        if r.product_name and r.product_name.lower() not in managed_names
    ]

    deleted_by_product: dict = {}
    if to_delete:
        to_delete_ids = [r.id for r in to_delete]
        db.query(ResourceHistory).filter(ResourceHistory.resource_id.in_(to_delete_ids)).delete(synchronize_session=False)
        db.query(ResourcePlan).filter(ResourcePlan.resource_id.in_(to_delete_ids)).delete(synchronize_session=False)
        for r in to_delete:
            pn = r.product_name or "unknown"
            deleted_by_product[pn] = deleted_by_product.get(pn, 0) + 1
        db.query(Resource).filter(Resource.id.in_(to_delete_ids)).delete(synchronize_session=False)

    db.commit()
    total = len(to_delete)
    log_event("cleanup.unmanaged.done", deleted=total, products=deleted_by_product, managed_count=len(managed_names))
    return {
        "ok": True,
        "deleted": total,
        "message": f"Removed {total} resources from {len(deleted_by_product)} unmanaged products",
        "per_product": deleted_by_product,
    }


@router.post("/resources/cleanup-system-namespaces")
def cleanup_system_namespaces(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Remove all resources from excluded namespaces (admin only)"""
    from ..routers.auth import _decode_role
    from ..services.exclusions import should_skip_namespace
    
    if _decode_role(token) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Find resources in excluded namespaces (across all backends/platforms)
    deleted_count = 0
    deleted_by_platform = {}
    resources = db.query(Resource).all()
    
    for res in resources:
        if should_skip_namespace(res.namespace, db):
            log_event("cleanup.excluded_namespace.delete", 
                     resource=res.resource_name, 
                     namespace=res.namespace,
                     platform=res.platform,
                     kind=res.kind)
            db.delete(res)
            deleted_count += 1
            # Track deletions per platform
            deleted_by_platform[res.platform] = deleted_by_platform.get(res.platform, 0) + 1
    
    db.commit()
    log_event("cleanup.excluded_namespaces.done", 
             deleted=deleted_count, 
             platforms=list(deleted_by_platform.keys()),
             per_platform=deleted_by_platform)
    
    return {
        "ok": True,
        "deleted": deleted_count,
        "message": f"Removed {deleted_count} excluded namespace resources"
    }


@router.post("/resources/{resource_id}/plan")
def upsert_resource_plan(resource_id: int, body: PlanUpdate, db: Session = Depends(get_db)) -> dict:
    res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
    if not res:
        raise HTTPException(status_code=404, detail="Resource not found")

    data = body.model_dump(exclude_unset=True)
    apply_to_all = data.pop("apply_to_all", False)
    match_image = data.pop("match_image", None)

    if apply_to_all and match_image:
        target_ids = _find_resources_with_image(db, res.platform, match_image)
        updated = 0
        for rid in target_ids:
            p = db.query(ResourcePlan).filter(ResourcePlan.resource_id == rid).one_or_none()
            if p is None:
                p = ResourcePlan(resource_id=rid)
                db.add(p)
            if "note" in data:
                p.note = (data["note"] or None)
            if "planned_upgrade_at" in data:
                p.planned_upgrade_at = (data["planned_upgrade_at"] or None)
            updated += 1
        db.commit()
        field = "note" if "note" in data else "planned_upgrade_at"
        log_event("plan.applied_to_all", source_id=resource_id, field=field, match_image=match_image, platform=res.platform, updated=updated)
        return {"ok": True, "note": data.get("note"), "planned_upgrade_at": data.get("planned_upgrade_at"), "updated_count": updated}

    plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == resource_id).one_or_none()
    if plan is None:
        plan = ResourcePlan(resource_id=resource_id)
        db.add(plan)
    if "note" in data:
        plan.note = (data["note"] or None)
    if "planned_upgrade_at" in data:
        plan.planned_upgrade_at = (data["planned_upgrade_at"] or None)
    db.commit()
    return {"ok": True, "note": plan.note, "planned_upgrade_at": plan.planned_upgrade_at}


def _find_resources_with_image(db: Session, platform: str, image: str) -> list[int]:
    """Find all resource IDs on the given platform that have the exact image in any container."""
    candidates = db.query(Resource).filter(Resource.platform == platform).all()
    matched = []
    for r in candidates:
        if r.image == image:
            matched.append(r.id)
            continue
        for c in ((r.security_info or {}).get("containers") or []):
            if c.get("image") == image:
                matched.append(r.id)
                break
    return matched


class BulkPlanUpdate(BaseModel):
    product_name: str
    planned_upgrade_at: Optional[str] = None


@router.post("/resources/bulk-plan")
def bulk_update_plan(
    body: BulkPlanUpdate,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    # Only admins can bulk update
    from jose import jwt
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        role = claims.get("role")
        if role != "admin":
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Find all resources with matching product_name
    resources = db.query(Resource).filter(Resource.product_name == body.product_name).all()
    if not resources:
        raise HTTPException(status_code=404, detail=f"No resources found for product: {body.product_name}")
    
    updated_count = 0
    for res in resources:
        # Only update if no existing plan (preserve existing individual plans)
        existing_plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == res.id).one_or_none()
        if existing_plan is None:
            plan = ResourcePlan(resource_id=res.id)
            plan.planned_upgrade_at = body.planned_upgrade_at
            db.add(plan)
            updated_count += 1
    
    db.commit()
    return {"ok": True, "updated": updated_count, "total": len(resources), "product": body.product_name}


class JobPlanUpdate(BaseModel):
    """Set upgrade plans for resources matching a job's criteria (federation-compatible)"""
    product_name: str
    target_platforms: Optional[List[str]] = None
    target_namespaces: Optional[List[str]] = None
    planned_upgrade_at: Optional[str] = None
    job_note: Optional[str] = None


@router.post("/resources/set-job-plans")
def set_job_plans(
    body: JobPlanUpdate,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Set planned_upgrade_at for resources matching job criteria. 
    Used by primary backend to sync plans to remote backends in federation."""
    from jose import jwt
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        role = claims.get("role")
        if role != "admin":
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Build query for matching resources
    query = db.query(Resource).filter(Resource.product_name == body.product_name)
    
    if body.target_platforms:
        query = query.filter(Resource.platform.in_(body.target_platforms))
    
    if body.target_namespaces:
        query = query.filter(Resource.namespace.in_(body.target_namespaces))
    
    resources = query.all()
    
    updated_count = 0
    for res in resources:
        plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == res.id).one_or_none()
        if plan is None:
            plan = ResourcePlan(resource_id=res.id)
            db.add(plan)
        
        if body.planned_upgrade_at:
            plan.planned_upgrade_at = body.planned_upgrade_at
        else:
            plan.planned_upgrade_at = None  # Clear the plan
        
        if body.job_note and not plan.note:
            plan.note = body.job_note
        
        updated_count += 1
    
    db.commit()
    log_event("resources.job_plans_set", product=body.product_name, updated=updated_count)
    return {"ok": True, "updated": updated_count, "product": body.product_name}


@router.get("/sync-logs")
def get_sync_logs(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    import os
    from sqlalchemy import or_
    # Only return sync logs for THIS backend (important when multiple backends share same database)
    backend_name = os.getenv("BACKEND_NAME", "default-backend")

    # Exclude real-time watch refreshes — they would flood this page and bury the
    # nightly/manual syncs. Watch changes are recorded in resource_history (source=watch).
    logs = db.query(SyncLog).filter(
        SyncLog.backend_name == backend_name,
        or_(SyncLog.triggered_by != "watch", SyncLog.triggered_by.is_(None)),
    ).order_by(SyncLog.started_at.desc()).limit(limit).all()
    
    return [
        {
            "id": log.id,
            "backend_name": log.backend_name,
            "platform": log.platform,
            "started_at": log.started_at,
            "finished_at": log.finished_at,
            "triggered_by": log.triggered_by,
            "status": log.status,
            "resources_refreshed": log.resources_refreshed,
            "errors": log.errors,
            "error_message": log.error_message,
            "duration_seconds": (
                (log.finished_at - log.started_at).total_seconds() 
                if log.finished_at and log.started_at 
                else None
            ) if hasattr(log.finished_at, '__sub__') else None
        }
        for log in logs
    ]


@router.get("/user-charts")
def get_user_charts(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> list[dict]:
    from jose import jwt
    import json
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username = claims.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    charts = db.query(UserChart).filter(UserChart.username == username).order_by(UserChart.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "chart_name": c.chart_name,
            "group_by": c.group_by,
            "count_by": c.count_by,
            "filter_by": c.filter_by,  # Legacy support
            "filter_value": c.filter_value,  # Legacy support
            "filters": json.loads(c.filters) if c.filters else None,  # New multi-filter support
            "chart_type": c.chart_type,
            "created_at": c.created_at
        }
        for c in charts
    ]


@router.post("/user-charts")
def create_user_chart(body: UserChartCreate, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    from jose import jwt
    import json
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username = claims.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Convert filters list to JSON string
    filters_json = None
    if body.filters:
        filters_json = json.dumps(body.filters)
    
    chart = UserChart(
        username=username,
        chart_name=body.chart_name,
        group_by=body.group_by,
        count_by=body.count_by,
        filter_by=getattr(body, 'filter_by', None),  # Legacy
        filter_value=getattr(body, 'filter_value', None),  # Legacy
        filters=filters_json,  # New multi-filter
        chart_type=getattr(body, 'chart_type', 'bar')
    )
    db.add(chart)
    db.commit()
    db.refresh(chart)
    return {"ok": True, "id": chart.id}


@router.put("/user-charts/{chart_id}")
def update_user_chart(chart_id: int, body: UserChartCreate, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    from jose import jwt
    import json
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username = claims.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    chart = db.query(UserChart).filter(UserChart.id == chart_id, UserChart.username == username).one_or_none()
    if not chart:
        raise HTTPException(status_code=404, detail="Chart not found")
    
    # Convert filters list to JSON string
    filters_json = None
    if body.filters:
        filters_json = json.dumps(body.filters)
    
    chart.chart_name = body.chart_name
    chart.group_by = body.group_by
    chart.count_by = body.count_by
    chart.filter_by = getattr(body, 'filter_by', None)  # Legacy
    chart.filter_value = getattr(body, 'filter_value', None)  # Legacy
    chart.filters = filters_json  # New multi-filter
    chart.chart_type = getattr(body, 'chart_type', 'bar')
    
    db.commit()
    return {"ok": True, "id": chart.id}


@router.delete("/user-charts/{chart_id}")
def delete_user_chart(chart_id: int, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    from jose import jwt
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username = claims.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    chart = db.query(UserChart).filter(UserChart.id == chart_id, UserChart.username == username).one_or_none()
    if not chart:
        raise HTTPException(status_code=404, detail="Chart not found")
    
    db.delete(chart)
    db.commit()
    return {"ok": True}


class EmailSendRequest(BaseModel):
    recipient_ids: List[int]  # List of email recipient IDs, empty = all enabled recipients
    subject: str = "Patch Management Resources Report"
    body: str = "Please find attached the latest resources report."


@router.post("/resources/send-email")
def send_resources_email(
    req: EmailSendRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Send CSV report via email to selected recipients."""
    from jose import jwt
    # Use module-level JWT_SECRET from env var
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        role = claims.get("role")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Load SMTP config
    smtp_config = {}
    for key in ["smtp_server", "smtp_port", "smtp_from_email", "smtp_username", "smtp_password", "smtp_use_tls"]:
        kv = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
        if kv and kv.value:
            smtp_config[key] = kv.value
    
    if not smtp_config.get("smtp_server") or not smtp_config.get("smtp_port"):
        raise HTTPException(status_code=400, detail="SMTP not configured")
    
    # Get recipients
    if req.recipient_ids:
        recipients = db.query(EmailRecipient).filter(
            EmailRecipient.id.in_(req.recipient_ids),
            EmailRecipient.enabled == True
        ).all()
    else:
        # All enabled recipients
        recipients = db.query(EmailRecipient).filter(EmailRecipient.enabled == True).all()
    
    if not recipients:
        raise HTTPException(status_code=400, detail="No enabled recipients found")
    
    recipient_emails = [r.email for r in recipients]
    
    # Generate CSV
    resources = list_resources(db)
    headers = [
        "Platform","Namespace","Name","Kind","Image","Current","Latest","Diff",
        "Vulns","CVEs","Advice","Note","Upgrade Plan","EOL","Product","Checked",
        "Twistlock Critical", "Twistlock High", "Twistlock Medium", "Twistlock Low", 
        "Twistlock Total", "Risk Score", "Compliance Score"
    ]
    
    def q(v):
        if v is None:
            return ""
        s = str(v).replace('"', '""')
        if "," in s or '"' in s or "\n" in s:
            return f'"{s}"'
        return s
    
    sep = ","
    rows = [headers]
    
    for r in resources:
        tw = r.security_info.get("twistlock") if r.security_info else {}
        vd = tw.get("vulnerabilityDistribution", {}) if tw else {}
        row = [
            r.platform, r.namespace, r.resource_name, r.kind,
            r.image or "", r.current_version or "", r.latest_version or "",
            r.version_diff or "", "", "",
            r.security_info.get("advice", "") if r.security_info else "",
            "", "", r.eol_date or "", r.product_name or "",
            r.last_checked_at.isoformat() if r.last_checked_at else "",
            str(vd.get("critical", 0)) if vd else "0",
            str(vd.get("high", 0)) if vd else "0",
            str(vd.get("medium", 0)) if vd else "0",
            str(vd.get("low", 0)) if vd else "0",
            str(vd.get("total", 0)) if vd else "0",
            str(tw.get("vulnerabilityRiskScore", 0)) if tw else "0",
            str(tw.get("complianceRiskScore", 0)) if tw else "0"
        ]
        rows.append(row)
    
    csv_lines = [sep.join([q(v) for v in row]) for row in rows]
    csv_content = "\ufeff" + "\n".join(csv_lines)
    
    # Send email
    success, message = send_email_with_csv(
        smtp_server=smtp_config["smtp_server"],
        smtp_port=int(smtp_config["smtp_port"]),
        from_email=smtp_config["smtp_from_email"],
        to_emails=recipient_emails,
        subject=req.subject,
        body=req.body,
        csv_content=csv_content,
        csv_filename=f"resources_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        username=smtp_config.get("smtp_username") or None,
        password=smtp_config.get("smtp_password") or None,
        use_tls=(smtp_config.get("smtp_use_tls", "true") == "true")
    )
    
    if not success:
        raise HTTPException(status_code=500, detail=message)
    
    return {"ok": True, "message": message, "recipients": len(recipient_emails)}


# ============================================================================
# QUICK SYNC FOR COMPARE (Manifest & Related Objects only - no Twistlock/EOL)
# ============================================================================

@router.post("/resources/quick-sync-manifest")
def quick_sync_manifest(
    request: dict,
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> dict:
    """
    Quick sync a resource - only fetches manifest and related objects.
    Skips Twistlock, EOL, and latest version checks for faster performance.
    Used by Compare feature for pre-comparison sync.
    
    Accepts either admin JWT token or X-Backend-Token for federation.
    """
    import os
    from datetime import datetime, timezone
    from ..services.kube import KubeClient
    from sqlalchemy.orm.attributes import flag_modified
    
    # Authentication check (same as sync-by-key)
    if x_backend_token:
        my_auth_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        backend_type = os.getenv("BACKEND_TYPE", "secondary")
        
        if backend_type == "secondary" and my_auth_token:
            if x_backend_token != my_auth_token:
                raise HTTPException(status_code=403, detail="Invalid backend token")
        else:
            backend = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True
            ).one_or_none()
            if not backend:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif authorization:
        token = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
        if _decode_role(token) != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    namespace = request.get("namespace", "").strip()
    resource_name = request.get("resource_name", "").strip()
    kind = request.get("kind", "").strip()
    
    if not namespace or not resource_name or not kind:
        return {"ok": False, "error": "namespace, resource_name, and kind are required"}
    
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    # Find resource in database
    res = db.query(Resource).filter(
        Resource.platform == local_platform,
        Resource.namespace == namespace,
        Resource.resource_name == resource_name,
        Resource.kind.ilike(kind)
    ).first()
    
    if not res:
        return {"ok": False, "error": f"Resource not found in database: {namespace}/{resource_name}"}
    
    log_event("resource.quick_sync.start", resource=resource_name, namespace=namespace)
    
    try:
        kube = KubeClient()
        
        # Fetch manifest
        manifest_yaml = kube.get_resource_manifest(namespace, resource_name, kind)
        if not manifest_yaml:
            return {"ok": False, "error": "Could not fetch manifest from Kubernetes"}
        
        res.manifest_yaml = manifest_yaml
        res.manifest_updated_at = datetime.now(timezone.utc)
        
        # Fetch related objects
        try:
            related_objects = kube.get_related_objects(namespace, resource_name, manifest_yaml)
            if related_objects:
                res.related_objects = related_objects
        except Exception as e:
            log_event("resource.quick_sync.related_error", resource=resource_name, error=str(e))
        
        # Fetch replica count
        try:
            replicas = kube.get_replicas_for_resource(namespace, resource_name, kind)
            res.replicas = replicas
        except Exception:
            pass
        
        flag_modified(res, "manifest_yaml")
        flag_modified(res, "related_objects")
        db.commit()
        
        log_event("resource.quick_sync.success", resource=resource_name)
        return {"ok": True, "resource_name": resource_name}
        
    except Exception as e:
        log_event("resource.quick_sync.error", resource=resource_name, error=str(e))
        return {"ok": False, "error": str(e)}


@router.post("/resources/check-exists-in-k8s")
def check_resource_exists_in_k8s(
    request: dict,
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> dict:
    """
    Check if a resource exists in Kubernetes and return its manifest.
    Does NOT save to database - used for Compare mini-discovery.
    
    Returns:
    - exists: bool - whether resource exists in K8s
    - manifest: dict - the manifest YAML (if exists)
    - related_objects: dict - related objects (if exists)
    """
    import os
    from ..services.kube import KubeClient
    
    # Authentication check (same pattern)
    if x_backend_token:
        my_auth_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        backend_type = os.getenv("BACKEND_TYPE", "secondary")
        
        if backend_type == "secondary" and my_auth_token:
            if x_backend_token != my_auth_token:
                raise HTTPException(status_code=403, detail="Invalid backend token")
        else:
            backend = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True
            ).one_or_none()
            if not backend:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif authorization:
        token = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
        role = _decode_role(token)
        # Allow admin and analyst for compare feature
        if role not in ["admin", "analyst"]:
            raise HTTPException(status_code=403, detail="Admin or Analyst role required")
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    namespace = request.get("namespace", "").strip()
    resource_name = request.get("resource_name", "").strip()
    kind = request.get("kind", "").strip()
    
    if not namespace or not resource_name or not kind:
        return {"exists": False, "error": "namespace, resource_name, and kind are required"}
    
    log_event("resource.check_exists.start", resource=resource_name, namespace=namespace, kind=kind)
    
    try:
        kube = KubeClient()
        
        # Try to fetch manifest - if it exists, the resource exists
        manifest_yaml = kube.get_resource_manifest(namespace, resource_name, kind)
        
        if not manifest_yaml:
            log_event("resource.check_exists.not_found", resource=resource_name, namespace=namespace)
            return {"exists": False, "manifest": None, "related_objects": None}
        
        # Resource exists - also fetch related objects
        related_objects = None
        try:
            related_objects = kube.get_related_objects(namespace, resource_name, manifest_yaml)
        except Exception:
            pass
        
        log_event("resource.check_exists.found", resource=resource_name, namespace=namespace)
        return {
            "exists": True,
            "manifest": manifest_yaml,
            "related_objects": related_objects,
            "namespace": namespace,
            "name": resource_name,
            "kind": kind
        }
        
    except Exception as e:
        log_event("resource.check_exists.error", resource=resource_name, error=str(e))
        return {"exists": False, "error": str(e)}


# ── Landscape category overrides ─────────────────────────────────

@router.get("/landscape/categories")
def get_landscape_categories(db: Session = Depends(get_db)) -> dict:
    """Return saved landscape category layout. No auth required (read-only view)."""
    import json as _json
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_categories").one_or_none()
    if row and row.value:
        try:
            return {"categories": _json.loads(row.value)}
        except Exception:
            pass
    return {"categories": None}


@router.post("/landscape/categories")
def save_landscape_categories(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Save landscape category layout. Admin role required."""
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    categories = body.get("categories")
    if categories is None:
        row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_categories").one_or_none()
        if row:
            db.delete(row)
        db.commit()
        log_event("landscape.categories.reset")
        return {"ok": True, "message": "Categories reset to defaults"}

    if not isinstance(categories, list):
        raise HTTPException(status_code=400, detail="categories must be an array")

    value = _json.dumps(categories, ensure_ascii=False)
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_categories").one_or_none()
    if row is None:
        db.add(ConfigKV(key="landscape_categories", value=value))
    else:
        row.value = value
    db.commit()
    log_event("landscape.categories.saved", count=len(categories))
    return {"ok": True, "message": f"Saved {len(categories)} categories"}


@router.get("/landscape/logos")
def get_landscape_logos(db: Session = Depends(get_db)) -> dict:
    """Return custom product logos. No auth required (read-only)."""
    import json as _json
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_logos").one_or_none()
    if row and row.value:
        try:
            return {"logos": _json.loads(row.value)}
        except Exception:
            pass
    return {"logos": {}}


@router.post("/landscape/logos")
def save_landscape_logo(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """
    Save or delete a custom product logo. Admin role required.
    Body: { "product": "kafka", "logo": "https://..." | "data:image/..." | null, "hideName": false }
    Sending logo=null removes the custom logo for that product.
    Logo value is stored as { "url": "...", "hideName": bool } when hideName is provided.
    """
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    product = (body.get("product") or "").strip().lower()
    if not product:
        raise HTTPException(status_code=400, detail="product name is required")

    logo_value = body.get("logo")
    hide_name = body.get("hideName", False)

    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_logos").one_or_none()
    logos: dict = {}
    if row and row.value:
        try:
            logos = _json.loads(row.value)
        except Exception:
            logos = {}

    if logo_value or hide_name:
        logos[product] = {"url": logo_value, "hideName": bool(hide_name)}
    else:
        logos.pop(product, None)

    value = _json.dumps(logos, ensure_ascii=False)
    if row is None:
        db.add(ConfigKV(key="landscape_logos", value=value))
    else:
        row.value = value
    db.commit()

    action = "set" if (logo_value or hide_name) else "removed"
    log_event("landscape.logo.updated", product=product, action=action)
    return {"ok": True, "product": product, "action": action}


# ── Landscape product aliases ─────────────────────────────────────

@router.get("/landscape/aliases")
def get_landscape_aliases(db: Session = Depends(get_db)) -> dict:
    """Return custom product display aliases. No auth required (read-only)."""
    import json as _json
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_aliases").one_or_none()
    if row and row.value:
        try:
            return {"aliases": _json.loads(row.value)}
        except Exception:
            pass
    return {"aliases": {}}


@router.post("/landscape/aliases")
def save_landscape_alias(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """
    Save or delete a product display alias. Admin role required.
    Body: { "product": "open-webui-open-webui", "alias": "Open WebUI" | null }
    Sending alias=null removes the alias for that product.
    """
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    product = (body.get("product") or "").strip().lower()
    if not product:
        raise HTTPException(status_code=400, detail="product name is required")

    alias_value = (body.get("alias") or "").strip()

    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_aliases").one_or_none()
    aliases: dict = {}
    if row and row.value:
        try:
            aliases = _json.loads(row.value)
        except Exception:
            aliases = {}

    if alias_value:
        aliases[product] = alias_value
    else:
        aliases.pop(product, None)

    value = _json.dumps(aliases, ensure_ascii=False)
    if row is None:
        db.add(ConfigKV(key="landscape_aliases", value=value))
    else:
        row.value = value
    db.commit()

    action = "set" if alias_value else "removed"
    log_event("landscape.alias.updated", product=product, alias=alias_value or None, action=action)
    return {"ok": True, "product": product, "action": action}


# ── Landscape custom (manual) products ────────────────────────────

@router.get("/landscape/custom-products")
def get_landscape_custom_products(db: Session = Depends(get_db)) -> dict:
    """Return manually added landscape products. No auth required."""
    import json as _json
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_custom_products").one_or_none()
    if row and row.value:
        try:
            return {"products": _json.loads(row.value)}
        except Exception:
            pass
    return {"products": []}


@router.post("/landscape/custom-products")
def save_landscape_custom_product(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """
    Add or update a custom landscape product. Admin role required.
    Body: {
        "name": "jira",
        "alias": "Jira Service Management",
        "platforms": ["on-premise"],
        "resourceCount": 3,
        "currentVersion": "9.12.0",
        "latestVersion": "9.14.0",
        "logo": "https://...",
        "category": "CI/CD & DevOps"
    }
    """
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    name = (body.get("name") or "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="product name is required")

    product_data = {
        "name": name,
        "alias": (body.get("alias") or "").strip() or None,
        "platforms": body.get("platforms") or [],
        "resourceCount": int(body.get("resourceCount") or 1),
        "currentVersion": (body.get("currentVersion") or "").strip() or None,
        "latestVersion": (body.get("latestVersion") or "").strip() or None,
        "logo": (body.get("logo") or "").strip() or None,
        "category": (body.get("category") or "").strip() or None,
    }

    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_custom_products").one_or_none()
    products: list = []
    if row and row.value:
        try:
            products = _json.loads(row.value)
        except Exception:
            products = []

    # Update existing or append new
    idx = next((i for i, p in enumerate(products) if p.get("name") == name), None)
    if idx is not None:
        products[idx] = product_data
    else:
        products.append(product_data)

    value = _json.dumps(products, ensure_ascii=False)
    if row is None:
        db.add(ConfigKV(key="landscape_custom_products", value=value))
    else:
        row.value = value
    db.commit()

    log_event("landscape.custom_product.saved", product=name)
    return {"ok": True, "product": name, "action": "saved"}


@router.delete("/landscape/custom-products/{product_name}")
def delete_landscape_custom_product(
    product_name: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a custom landscape product. Admin role required."""
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    name = product_name.strip().lower()

    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_custom_products").one_or_none()
    products: list = []
    if row and row.value:
        try:
            products = _json.loads(row.value)
        except Exception:
            products = []

    before = len(products)
    products = [p for p in products if p.get("name") != name]

    if len(products) == before:
        raise HTTPException(status_code=404, detail="Product not found")

    value = _json.dumps(products, ensure_ascii=False)
    if row is None:
        db.add(ConfigKV(key="landscape_custom_products", value=value))
    else:
        row.value = value
    db.commit()

    log_event("landscape.custom_product.deleted", product=name)
    return {"ok": True, "product": name, "action": "deleted"}


# ── Landscape product snapshot (cache for instant load) ────────────

@router.get("/landscape/product-snapshot")
def get_landscape_product_snapshot(db: Session = Depends(get_db)) -> dict:
    """Return cached product snapshot for instant landscape rendering. No auth required."""
    import json as _json
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_product_snapshot").one_or_none()
    if row and row.value:
        try:
            return {"products": _json.loads(row.value)}
        except Exception:
            pass
    return {"products": []}


@router.post("/landscape/product-snapshot")
def save_landscape_product_snapshot(
    body: dict,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Replace the full product snapshot. Any authenticated role."""
    import json as _json

    _decode_role(token)

    products = body.get("products")
    if not isinstance(products, list):
        raise HTTPException(status_code=400, detail="products array is required")

    value = _json.dumps(products, ensure_ascii=False)
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_product_snapshot").one_or_none()
    if row is None:
        db.add(ConfigKV(key="landscape_product_snapshot", value=value))
    else:
        row.value = value
    db.commit()
    return {"ok": True, "count": len(products)}


@router.delete("/landscape/product-snapshot/{product_name}")
def delete_landscape_product_snapshot_entry(
    product_name: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Remove a single product from the snapshot. Admin role required."""
    import json as _json

    role = _decode_role(token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    name = product_name.strip().lower()
    row = db.query(ConfigKV).filter(ConfigKV.key == "landscape_product_snapshot").one_or_none()
    products: list = []
    if row and row.value:
        try:
            products = _json.loads(row.value)
        except Exception:
            products = []

    before = len(products)
    products = [p for p in products if p.get("name") != name]

    if len(products) == before:
        raise HTTPException(status_code=404, detail="Product not found in snapshot")

    value = _json.dumps(products, ensure_ascii=False)
    if row is None:
        db.add(ConfigKV(key="landscape_product_snapshot", value=value))
    else:
        row.value = value
    db.commit()

    log_event("landscape.snapshot_product.deleted", product=name)
    return {"ok": True, "product": name, "action": "deleted"}


from datetime import datetime
