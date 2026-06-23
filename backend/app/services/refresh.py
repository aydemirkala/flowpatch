from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Set, Tuple, Optional
import time

from sqlalchemy import select
import traceback
from sqlalchemy.orm import Session

from ..models import Resource, ResourceHistory, ConfigKV, SyncLog, BackendEndpoint
from ..services.kube import KubeClient
from ..services.sources_reader import read_sources, SourceItem
from ..services.versioning import (
    compare_semver,
    fetch_latest_version,
    fetch_all_semver_tags,
    parse_image,
    compute_cves_for_image,
    build_upgrade_advice,
    parse_version_tuple,
)
from ..logging_utils import log_event
from .twistlock import fetch_image_report
from .trivy import submit_scan as trivy_submit_scan
from .llm_advice import (submit_advice_request as llm_submit_advice,
                         submit_eol_status_request as llm_submit_eol_status)
from .exclusions import should_skip_namespace, should_skip_image
from .image_cache import (is_primary_backend, get_latest_version_config, fetch_latest_version_from_primary,
                          get_twistlock_config, fetch_twistlock_from_primary, _twistlock_refresh_hours,
                          get_trivy_config, fetch_trivy_from_primary,
                          get_cached_trivy,
                          get_eol_config, fetch_eol_from_primary,
                          get_llm_config, fetch_llm_from_primary)
from .sync_state import is_sync_cancelled, clear_cancelled_sync


def _is_cleanup_force_enabled(db: Session) -> bool:
    """Return True if cleanup may bypass the mass-delete safety guard.

    Resolution order:
      1. Local backend_endpoints row for this BACKEND_NAME (works for both
         primary and secondary, since each backend has its own row in its
         own DB).
      2. Federation cache (secondary backends only) — falls back to the
         flag broadcast by the primary's `/admin/federation/config` payload.
    Default: False (safe).
    """
    import os
    try:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        row = (
            db.query(BackendEndpoint)
            .filter(BackendEndpoint.name == backend_name)
            .one_or_none()
        )
        if row is not None and bool(getattr(row, "cleanup_force_enabled", False)):
            return True
        if os.getenv("BACKEND_TYPE", "primary") == "secondary":
            try:
                from .config_cache import get_config_cache
                backend_config = get_config_cache().get_backend_config()
                return bool(backend_config.get("cleanup_force_enabled", False))
            except Exception:
                return False
    except Exception:
        return False
    return False


def _retry_with_backoff(func, max_retries=3, initial_delay=0.5, context=""):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                log_event(f"{context}.retry.failed", attempts=max_retries, error=str(e))
                raise
            wait_time = initial_delay * (2 ** attempt)
            log_event(f"{context}.retry.attempt", attempt=attempt+1, max=max_retries, wait=wait_time)
            time.sleep(wait_time)
    return None


def refresh_single_product(db: Session, product_name: str, triggered_by: str = "auto") -> Dict[str, int]:
    """
    Refresh resources for a single product only.
    Much faster than full refresh - only discovers and processes matching resources.
    
    Args:
        db: Database session
        product_name: Product name to sync (e.g., "redis", "fluent-bit")
        triggered_by: Who triggered this sync
    
    Returns:
        Dict with stats: {"discovered": N, "refreshed": N, "errors": N}
    """
    from .dynamic_sources import discover_single_product
    
    log_event("refresh_product.start", product=product_name, triggered_by=triggered_by)
    
    # Discover resources for this product
    sources = discover_single_product(db, product_name)
    
    if not sources:
        log_event("refresh_product.no_sources", product=product_name)
        return {"discovered": 0, "refreshed": 0, "errors": 0}
    
    # Convert to SourceItem format
    items = []
    for src in sources:
        items.append(SourceItem(
            platform=src["platform"],
            namespace=src["namespace"],
            kind=src["kind"],
            resource_name=src["resource_name"],
            product_name=src.get("product_name", "")
        ))
    
    log_event("refresh_product.sources", product=product_name, count=len(items))
    
    # Process using the main refresh logic (without cleanup)
    kube = KubeClient()
    refreshed = 0
    errors = 0
    
    for item in items:
        try:
            # Skip excluded namespaces
            if should_skip_namespace(item.namespace, db):
                continue
            
            image = kube.get_image_for_resource(
                namespace=item.namespace, 
                name=item.resource_name, 
                kind=item.kind
            )
            
            primary_image_excluded = False
            if image and should_skip_image(image, db):
                primary_image_excluded = True
                image = None
            
            # Get all containers and track exclusions
            containers_list = []
            has_excluded_container = False
            try:
                all_cons = kube.get_images_for_resource(
                    namespace=item.namespace, 
                    name=item.resource_name, 
                    kind=item.kind
                )
                for c in all_cons:
                    raw_img = c.get("image")
                    nm = c.get("name") or ""
                    if raw_img and should_skip_image(raw_img, db):
                        has_excluded_container = True
                        continue
                    if raw_img:
                        containers_list.append({"name": nm, "image": raw_img})
            except Exception:
                pass
            
            # If all containers excluded, skip
            if not containers_list and primary_image_excluded:
                continue
            
            # If no primary image but have containers, use first
            if not image and containers_list:
                image = containers_list[0]["image"]
            
            if not image:
                continue
            
            # Normalize image
            try:
                pi = parse_image(image)
                if pi:
                    repo = pi.repository
                    if pi.registry == "docker.io" and "/" not in repo:
                        repo = f"library/{repo}"
                    image = f"{pi.registry}/{repo}:{pi.tag or 'latest'}"
            except Exception:
                pass
            
            current_version = image.rsplit(":", 1)[-1] if image else None
            
            # Upsert resource
            existing = db.execute(
                select(Resource).where(
                    Resource.platform == item.platform,
                    Resource.namespace == item.namespace,
                    Resource.resource_name == item.resource_name,
                    Resource.kind == item.kind,
                )
            ).scalar_one_or_none()
            
            now = datetime.now(timezone.utc)
            
            if existing is None:
                existing = Resource(
                    platform=item.platform,
                    namespace=item.namespace,
                    resource_name=item.resource_name,
                    kind=item.kind,
                )
                db.add(existing)
            
            existing.image = image
            existing.current_version = current_version
            existing.product_name = item.product_name
            existing.last_checked_at = now
            
            # Fetch replicas
            try:
                replicas = kube.get_replicas_for_resource(item.namespace, item.resource_name, item.kind)
                existing.replicas = replicas
            except Exception:
                pass
            
            # Fetch and cache manifest YAML for comparison
            # Save manifest if resource belongs to a managed product (has product_name)
            # Note: Excluded container images don't prevent manifest saving for Compare feature
            # The goal is to compare product deployments even if they have sidecar containers from excluded registries
            should_save_manifest = bool(item.product_name)
            if should_save_manifest:
                try:
                    manifest = kube.get_resource_manifest(item.namespace, item.resource_name, item.kind)
                    if manifest:
                        existing.manifest_yaml = manifest
                        existing.manifest_updated_at = now
                        
                        # Also fetch related objects (PDB, HPA, Service, ConfigMap, Secret, Route)
                        try:
                            related = kube.get_related_objects(item.namespace, item.resource_name, manifest)
                            related_count = sum(len(v) for v in related.values())
                            if related_count > 0:
                                existing.related_objects = related
                                log_event("kube.related_objects.saved", 
                                         namespace=item.namespace, 
                                         resource=item.resource_name,
                                         total=related_count)
                            else:
                                existing.related_objects = None
                        except Exception as e:
                            log_event("kube.related_objects.error", namespace=item.namespace, resource=item.resource_name, error=str(e))
                        
                        log_event("kube.manifest.saved", namespace=item.namespace, resource=item.resource_name, product=item.product_name)
                except Exception as e:
                    log_event("kube.manifest.fetch_error", namespace=item.namespace, resource=item.resource_name, error=str(e))
            
            # Fetch latest version (via federation or local based on config)
            try:
                pi = parse_image(image)
                if pi:
                    # Check if we should use primary backend for latest version
                    latest_version_config = get_latest_version_config(db)
                    use_primary = latest_version_config.get("mode") == "primary" and not is_primary_backend()
                    
                    if use_primary:
                        log_event("refresh_product.latest_version.via_primary", 
                                 resource=item.resource_name, image=image)
                        latest = fetch_latest_version_from_primary(db, image)
                    else:
                        latest = fetch_latest_version(pi)
                    
                    if latest:
                        existing.latest_version = latest
                        diff = compare_semver(current_version, latest)
                        existing.version_diff = diff.category if hasattr(diff, 'category') else str(diff)
            except Exception as e:
                log_event("refresh_product.version_error", resource=item.resource_name, error=str(e))
            
            # Update security_info containers
            try:
                sec = existing.security_info or {}
                sec["containers"] = containers_list
                existing.security_info = sec
            except Exception:
                pass

            # Keep the compact grid summary in sync (R4 perf — slim listing reads this).
            try:
                existing.sec_summary = build_resource_summary(existing.security_info, image)
            except Exception:
                pass

            refreshed += 1

        except Exception as e:
            errors += 1
            log_event("refresh_product.error", 
                     resource=item.resource_name, 
                     namespace=item.namespace,
                     error=str(e))
    
    db.commit()
    
    log_event("refresh_product.complete", 
             product=product_name, 
             discovered=len(items), 
             refreshed=refreshed, 
             errors=errors)
    
    return {"discovered": len(items), "refreshed": refreshed, "errors": errors}


def refresh_from_sources(db: Session, triggered_by: str = "auto", use_dynamic: bool = False,
                         sync_log_id: int = None, target_items: list = None,
                         skip_cleanup: bool = False, log_sync: bool = True) -> Dict[str, int]:
    """
    Refresh resources from sources.

    Args:
        db: Database session
        triggered_by: Who triggered this sync ("auto" or username)
        use_dynamic: If True, use dynamic discovery based on managed products.
                     If False, use static sources.txt file.
        sync_log_id: Optional ID of the sync log entry to update on completion
        target_items: If provided, process only these SourceItems (filtered sync).
        skip_cleanup: If True, skip stale resource cleanup.
        log_sync: If False, do not create a SyncLog row for this run. Used by the
                  real-time watch so per-resource refreshes don't flood the Sync
                  Logs page (the change is recorded in resource_history instead).
    """
    # Set global socket timeout to prevent any operation from hanging indefinitely
    # This acts as a safety net for any network calls that don't specify their own timeout
    import socket
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)  # 30 seconds max for any socket operation

    try:
        return _do_refresh(db, triggered_by, use_dynamic, sync_log_id,
                          target_items=target_items, skip_cleanup=skip_cleanup,
                          log_sync=log_sync)
    finally:
        # Restore original timeout
        socket.setdefaulttimeout(old_timeout)


def build_resource_summary(security_info: Optional[dict], image: Optional[str] = None) -> Optional[dict]:
    """Build the compact security/display summary stored in Resource.sec_summary.

    Must produce EXACTLY the trimmed object the resources grid (slim list) needs —
    distributions, riskFactorCount, advice, small flags, and a trimmed containers
    list — so the list query reads this small column instead of detoasting the large
    `security_info` blob 11x per row. Keep field-for-field identical to what
    _list_resources_slim used to build inline (routers/resources.py)."""
    si_full = security_info or {}
    si: dict = {}

    advice = si_full.get("advice")
    if advice:
        si["advice"] = advice
    for k in ("llm_advice", "llm_advice_at", "llm_upgrade_advice",
              "llm_upgrade_at", "exists_in_cluster", "trivy_fetched_at"):
        v = si_full.get(k)
        if v is not None:
            si[k] = v

    tw = si_full.get("twistlock") or {}
    tw_dist = tw.get("vulnerabilityDistribution")
    if tw_dist is not None:
        si["twistlock"] = {"vulnerabilityDistribution": tw_dist,
                           "riskFactorCount": tw.get("riskFactorCount")}
    trv = si_full.get("trivy") or {}
    trv_dist = trv.get("vulnerabilityDistribution")
    if trv_dist is not None:
        si["trivy"] = {"vulnerabilityDistribution": trv_dist}

    containers_raw = si_full.get("containers")
    containers: list = []
    if containers_raw and isinstance(containers_raw, list):
        for c in containers_raw:
            if not isinstance(c, dict):
                continue
            sc = {k2: c.get(k2) for k2 in ("name", "image", "current_version",
                  "latest_version", "version_diff", "is_main", "eol_date")}
            c_tw = c.get("twistlock")
            if c_tw and isinstance(c_tw, dict):
                sc["twistlock"] = {
                    "vulnerabilityDistribution": c_tw.get("vulnerabilityDistribution"),
                    "riskFactorCount": c_tw.get("riskFactorCount"),
                }
            c_trv = c.get("trivy")
            if c_trv and isinstance(c_trv, dict):
                sc["trivy"] = {"vulnerabilityDistribution": c_trv.get("vulnerabilityDistribution")}
            containers.append(sc)
        si["containers"] = containers

    if not containers and image:
        tag = image.rsplit(":", 1)[-1] if ":" in image else "latest"
        name = image.split("/")[-1].split(":")[0]
        si["containers"] = [{"name": name, "image": image, "current_version": tag}]

    return si or None


def _eol_support_ttl_days(db: Session) -> int:
    """How old the LLM-derived EOL support status may be before refresh re-queries it,
    in days. Admin-configurable (ConfigKV 'eol_support_ttl_days'); default 3. Bounded 1..365."""
    try:
        from ..models import ConfigKV
        row = db.query(ConfigKV).filter(ConfigKV.key == "eol_support_ttl_days").first()
        if row and row.value not in (None, ""):
            return max(1, min(365, int(row.value)))
    except Exception:
        pass
    return 3


def apply_trivy_to_resources(db: Session, image: str, report: Optional[dict]) -> int:
    """After a background Trivy scan finishes, merge its result into the
    `security_info` + `sec_summary` of resources on THIS backend that use `image`,
    so the grid shows Trivy data without a manual re-sync.

    Safe by design (handles the concurrency caveats):
      - **Targeted:** only the `trivy` sub-field is touched — top-level when the
        resource's main image matches, plus any container whose image matches —
        never twistlock/version/eol. A concurrent refresh writing other fields of
        the same row is not clobbered; and if a refresh does overwrite our trivy,
        the next refresh restores it from the cache (eventual consistency holds).
      - **Change-guarded:** a row is rewritten only when its trivy data actually
        changed, so no-op UPDATEs are skipped (avoids a write burst during the
        nightly mass sync).
      - **Fresh read + single short commit** to minimise the race window.
    Returns the number of rows updated.
    """
    if not report:
        return 0
    try:
        from datetime import datetime as _dt, timezone as _tz
        from ..models import Resource

        # Resources whose MAIN image matches (portable ORM query).
        candidate_ids = {rid for (rid,) in
                         db.query(Resource.id).filter(Resource.image == image).all()}
        # Plus any resource with a CONTAINER (sidecar) on this image (Postgres jsonb
        # containment; best-effort so a non-jsonb backend still updates main images).
        try:
            from sqlalchemy import text as _sql_text
            import json as _json
            rows = db.execute(_sql_text(
                "SELECT id FROM resources "
                "WHERE (security_info -> 'containers') @> CAST(:cm AS jsonb)"
            ), {"cm": _json.dumps([{"image": image}])}).fetchall()
            candidate_ids.update(rid for (rid,) in rows)
        except Exception as e:
            log_event("trivy.resources.container_query_error", image=image, error=str(e))

        if not candidate_ids:
            return 0

        now_iso = _dt.now(_tz.utc).isoformat()
        updated = 0
        for rid in candidate_ids:
            res = db.query(Resource).filter(Resource.id == rid).one_or_none()
            if not res or not res.security_info:
                continue
            si = dict(res.security_info)
            changed = False

            # Top-level trivy: only when the resource's main image is the scanned one.
            if res.image == image and si.get("trivy") != report:
                si["trivy"] = report
                si["trivy_fetched_at"] = now_iso
                changed = True

            # Per-container trivy for every container running the scanned image.
            containers = si.get("containers")
            if isinstance(containers, list):
                new_containers = []
                c_changed = False
                for c in containers:
                    if isinstance(c, dict) and c.get("image") == image and c.get("trivy") != report:
                        c = {**c, "trivy": report}
                        c_changed = True
                    new_containers.append(c)
                if c_changed:
                    si["containers"] = new_containers
                    changed = True

            if changed:
                # Reassign (new dict) so SQLAlchemy flags the JSONB column dirty.
                res.security_info = si
                res.sec_summary = build_resource_summary(si, res.image)
                updated += 1

        if updated:
            db.commit()
            log_event("trivy.resources.updated", image=image, count=updated)
        return updated
    except Exception as e:
        log_event("trivy.resources.update_error", image=image, error=str(e))
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _history_source(triggered_by: str) -> str:
    """Map a refresh trigger to a Change-History 'source' label.

    Lets the History view distinguish real-time watch detections from the nightly
    cron and manual syncs (previously all recorded as the generic 'sync')."""
    tb = (triggered_by or "").strip()
    if tb == "watch":
        return "watch"
    if tb == "auto":
        return "cron"
    # usernames, "row-sync", "product_added:*" → operator-initiated
    return "manual"


def _do_refresh(db: Session, triggered_by: str, use_dynamic: bool, sync_log_id: int = None,
                target_items: list = None, skip_cleanup: bool = False,
                log_sync: bool = True) -> Dict[str, int]:
    """Internal refresh implementation with socket timeout protection.
    
    Args:
        target_items: If provided, process only these SourceItems (skips discovery).
                      Used by per-row sync to run the same logic with filtered scope.
        skip_cleanup: If True, skip stale resource cleanup. Used for filtered syncs.
    """
    if target_items is not None:
        items = target_items
        log_event("refresh.start", count=len(items), triggered_by=triggered_by, mode="filtered")
    elif use_dynamic:
        # Use dynamic discovery
        from ..services.dynamic_sources import get_combined_sources
        import os
        
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        platform_name = os.getenv("BACKEND_PLATFORM", "default-platform")
        sources_data = get_combined_sources(db, backend_name, platform_name)
        
        # Convert to SourceItem format
        items = []
        for src in sources_data:
            item = SourceItem(
                platform=src["platform"],
                namespace=src["namespace"],
                kind=src["kind"],
                resource_name=src["resource_name"],
                product_name=src.get("product_name", "")
            )
            items.append(item)
        
        log_event("refresh.start", count=len(items), triggered_by=triggered_by, mode="dynamic")
    else:
        # Use static sources.txt
        items = read_sources(db=db)
        log_event("refresh.start", count=len(items), triggered_by=triggered_by, mode="static")
    kube = KubeClient()
    
    # Mark old "running" entries as "timeout" (older than 5 minutes)
    try:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        old_running = db.query(SyncLog).filter(
            SyncLog.status == "running",
            SyncLog.started_at < cutoff
        ).all()
        for old in old_running:
            old.status = "timeout"
            old.finished_at = old.started_at + timedelta(minutes=5)
            old.error_message = "Sync did not complete (timeout or crash)"
        if old_running:
            db.commit()
            log_event("synclog.cleanup", cleaned=len(old_running))
    except Exception as e:
        log_event("synclog.cleanup.error", error=str(e))
        db.rollback()
    
    # Create sync log entry (skip if already provided or if table doesn't exist yet)
    import os
    backend_name_env = os.getenv("BACKEND_NAME", "default-backend")
    platform_name_env = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    # Try to get platform from backend_endpoints table
    try:
        backend_record = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name_env).one_or_none()
        if backend_record and backend_record.platform:
            platform_name_env = backend_record.platform
    except Exception:
        pass
    
    # Only create a new sync log if one wasn't provided (and logging isn't suppressed).
    # Watch-triggered per-resource refreshes pass log_sync=False so they don't flood
    # the Sync Logs page; their changes are recorded in resource_history instead.
    if log_sync and not sync_log_id:
        try:
            sync_log = SyncLog(
                backend_name=backend_name_env,
                platform=platform_name_env,
                triggered_by=triggered_by,
                status="running"
            )
            db.add(sync_log)
            db.commit()
            sync_log_id = sync_log.id
        except Exception as e:
            log_event("synclog.create.error", error=str(e))
            db.rollback()
    
    # Determine force sync mode:
    # - Auto-sync (scheduled) ALWAYS forces sync to ensure fresh data
    # - Manual sync respects the Force Sync Mode toggle
    force_sync = False
    if triggered_by in ("auto", "row-sync"):
        force_sync = True
        log_event("refresh.force_mode", reason=f"{triggered_by} always forces", triggered_by=triggered_by)
    else:
        # Manual sync: check if SYNC-FORCE is enabled for THIS backend
        try:
            import os
            backend_name = os.getenv("BACKEND_NAME", "default-backend")
            backend_type = os.getenv("BACKEND_TYPE", "primary")
            
            # First check local database
            backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
            
            if backend and backend.force_sync_enabled:
                force_sync = True
            elif backend_type == "secondary":
                # Secondary backends: fetch force_sync setting from primary via federation
                try:
                    from .config_cache import get_config_cache
                    cache = get_config_cache()
                    backend_config = cache.get_backend_config()
                    force_sync = backend_config.get("force_sync_enabled", False)
                    if force_sync:
                        log_event("refresh.force_sync.from_primary", backend=backend_name, source="federation")
                except Exception as e:
                    log_event("refresh.force_sync.federation_error", backend=backend_name, error=str(e))
        except Exception:
            pass
    
    if force_sync:
        log_event("refresh.force_sync.enabled", mode="force")
    else:
        log_event("refresh.force_sync.disabled", mode="normal")

    refreshed = 0
    errors = 0
    deleted = 0
    error_messages: list[str] = []  # Collect error messages for sync log

    # Per-sync cache for image data to avoid redundant API calls
    image_cache: Dict[str, dict] = {}  # key: normalized_image, value: {latest_version, twistlock, eol, etc}
    
    # Track seen keys to remove stale ones later
    seen_keys: Set[Tuple[str, str, str, str]] = set()
    cancelled = False
    helm_release_cache: dict = {}  # Phase 1A: decode each Helm release Secret once per refresh pass
    twist_refresh_hours = _twistlock_refresh_hours(db)  # Admin-configurable; read once per pass
    for item in items:
        # Check if sync was cancelled
        if sync_log_id and is_sync_cancelled(sync_log_id):
            log_event("refresh.cancelled", sync_log_id=sync_log_id, processed=refreshed)
            cancelled = True
            break
        
        key = (item.platform, item.namespace, item.resource_name, item.kind)
        
        # Skip excluded namespaces
        if should_skip_namespace(item.namespace, db):
            log_event("refresh.skip.excluded_namespace", 
                     resource=item.resource_name, 
                     namespace=item.namespace,
                     kind=item.kind)
            continue
        
        try:
            image = kube.get_image_for_resource(namespace=item.namespace, name=item.resource_name, kind=item.kind)
            primary_image_excluded = False
            
            # Check if primary image is from excluded registry
            if image and should_skip_image(image, db):
                log_event("refresh.skip.excluded_registry.primary", 
                         resource=item.resource_name, 
                         namespace=item.namespace, 
                         image=image)
                primary_image_excluded = True
                image = None  # Clear so we can use a non-excluded container image
            
            # Collect all container images for this workload (including non-excluded ones)
            containers_list: list[dict[str, str]] = []
            has_excluded_container = False  # Track if any container was excluded
            try:
                all_cons = kube.get_images_for_resource(namespace=item.namespace, name=item.resource_name, kind=item.kind)
                for c in all_cons:
                    raw_img = c.get("image")
                    nm = c.get("name") or ""
                    if not raw_img:
                        continue
                    
                    # Skip excluded registries for containers
                    if should_skip_image(raw_img, db):
                        log_event("refresh.skip.excluded_registry.container", 
                                 resource=item.resource_name, 
                                 container=nm,
                                 image=raw_img)
                        has_excluded_container = True
                        continue
                    
                    norm = raw_img
                    try:
                        from .versioning import parse_image as _parse_img
                        pi_c = _parse_img(raw_img)
                        if pi_c is not None:
                            repo_c = pi_c.repository
                            if pi_c.registry == "docker.io" and not repo_c.startswith("library/") and "/" not in repo_c:
                                repo_c = f"library/{repo_c}"
                            norm = f"{pi_c.registry}/{repo_c}:{pi_c.tag or 'latest'}"
                    except Exception:
                        pass
                    containers_list.append({"name": nm, "image": norm})
                try:
                    log_event("kube.containers.list", namespace=item.namespace, resource=item.resource_name, kind=item.kind, count=len(containers_list))
                except Exception:
                    pass
            except Exception:
                pass
            
            # If all containers are excluded, skip the entire resource
            if not containers_list and primary_image_excluded:
                log_event("refresh.skip.all_containers_excluded", 
                         resource=item.resource_name, 
                         namespace=item.namespace)
                continue
            
            # If primary image was excluded but we have non-excluded containers, use first one
            if not image and containers_list:
                image = containers_list[0]["image"]
            # Normalize missing registry/tag for display and downstream integrations
            if image:
                try:
                    from .versioning import parse_image as _parse
                    pi = _parse(image)
                    if pi is not None:
                        repo = pi.repository
                        if pi.registry == "docker.io" and not repo.startswith("library/") and "/" not in repo:
                            repo = f"library/{repo}"
                        image = f"{pi.registry}/{repo}:{pi.tag or 'latest'}"
                except Exception:
                    pass
            exists_in_cluster = image is not None or kube.resource_exists(item.namespace, item.resource_name, item.kind)

            current_version = None
            if image:
                current_version = image.rsplit(":", 1)[-1]

            # Upsert resource first to determine if image changed
            existing: Resource | None = db.execute(
                select(Resource).where(
                    Resource.platform == item.platform,
                    Resource.namespace == item.namespace,
                    Resource.resource_name == item.resource_name,
                    Resource.kind == item.kind,
                )
            ).scalar_one_or_none()

            now = datetime.now(timezone.utc)

            created = False
            previous_image = None
            previous_version = None
            image_changed = False
            version_changed = False
            if existing is None:
                created = True
                image_changed = True
                existing = Resource(
                    platform=item.platform,
                    namespace=item.namespace,
                    resource_name=item.resource_name,
                    kind=item.kind,
                )
                db.add(existing)
            else:
                previous_image = existing.image
                previous_version = existing.current_version
                image_changed = (previous_image != image)
                version_changed = (previous_version != current_version)

            existing.image = image
            existing.current_version = current_version
            
            # Fetch replica count
            try:
                replicas = kube.get_replicas_for_resource(item.namespace, item.resource_name, item.kind)
                existing.replicas = replicas
                if replicas is not None and replicas == 0:
                    log_event("kube.replicas.zero", namespace=item.namespace, resource=item.resource_name, kind=item.kind)
            except Exception as e:
                log_event("kube.replicas.fetch_error", namespace=item.namespace, resource=item.resource_name, error=str(e))
                existing.replicas = None
            
            # Fetch and cache manifest YAML for comparison (only for managed products)
            # Save manifest if resource belongs to a managed product (has product_name)
            # Note: Excluded container images don't prevent manifest saving for Compare feature
            # The goal is to compare product deployments even if they have sidecar containers from excluded registries
            should_save_manifest = bool(item.product_name or existing.product_name)
            if should_save_manifest:
                try:
                    manifest = kube.get_resource_manifest(item.namespace, item.resource_name, item.kind)
                    if manifest:
                        existing.manifest_yaml = manifest
                        existing.manifest_updated_at = now
                        
                        # Also fetch related objects (PDB, HPA, Service, ConfigMap, Secret, Route)
                        try:
                            related = kube.get_related_objects(item.namespace, item.resource_name, manifest)
                            # Count non-empty related objects
                            related_count = sum(len(v) for v in related.values())
                            if related_count > 0:
                                existing.related_objects = related
                                log_event("kube.related_objects.saved", 
                                         namespace=item.namespace, 
                                         resource=item.resource_name,
                                         pdb=len(related.get("pdb", [])),
                                         hpa=len(related.get("hpa", [])),
                                         service=len(related.get("service", [])),
                                         configmap=len(related.get("configmap", [])),
                                         secret=len(related.get("secret", [])),
                                         route=len(related.get("route", [])))
                            else:
                                existing.related_objects = None
                        except Exception as e:
                            log_event("kube.related_objects.error", namespace=item.namespace, resource=item.resource_name, error=str(e))
                        
                        log_event("kube.manifest.saved", namespace=item.namespace, resource=item.resource_name, product=item.product_name or existing.product_name)

                        # --- Helm release status (Phase 1A) ---
                        # Drift of the live manifest vs what Helm last rendered. Written to
                        # DEDICATED columns (not security_info) so a forced security_info
                        # rebuild can't clobber it. Recompute only when forced / image
                        # changed / not yet determined / the release revision bumped — the
                        # decode is the expensive part and is bounded by helm_release_cache.
                        try:
                            _ann = ((manifest or {}).get("metadata") or {}).get("annotations") or {}
                            _rel_name = _ann.get("meta.helm.sh/release-name")
                            _rel_ns = _ann.get("meta.helm.sh/release-namespace")
                            if not _rel_name or not _rel_ns:
                                existing.helm_status = "none"
                                existing.helm_release_name = None
                                existing.helm_release_namespace = None
                                existing.helm_release_revision = None
                                existing.helm_drift_detail = None
                            else:
                                _recompute = force_sync or image_changed or existing.helm_status in (None, "unknown")
                                if not _recompute:
                                    # Cheap revision check (no gunzip): a helm upgrade bumps it.
                                    if kube.helm_latest_revision(_rel_ns, _rel_name) != existing.helm_release_revision:
                                        _recompute = True
                                if _recompute:
                                    _has_hpa = bool((existing.related_objects or {}).get("hpa"))
                                    _hs = kube.get_helm_status(
                                        item.namespace, item.resource_name, item.kind,
                                        manifest, has_hpa=_has_hpa, release_cache=helm_release_cache,
                                    )
                                    existing.helm_status = _hs.get("status")
                                    existing.helm_release_name = _hs.get("release_name")
                                    existing.helm_release_namespace = _hs.get("release_namespace")
                                    existing.helm_release_revision = _hs.get("revision")
                                    existing.helm_drift_detail = _hs.get("drift_detail")
                                    log_event("helm.status.computed", namespace=item.namespace,
                                              resource=item.resource_name, status=existing.helm_status,
                                              revision=existing.helm_release_revision)
                        except Exception as _e:
                            log_event("helm.status.refresh_error", namespace=item.namespace,
                                      resource=item.resource_name, error=str(_e))
                            if existing.helm_status is None:
                                existing.helm_status = "unknown"
                except Exception as e:
                    log_event("kube.manifest.fetch_error", namespace=item.namespace, resource=item.resource_name, error=str(e))
            
            # Now that image_changed is defined, fetch version/CVE data if needed
            latest_version = None
            version_diff = "unknown"
            vulns = []
            advice = None
            parsed = parse_image(image)
            
            # Only fetch version/CVE data if image changed or missing (or force mode)
            if parsed and (force_sync or image_changed or not existing.latest_version):
                # Check cache first for this normalized image
                cache_key = image  # Already normalized
                
                if cache_key in image_cache:
                    # Reuse cached data from earlier resource in this sync
                    cached_data = image_cache[cache_key]
                    latest_version = cached_data.get("latest_version")
                    version_diff = cached_data.get("version_diff")
                    vulns = cached_data.get("vulns", [])
                    advice = cached_data.get("advice")
                    log_event("refresh.cache_hit", image=cache_key, resource=item.resource_name)
                else:
                    # Fetch from external APIs
                    if current_version == "latest":
                        latest_version = "latest"
                        version_diff = "same"
                        log_event("version.assume_latest", image=image)
                    else:
                        # Check if we should use primary backend for latest version
                        latest_version_config = get_latest_version_config(db)
                        use_primary = latest_version_config.get("mode") == "primary" and not is_primary_backend()
                        
                        if use_primary:
                            log_event("refresh.latest_version.via_primary", 
                                     resource=item.resource_name, image=image)
                            latest_version = fetch_latest_version_from_primary(db, image)
                        else:
                            latest_version = fetch_latest_version(parsed)
                        version_diff = compare_semver(current_version, latest_version).category
                    # We no longer compute/store path-to-latest
                    all_tags = []
                    # Compute and persist vulnerabilities/advice
                    vulns = compute_cves_for_image(parsed, current_version, latest_version or (all_tags[0] if all_tags else None))
                    advice = build_upgrade_advice(version_diff, vulns)
                    
                    # Cache the results for this sync
                    image_cache[cache_key] = {
                        "latest_version": latest_version,
                        "version_diff": version_diff,
                        "vulns": vulns,
                        "advice": advice
                    }
                
                existing.latest_version = latest_version
                existing.version_diff = version_diff
            elif parsed:
                # Reuse cached CVE/advice from existing security_info
                try:
                    si_cached = existing.security_info or {}
                    vulns = si_cached.get("vulnerabilities", [])
                    advice = si_cached.get("advice")
                    latest_version = existing.latest_version
                    version_diff = existing.version_diff
                    log_event("refresh.skip.unchanged", resource=item.resource_name, namespace=item.namespace)
                except Exception:
                    vulns = []
                    advice = None
                    latest_version = existing.latest_version
                    version_diff = existing.version_diff
            else:
                # No parsed image, use existing data
                latest_version = existing.latest_version
                version_diff = existing.version_diff
            
            # Persist product name from sources, if provided
            try:
                if getattr(item, "product_name", None):
                    existing.product_name = item.product_name
            except Exception:
                pass
            # Note: exists_in_cluster and containers_list will be merged into security_info later
            # Resource-level EOL lookup is disabled - using per-container EOL instead
            # Persist security info (CVEs/advice) on the resource
            try:
                twist: dict | None = None
                # Prefer current image; fall back to last known image if current not available
                image_for_scan = image or getattr(existing, "image", None)
                # Only fetch Twistlock if image changed or data is older than 1 day
                cached_twist = None
                twist_age_hours = 999
                try:
                    if existing.security_info:
                        cached_twist = existing.security_info.get("twistlock")
                        twist_timestamp = existing.security_info.get("twistlock_fetched_at")
                        if twist_timestamp:
                            twist_dt = datetime.fromisoformat(twist_timestamp)
                            twist_age_hours = (now - twist_dt).total_seconds() / 3600
                except Exception:
                    pass
                
                should_fetch_twist = image_for_scan and (force_sync or image_changed or not cached_twist or twist_age_hours > twist_refresh_hours)
                
                if should_fetch_twist:
                    # Check cache first
                    twist_cache_key = f"twist:{image_for_scan}"
                    if twist_cache_key in image_cache:
                        twist = image_cache[twist_cache_key]
                        log_event("refresh.cache_hit.twistlock", image=image_for_scan)
                    else:
                        try:
                            # Check if we should use primary backend for Twistlock
                            twistlock_config = get_twistlock_config(db)
                            use_primary_twistlock = twistlock_config.get("mode") == "primary" and not is_primary_backend()
                            
                            if use_primary_twistlock:
                                log_event("refresh.twistlock.via_primary", 
                                         resource=item.resource_name, image=image_for_scan)
                                twist = fetch_twistlock_from_primary(db, image_for_scan)
                            else:
                                twist = _retry_with_backoff(
                                    lambda: fetch_image_report(image_for_scan),
                                    max_retries=3,
                                    initial_delay=0.5,
                                    context="twistlock"
                                )
                            if twist is None:
                                log_event("twistlock.report.none", image=image_for_scan)
                            # Cache the result
                            image_cache[twist_cache_key] = twist
                        except Exception:
                            log_event("twistlock.report.error", image=image_for_scan)
                            twist = None
                elif image_for_scan and cached_twist:
                    twist = cached_twist
                    log_event("twistlock.skip.cached", image=image_for_scan, age_hours=round(twist_age_hours, 1))
                else:
                    log_event("twistlock.skip.no_image", resource=item.resource_name, namespace=item.namespace)

                # ── Trivy scan ──
                trivy_result: dict | None = None
                if image_for_scan:
                    trivy_cache_key = f"trivy:{image_for_scan}"
                    if trivy_cache_key in image_cache:
                        trivy_result = image_cache[trivy_cache_key]
                    else:
                        # Always read the cached Trivy result — even on force_sync. Trivy
                        # scans run in the BACKGROUND, so the cache holds the latest
                        # COMPLETED scan. Using it for this write avoids overwriting
                        # security_info.trivy with None while a fresh scan is still running
                        # (race with apply_trivy_to_resources); the bg scan below
                        # re-freshens the cache for next time.
                        cached_trivy_data, trivy_hit = get_cached_trivy(db, image_for_scan)
                        if trivy_hit:
                            trivy_result = cached_trivy_data
                            image_cache[trivy_cache_key] = trivy_result
                    # (Re)submit a background scan when forced or when nothing is cached,
                    # to keep the cache fresh; federation pulls from primary if delegated.
                    if trivy_result is None or force_sync:
                        trivy_cfg = get_trivy_config(db)
                        if trivy_cfg.get("mode") == "primary" and not is_primary_backend():
                            bg_result = fetch_trivy_from_primary(db, image_for_scan)
                            if bg_result:
                                trivy_result = bg_result
                                image_cache[trivy_cache_key] = bg_result
                        else:
                            trivy_submit_scan(image_for_scan)
                    # Last resort: keep whatever was already stored rather than None.
                    if trivy_result is None:
                        try:
                            if existing.security_info:
                                trivy_result = existing.security_info.get("trivy")
                        except Exception:
                            pass

                # Per-container Twistlock scans and version checks
                # Try to reuse cached container data if containers haven't changed
                cached_containers = None
                try:
                    cached_containers = (existing.security_info or {}).get("containers") if existing.security_info and not image_changed else None
                except Exception:
                    pass
                
                # Check if containers changed by comparing current with cached
                containers_changed = False
                if containers_list and cached_containers:
                    # Create comparable sets of container images
                    current_images = {(c.get("name"), c.get("image")) for c in containers_list}
                    cached_images = {(c.get("name"), c.get("image")) for c in cached_containers}
                    containers_changed = current_images != cached_images
                    if containers_changed:
                        log_event("kube.containers.changed", 
                                 resource=item.resource_name, 
                                 namespace=item.namespace,
                                 current=len(containers_list),
                                 cached=len(cached_containers))
                
                if containers_list and (force_sync or image_changed or not cached_containers or containers_changed):
                    for c in containers_list:
                        try:
                            img_c = c.get("image")
                            container_name_c = c.get("name", "")
                            if not img_c:
                                continue
                            # Twistlock scan with retry - check cache first
                            twist_c_key = f"twist:{img_c}"
                            if twist_c_key in image_cache:
                                tw_c = image_cache[twist_c_key]
                                log_event("refresh.cache_hit.twistlock.container", image=img_c)
                            else:
                                # Check if we should use primary backend for Twistlock
                                twistlock_config = get_twistlock_config(db)
                                use_primary_twistlock = twistlock_config.get("mode") == "primary" and not is_primary_backend()
                                
                                if use_primary_twistlock:
                                    log_event("refresh.twistlock.via_primary.container", image=img_c)
                                    tw_c = fetch_twistlock_from_primary(db, img_c)
                                else:
                                    tw_c = _retry_with_backoff(
                                        lambda: fetch_image_report(img_c),
                                        max_retries=3,
                                        initial_delay=0.5,
                                        context="twistlock.container"
                                    )
                                if tw_c is None:
                                    log_event("twistlock.container.report.none", image=img_c)
                                image_cache[twist_c_key] = tw_c
                            c["twistlock"] = tw_c

                            # Trivy per container
                            trivy_c_key = f"trivy:{img_c}"
                            trv_c = None
                            if trivy_c_key in image_cache:
                                trv_c = image_cache[trivy_c_key]
                            else:
                                # Always read the cache (even on force_sync) — background
                                # scan result; avoids clobbering with None. Fresh scan is
                                # (re)submitted below.
                                cached_trv, trv_hit = get_cached_trivy(db, img_c)
                                if trv_hit:
                                    trv_c = cached_trv
                                    image_cache[trivy_c_key] = trv_c
                            if trv_c is None or force_sync:
                                trivy_cfg = get_trivy_config(db)
                                if trivy_cfg.get("mode") == "primary" and not is_primary_backend():
                                    bg_trv = fetch_trivy_from_primary(db, img_c)
                                    if bg_trv:
                                        trv_c = bg_trv
                                        image_cache[trivy_c_key] = bg_trv
                                else:
                                    trivy_submit_scan(img_c)
                            c["trivy"] = trv_c

                            # Version check
                            try:
                                parsed_c = parse_image(img_c)
                                if parsed_c:
                                    current_ver_c = img_c.rsplit(":", 1)[-1] if ":" in img_c else "latest"
                                    if current_ver_c == "latest":
                                        c["latest_version"] = "latest"
                                        c["version_diff"] = "same"
                                    else:
                                        # Check cache for version info
                                        ver_cache_key = f"ver:{img_c}"
                                        if ver_cache_key in image_cache:
                                            cached_ver = image_cache[ver_cache_key]
                                            c["latest_version"] = cached_ver["latest"]
                                            c["version_diff"] = cached_ver["diff"]
                                            log_event("refresh.cache_hit.version.container", image=img_c)
                                        else:
                                            # Check if we should use primary backend for latest version
                                            latest_version_config = get_latest_version_config(db)
                                            use_primary = latest_version_config.get("mode") == "primary" and not is_primary_backend()
                                            
                                            if use_primary:
                                                log_event("refresh.latest_version.via_primary.container", image=img_c)
                                                latest_c = fetch_latest_version_from_primary(db, img_c)
                                            else:
                                                latest_c = fetch_latest_version(parsed_c)
                                            c["latest_version"] = latest_c
                                            diff_c = compare_semver(current_ver_c, latest_c).category if latest_c else "unknown"
                                            c["version_diff"] = diff_c
                                            image_cache[ver_cache_key] = {"latest": latest_c, "diff": diff_c}
                            except Exception:
                                c["latest_version"] = None
                                c["version_diff"] = "unknown"
                            
                            # Per-container EOL lookup
                            try:
                                if current_ver_c and current_ver_c != "latest":
                                    # Special case: Dapr has custom EOL source
                                    if (getattr(existing, "product_name", None) or "").lower() == "dapr" or (parsed_c and "dapr" in parsed_c.repository.lower()):
                                        try:
                                            # Parse version (X.Y.Z -> X.Y for Dapr)
                                            from .versioning import parse_version_tuple as _dapr_pvt
                                            dapr_t = _dapr_pvt(current_ver_c)
                                            if dapr_t:
                                                dapr_maj, dapr_min, _ = dapr_t
                                                dapr_minor_ver = f"{dapr_maj}.{dapr_min}"
                                                # Dapr support table from https://docs.dapr.io/operations/support/support-release-policy/
                                                # Maps minor version to (status, approximate_eol_date)
                                                # Based on October 2025 table: 1.16, 1.15, 1.14 supported; rest unsupported
                                                dapr_support = {
                                                    "1.16": ("Supported", None),
                                                    "1.15": ("Supported", None),
                                                    "1.14": ("Supported", "2025-04-30"),  # Estimated 6 months after 1.14.4
                                                    "1.13": ("Unsupported", "2024-10-31"),
                                                    "1.12": ("Unsupported", "2024-07-31"),
                                                    "1.11": ("Unsupported", "2024-04-30"),
                                                    "1.10": ("Unsupported", "2024-01-31"),
                                                    "1.9": ("Unsupported", "2023-10-31"),
                                                    "1.8": ("Unsupported", "2023-07-31"),
                                                    "1.7": ("Unsupported", "2023-04-30"),
                                                    "1.6": ("Unsupported", "2023-01-31"),
                                                }
                                                status, eol_date = dapr_support.get(dapr_minor_ver, ("Unsupported", "2024-01-01"))
                                                c["eol_date"] = eol_date
                                                log_event("eol.container.dapr", container=container_name_c, version=dapr_minor_ver, status=status, eol=eol_date)
                                                continue  # Skip standard EOL lookup for Dapr
                                        except Exception as e_dapr:
                                            log_event("eol.dapr.error", error=str(e_dapr))
                                    
                                    # Build EOL product candidates from image and container name
                                    eol_candidates = []
                                    # Try image repository name (e.g., fluent/fluent-bit -> fluent-bit, apache/apisix -> apisix)
                                    if parsed_c and "/" in parsed_c.repository:
                                        repo_name = parsed_c.repository.split("/")[-1]
                                        eol_candidates.append(repo_name)
                                        # Also try vendor-product format (apache-apisix for apache/apisix)
                                        vendor = parsed_c.repository.split("/")[0]
                                        if vendor and vendor != "library":
                                            eol_candidates.append(f"{vendor}-{repo_name}")
                                    # Try container name
                                    if container_name_c and container_name_c.lower() not in [ec.lower() for ec in eol_candidates]:
                                        eol_candidates.append(container_name_c)
                                        # Normalize typos
                                        if "fulentbit" in container_name_c.lower():
                                            eol_candidates.append(container_name_c.lower().replace("fulentbit", "fluentbit"))
                                        # Try first word before hyphen
                                        if "-" in container_name_c:
                                            first_word = container_name_c.split("-")[0]
                                            if first_word not in eol_candidates:
                                                eol_candidates.append(first_word)
                                    
                                    log_event("eol.container.candidates", container=container_name_c, candidates=eol_candidates)
                                    
                                    # Check EOL mode config (primary or local)
                                    eol_config = get_eol_config(db)
                                    eol_mode = eol_config.get("mode", "local")
                                    
                                    # Try each candidate
                                    for eol_prod in eol_candidates:
                                        try:
                                            # Check cache for EOL product data
                                            eol_cache_key = f"eol:{eol_prod}:{current_ver_c}"
                                            if eol_cache_key in image_cache:
                                                c["eol_date"] = image_cache[eol_cache_key]
                                                log_event("refresh.cache_hit.eol.container", product=eol_prod, version=current_ver_c)
                                                break
                                            
                                            # If mode is 'primary', fetch from primary backend
                                            if eol_mode == "primary":
                                                log_event("refresh.eol.via_primary", resource=item.resource_name, container=container_name_c, product=eol_prod)
                                                full_image = c.get("image", "")
                                                eol_data = fetch_eol_from_primary(db, full_image, eol_prod, current_ver_c)
                                                if eol_data:
                                                    raw_fed_eol = eol_data.get("eol")
                                                    eol_val_c = str(raw_fed_eol) if isinstance(raw_fed_eol, str) and raw_fed_eol else None
                                                    c["eol_date"] = eol_val_c
                                                    # Cache the EOL result
                                                    image_cache[eol_cache_key] = eol_val_c
                                                    log_event("eol.container.set.via_federation", container=container_name_c, product=eol_prod, eol=eol_val_c)
                                                    break
                                                continue  # Try next candidate
                                            
                                            # Local mode: fetch directly from endoflife.date
                                            base_row_c = db.query(ConfigKV).filter(ConfigKV.key == "eol_api_url").one_or_none()
                                            base_url_c = (base_row_c.value if base_row_c and base_row_c.value else "https://endoflife.date")
                                            url_c = f"{base_url_c.rstrip('/')}/api/v1/products/{eol_prod}"
                                            
                                            import urllib.request as _req
                                            req_c = _req.Request(url_c)
                                            req_c.add_header("Accept", "application/json")
                                            with _req.urlopen(req_c, timeout=6) as resp_c:
                                                raw_c = resp_c.read().decode("utf-8")
                                            
                                            import json as _js
                                            data_c = _js.loads(raw_c)
                                            series_c = []
                                            if isinstance(data_c, list):
                                                series_c = data_c
                                            elif isinstance(data_c, dict) and isinstance(data_c.get("result"), dict):
                                                res_c = data_c["result"]
                                                series_c = res_c.get("releases") or res_c.get("cycles") or []
                                            
                                            if series_c:
                                                # Match version to series (X.Y.Z -> X.Y -> X)
                                                from .versioning import parse_version_tuple as _pvt_c
                                                ver_candidates = []
                                                try:
                                                    t_c = _pvt_c(current_ver_c)
                                                    if t_c:
                                                        maj_c, minr_c, patch_c = t_c
                                                        ver_candidates = [f"{maj_c}.{minr_c}.{patch_c}", f"{maj_c}.{minr_c}", f"{maj_c}"]
                                                except Exception:
                                                    pass
                                                
                                                chosen_c = None
                                                for vc in ver_candidates:
                                                    for sc in series_c:
                                                        name_c = str(sc.get("name") or sc.get("cycle") or "")
                                                        if name_c == vc or name_c.startswith(vc):
                                                            chosen_c = sc
                                                            break
                                                    if chosen_c:
                                                        break
                                                
                                                if chosen_c:
                                                    raw_eol_c = chosen_c.get("eolFrom") or chosen_c.get("eol")
                                                    eol_val_c = str(raw_eol_c) if isinstance(raw_eol_c, str) and raw_eol_c else None
                                                    c["eol_date"] = eol_val_c
                                                    # Cache the EOL result
                                                    eol_cache_key = f"eol:{eol_prod}:{current_ver_c}"
                                                    image_cache[eol_cache_key] = eol_val_c
                                                    log_event("eol.container.set", container=container_name_c, product=eol_prod, series=chosen_c.get("name"), eol=eol_val_c)
                                                    break  # Found EOL, stop trying candidates
                                        except Exception:
                                            continue
                            except Exception as e_eol:
                                log_event("eol.container.error", container=container_name_c, error=str(e_eol))
                        except Exception:
                            log_event("twistlock.container.report.error", image=c.get("image"))
                            c["twistlock"] = None
                elif cached_containers and not containers_changed:
                    # Reuse cached container data ONLY if containers haven't changed
                    containers_list = cached_containers
                    log_event("refresh.containers.cached", resource=item.resource_name, count=len(cached_containers))
                elif containers_changed:
                    # Containers changed but we didn't process them (shouldn't happen with fixed logic above)
                    log_event("refresh.containers.changed_but_not_processed", resource=item.resource_name)
                
                # If no containers, do EOL lookup for the primary image
                if not containers_list and image and current_version and (force_sync or image_changed or not existing.eol_date):
                    try:
                        eol_single_candidates = []
                        parsed_single = parse_image(image)
                        if parsed_single and "/" in parsed_single.repository:
                            repo_single = parsed_single.repository.split("/")[-1]
                            eol_single_candidates.append(repo_single)
                            vendor_single = parsed_single.repository.split("/")[0]
                            if vendor_single and vendor_single != "library":
                                eol_single_candidates.append(f"{vendor_single}-{repo_single}")
                        # Try product name as fallback
                        if getattr(existing, "product_name", None):
                            prod_name = existing.product_name
                            if prod_name.lower() not in [c.lower() for c in eol_single_candidates]:
                                eol_single_candidates.append(prod_name)
                        
                        log_event("eol.single.candidates", resource=item.resource_name, candidates=eol_single_candidates)
                        
                        for eol_s in eol_single_candidates:
                            try:
                                base_s = db.query(ConfigKV).filter(ConfigKV.key == "eol_api_url").one_or_none()
                                base_url_s = (base_s.value if base_s and base_s.value else "https://endoflife.date")
                                url_s = f"{base_url_s.rstrip('/')}/api/v1/products/{eol_s}"
                                
                                import urllib.request as _req_s
                                req_s = _req_s.Request(url_s)
                                req_s.add_header("Accept", "application/json")
                                with _req_s.urlopen(req_s, timeout=6) as resp_s:
                                    raw_s = resp_s.read().decode("utf-8")
                                
                                import json as _js_s
                                data_s = _js_s.loads(raw_s)
                                series_s = []
                                if isinstance(data_s, list):
                                    series_s = data_s
                                elif isinstance(data_s, dict) and isinstance(data_s.get("result"), dict):
                                    res_s = data_s["result"]
                                    series_s = res_s.get("releases") or res_s.get("cycles") or []
                                
                                if series_s:
                                    from .versioning import parse_version_tuple as _pvt_s
                                    ver_cands_s = []
                                    try:
                                        t_s = _pvt_s(current_version)
                                        if t_s:
                                            maj_s, minr_s, patch_s = t_s
                                            ver_cands_s = [f"{maj_s}.{minr_s}.{patch_s}", f"{maj_s}.{minr_s}", f"{maj_s}"]
                                    except Exception:
                                        pass
                                    
                                    chosen_s = None
                                    for vs in ver_cands_s:
                                        for ss in series_s:
                                            name_s = str(ss.get("name") or ss.get("cycle") or "")
                                            if name_s == vs or name_s.startswith(vs):
                                                chosen_s = ss
                                                break
                                        if chosen_s:
                                            break
                                    
                                    if chosen_s:
                                        raw_eol_s = chosen_s.get("eolFrom") or chosen_s.get("eol")
                                        eol_val_s = str(raw_eol_s) if isinstance(raw_eol_s, str) and raw_eol_s else None
                                        existing.eol_date = eol_val_s
                                        log_event("eol.single.set", resource=item.resource_name, product=eol_s, series=chosen_s.get("name"), eol=eol_val_s)
                                        break
                            except Exception:
                                continue
                    except Exception as e_s:
                        log_event("eol.single.error", resource=item.resource_name, error=str(e_s))
                # Merge with prior flags (exists_in_cluster, containers)
                merged_si = dict(existing.security_info or {})
                merged_si["vulnerabilities"] = (vulns if parsed else [])
                merged_si["vulnerability_count"] = (len(vulns) if parsed and vulns else 0)
                merged_si["advice"] = (advice if parsed else None)
                merged_si["twistlock"] = twist
                if twist:
                    merged_si["twistlock_fetched_at"] = now.isoformat()
                # A background Trivy scan submitted earlier in this same refresh may have
                # COMPLETED by now (fast images finish in ~1-2s while version/EOL/LLM work
                # runs). Re-read the cache so we don't overwrite a freshly-applied result
                # with None (closes the race vs apply_trivy_to_resources on first scan).
                if trivy_result is None and image_for_scan:
                    try:
                        _late_trv, _late_hit = get_cached_trivy(db, image_for_scan)
                        if _late_hit:
                            trivy_result = _late_trv
                    except Exception:
                        pass
                merged_si["trivy"] = trivy_result
                if trivy_result:
                    merged_si["trivy_fetched_at"] = now.isoformat()
                # ensure exists_in_cluster is preserved (was set earlier in this loop)
                if "exists_in_cluster" not in merged_si:
                    merged_si["exists_in_cluster"] = exists_in_cluster
                # preserve containers_list if we discovered any
                if containers_list:
                    merged_si["containers"] = containers_list
                existing.security_info = merged_si
                log_event("resource.security_info.set", resource=item.resource_name, namespace=item.namespace, twistlock_set=bool(twist))

                # Submit LLM advice for resources with significant vulnerabilities
                tw_crit = (twist or {}).get("vulnerabilityDistribution", {}).get("critical", 0) if twist else 0
                trv_crit = (trivy_result or {}).get("vulnerabilityDistribution", {}).get("critical", 0) if trivy_result else 0
                tw_rf = (twist or {}).get("riskFactorCount", 0) if twist else 0
                max_crit = max(tw_crit, trv_crit)

                has_fresh_llm = False
                llm_ts = merged_si.get("llm_advice_at")
                if llm_ts and not image_changed:
                    try:
                        age_h = (now - datetime.fromisoformat(llm_ts)).total_seconds() / 3600
                        has_fresh_llm = age_h < 24
                    except Exception:
                        pass

                if not has_fresh_llm and (max_crit > 0 or tw_rf >= 5):
                    try:
                        llm_cfg = get_llm_config(db)
                        if llm_cfg.get("mode") == "primary" and not is_primary_backend():
                            fed_advice = fetch_llm_from_primary(
                                db, image or "",
                                resource_name=item.resource_name,
                                current_version=current_version or "",
                                latest_version=latest_version or "",
                                version_diff=version_diff or "",
                                eol_date=str(existing.eol_date) if existing.eol_date else "",
                            )
                            if fed_advice:
                                merged_si["advice"] = fed_advice
                                merged_si["llm_advice"] = True
                                merged_si["llm_advice_at"] = now.isoformat()
                                existing.security_info = merged_si
                                existing.advice = fed_advice
                                db.commit()
                                log_event("llm.federation.saved", resource=item.resource_name)
                        else:
                            llm_submit_advice(
                                resource_id=existing.id,
                                resource_name=item.resource_name,
                                image=image or "",
                                current_version=current_version or "",
                                latest_version=latest_version or "",
                                version_diff=version_diff or "",
                                twistlock=twist,
                                trivy=trivy_result,
                                eol_date=str(existing.eol_date) if existing.eol_date else None,
                            )
                    except Exception:
                        pass
                elif has_fresh_llm:
                    log_event("llm.skip.cached", resource=item.resource_name, age_hours=round(age_h, 1))
            except Exception:
                pass

            # LLM support-status fallback when there is NO public EOL (endoflife.date) data.
            # Computed once per product+version (cleared when the version changes), applied
            # to matching resources by the background worker. Local LLM or, for
            # llm_mode=primary secondaries, the primary's LLM (non-blocking either way).
            try:
                if existing.eol_date:
                    # Real EOL date present → drop any stale LLM fallback.
                    if existing.eol_support_status is not None:
                        existing.eol_support_status = None
                        existing.eol_support_note = None
                elif existing.product_name:
                    if image_changed and existing.eol_support_status is not None:
                        existing.eol_support_status = None
                        existing.eol_support_note = None
                    # Self-heal a row whose stored note was garbled by an earlier parse
                    # (e.g. leaked <think> reasoning): drop it so the next refresh re-queries
                    # it with the current parser instead of showing raw chain-of-thought.
                    if existing.eol_support_status is not None and (existing.eol_support_note or "").lstrip().startswith("<"):
                        existing.eol_support_status = None
                        existing.eol_support_note = None
                    # TTL expiry: re-query when the cached LLM EOL status is older than the
                    # Admin-configurable window (default 3 days), or has no timestamp at all
                    # (rows written before this column existed) — so a stale/incorrect guess
                    # is refreshed with the current prompt instead of persisting forever.
                    if existing.eol_support_status is not None:
                        _stamp = existing.eol_support_at
                        _expired = _stamp is None
                        if _stamp is not None:
                            try:
                                _st = _stamp if _stamp.tzinfo else _stamp.replace(tzinfo=timezone.utc)
                                _expired = (now - _st).total_seconds() > _eol_support_ttl_days(db) * 86400
                            except Exception:
                                _expired = False
                        if _expired:
                            existing.eol_support_status = None
                            existing.eol_support_note = None
                    if existing.eol_support_status is None:
                        _llm_cfg_eol = get_llm_config(db)
                        if _llm_cfg_eol.get("mode") == "primary" and not is_primary_backend():
                            from .config_cache import get_config_cache
                            _cc = get_config_cache()
                            _purl, _ptok = _cc.get_primary_url(), _cc.get_auth_token()
                            if _purl and _ptok:
                                llm_submit_eol_status(existing.product_name, current_version or "",
                                                      primary_url=_purl, auth_token=_ptok,
                                                      image=existing.image or "")
                        else:
                            llm_submit_eol_status(existing.product_name, current_version or "",
                                                  image=existing.image or "")
            except Exception:
                pass

            # Note: newer_versions is not stored in DB; returned via API only
            existing.last_checked_at = now

            # Only set last_updated_at when the image changes (or when first created with an image)
            if created and image:
                existing.last_updated_at = now
            elif not created and previous_image != image:
                existing.last_updated_at = now

            db.flush()

            # Add history entries using resource_history table as source of truth.
            # This auto-heals any gap left by past (buggy) syncs: if the current
            # image differs from the last recorded history image, a new entry is
            # added. Works identically for primary (local) and federation
            # (remote) backends, since both run this code path.
            latest_hist_per_container: dict[str, ResourceHistory] = {}
            if not created:
                try:
                    hist_rows = (
                        db.query(ResourceHistory)
                        .filter(ResourceHistory.resource_id == existing.id)
                        .order_by(ResourceHistory.checked_at.desc())
                        .all()
                    )
                    for h in hist_rows:
                        # IMPORTANT: use a local name (hist_key) so we don't
                        # shadow the outer `key` tuple used later by the
                        # cleanup phase via `seen_keys.add(key)`.
                        hist_key = h.container_name or ""
                        if hist_key not in latest_hist_per_container:
                            latest_hist_per_container[hist_key] = h
                except Exception:
                    pass

            def _record_history(container_name, new_image, new_version,
                                new_latest, new_diff):
                """Insert history row if image changed; otherwise refresh the
                most recent row's latest_version/version_diff so the Change
                History view stays in sync with up-to-date lookups (e.g. after
                a successful fallback)."""
                hist_key = container_name or ""
                last_row = latest_hist_per_container.get(hist_key)
                last_image = (last_row.image or "") if last_row is not None else None
                if last_image is not None and last_image == (new_image or ""):
                    # Image unchanged: refresh metadata on the most recent row
                    # so stale latest_version/version_diff values get updated.
                    changed = False
                    if new_latest and last_row.latest_version != new_latest:
                        last_row.latest_version = new_latest
                        changed = True
                    if new_diff and last_row.version_diff != new_diff:
                        last_row.version_diff = new_diff
                        changed = True
                    if changed:
                        log_event(
                            "resource.history.metadata_refreshed",
                            resource=item.resource_name,
                            container=container_name,
                            latest_version=new_latest,
                            version_diff=new_diff,
                        )
                    return
                is_fresh = created or image_changed
                src = _history_source(triggered_by) if is_fresh else "sync-backfill"
                # Use last_updated_at only when filling a gap for a container
                # that already had history. For first-ever entries we have no
                # better signal, so use now.
                ts = now if (is_fresh or last_image is None) else (existing.last_updated_at or now)
                db.add(
                    ResourceHistory(
                        resource_id=existing.id,
                        container_name=container_name,
                        image=new_image,
                        version=new_version,
                        latest_version=new_latest,
                        version_diff=new_diff,
                        checked_at=ts,
                        source=src,
                    )
                )
                if created:
                    reason = "created"
                elif last_image is None:
                    reason = "missing_history"
                elif is_fresh:
                    reason = "image_changed"
                else:
                    reason = "backfill"
                log_event(
                    "resource.history.added",
                    resource=item.resource_name,
                    container=container_name,
                    reason=reason,
                    old_image=last_image or "",
                    new_image=new_image,
                    source=src,
                )

            if containers_list:
                for c in containers_list:
                    c_name = c.get("name", "")
                    c_image = c.get("image", "")
                    if not c_image:
                        continue
                    c_version = c_image.rsplit(":", 1)[-1] if ":" in c_image else None
                    _record_history(
                        container_name=c_name,
                        new_image=c_image,
                        new_version=c_version,
                        new_latest=c.get("latest_version"),
                        new_diff=c.get("version_diff"),
                    )
            elif image:
                # Resource has no per-container info; record at resource level
                _record_history(
                    container_name=None,
                    new_image=image,
                    new_version=current_version,
                    new_latest=latest_version,
                    new_diff=version_diff,
                )

            # Precompute the compact grid summary so the slim listing never has to
            # detoast the large security_info blob (R4 perf). Mirrors the trimmed
            # object the resources grid consumes.
            try:
                existing.sec_summary = build_resource_summary(existing.security_info, existing.image)
            except Exception:
                pass

            refreshed += 1
        except Exception as e:
            errors += 1
            # Collect error message for sync log
            error_msg = f"{item.platform}/{item.namespace}/{item.resource_name} ({item.kind}): {str(e)}"
            error_messages.append(error_msg)
            # Log item-level error details for troubleshooting
            try:
                log_event(
                    "refresh.item.error",
                    platform=item.platform,
                    namespace=item.namespace,
                    resource=item.resource_name,
                    kind=item.kind,
                    error=str(e),
                    traceback=traceback.format_exc().replace("\n", "\\n"),
                )
            except Exception:
                pass
            db.rollback()
            # After rollback, expire all objects in the session to clear the bad state
            db.expire_all()
        else:
            db.commit()
        finally:
            # Even if processing failed, mark the key as seen to avoid accidental deletion
            seen_keys.add(key)

    # Delete resources no longer present in sources list.
    # IMPORTANT SAFETY RULES:
    #   1. Cleanup ONLY touches the `resources` table. History/plan rows are
    #      removed via ORM cascade. No other tables (config_kv, backend_endpoints,
    #      users, sync_logs, etc.) are ever deleted here.
    #   2. Cleanup is skipped for filtered syncs (per-row sync) to avoid
    #      deleting unrelated resources.
    #   3. Cleanup is skipped if the sync was cancelled mid-way (items list
    #      is incomplete and seen_keys would be missing entries).
    #   4. A safety guard aborts cleanup if it would delete more than
    #      CLEANUP_MAX_DELETE_RATIO of the managed-platform resources, unless
    #      the per-backend `cleanup_force_enabled` flag is explicitly set.
    if not skip_cleanup and not cancelled:
        try:
            # Get unique platforms from the sources this backend is managing
            managed_platforms = set(item.platform for item in items)

            if managed_platforms:
                # Only query resources from platforms this backend manages
                all_resources: list[Resource] = db.execute(
                    select(Resource).where(Resource.platform.in_(managed_platforms))
                ).scalars().all()

                # Step 1: build the list of stale resources WITHOUT deleting yet
                stale: list[Resource] = []
                for r in all_resources:
                    key = (r.platform, r.namespace, r.resource_name, r.kind)
                    if key not in seen_keys:
                        stale.append(r)

                total_in_db = len(all_resources)
                to_delete = len(stale)
                ratio = (to_delete / total_in_db) if total_in_db > 0 else 0.0

                # Step 2: check the mass-delete safety guard
                CLEANUP_MAX_DELETE_RATIO = 0.10  # 10 %
                cleanup_force = _is_cleanup_force_enabled(db)

                if to_delete > 0 and ratio > CLEANUP_MAX_DELETE_RATIO and not cleanup_force:
                    # Abort cleanup entirely; do NOT delete anything.
                    log_event(
                        "resource.cleanup.aborted_mass_delete",
                        reason="ratio_above_threshold",
                        to_delete=to_delete,
                        total_in_db=total_in_db,
                        ratio=round(ratio, 4),
                        threshold=CLEANUP_MAX_DELETE_RATIO,
                        platforms=sorted(managed_platforms),
                        hint="Set cleanup_force_enabled=true on the backend to override.",
                    )
                else:
                    if to_delete > 0 and ratio > CLEANUP_MAX_DELETE_RATIO and cleanup_force:
                        log_event(
                            "resource.cleanup.force_override",
                            to_delete=to_delete,
                            total_in_db=total_in_db,
                            ratio=round(ratio, 4),
                            threshold=CLEANUP_MAX_DELETE_RATIO,
                            platforms=sorted(managed_platforms),
                        )

                    # Step 3: perform the actual deletion
                    for r in stale:
                        db.delete(r)
                        deleted += 1
                        log_event(
                            "resource.deleted",
                            platform=r.platform,
                            namespace=r.namespace,
                            resource=r.resource_name,
                            kind=r.kind,
                        )

                    if deleted:
                        db.commit()
        except Exception as e:
            log_event("resource.cleanup.error", error=str(e))
            db.rollback()
    elif cancelled and not skip_cleanup:
        log_event(
            "resource.cleanup.skipped",
            reason="sync_cancelled",
            seen=len(seen_keys),
        )

    # Update sync log with final status
    if sync_log_id:
        try:
            sync_log_final = db.query(SyncLog).filter(SyncLog.id == sync_log_id).one_or_none()
            if sync_log_final:
                now = datetime.now(timezone.utc)
                
                # Don't overwrite if already cancelled
                if sync_log_final.status != "cancelled":
                    sync_log_final.finished_at = now
                    if cancelled:
                        sync_log_final.status = "cancelled"
                        sync_log_final.error_message = "Cancelled by user"
                    else:
                        sync_log_final.status = "success" if errors == 0 else "error"
                    # Calculate duration
                    if sync_log_final.started_at:
                        try:
                            duration = (now - sync_log_final.started_at).total_seconds()
                            sync_log_final.duration_seconds = int(duration)
                        except Exception:
                            pass
                    # Store error messages (limit to 5000 chars to avoid DB overflow)
                    if error_messages and not cancelled:
                        combined_errors = "\n".join(error_messages)
                        sync_log_final.error_message = combined_errors[:5000]
                
                sync_log_final.resources_refreshed = refreshed
                sync_log_final.errors = errors
                db.commit()
            
            # Clear from cancelled set
            clear_cancelled_sync(sync_log_id)
        except Exception:
            pass
    
    # Log cache statistics
    cache_stats = {
        "total_cached": len(image_cache),
        "versions": sum(1 for k in image_cache.keys() if k.startswith("ver:")),
        "twistlock": sum(1 for k in image_cache.keys() if k.startswith("twist:")),
        "eol": sum(1 for k in image_cache.keys() if k.startswith("eol:")),
        "other": sum(1 for k in image_cache.keys() if not k.startswith(("ver:", "twist:", "eol:")))
    }
    log_event("refresh.done", refreshed=refreshed, errors=errors, deleted=deleted, force_mode=force_sync, cache=cache_stats)
    return {"refreshed": refreshed, "errors": errors, "deleted": deleted}
