from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import engine, SessionLocal
from .schema_migration import ensure_schema
from .models import (
    Base, ConfigKV, BackendEndpoint, EmailRecipient,
    Resource, ResourceHistory, ResourcePlan, Role, User, SyncLog, UserChart,
    ProductList, ManagedProduct, ImageUpdateJob, ImageUpdateResult
)
from .routers import resources, health, auth, compare
from .routers import image_update
from .services.refresh import refresh_from_sources
import threading
import time
import os
import asyncio
from datetime import datetime, timezone
from .logging_utils import log_event


app = FastAPI(title="OpenShift Patch Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _seed_default_roles() -> None:
    """Seed default roles (admin, read-only) if they don't exist."""
    from .models import Role
    try:
        db = SessionLocal()
        # Check and create admin role
        if not db.query(Role).filter(Role.name == "admin").first():
            db.add(Role(name="admin"))
            log_event("roles.seed", role="admin")
        # Check and create read-only role
        if not db.query(Role).filter(Role.name == "read-only").first():
            db.add(Role(name="read-only"))
            log_event("roles.seed", role="read-only")
        db.commit()
    except Exception as e:
        log_event("roles.seed.error", error=str(e))
    finally:
        db.close()


def _backfill_sec_summary() -> None:
    """Populate resources.sec_summary for rows where it's NULL (one-time, idempotent).

    Pure JSON reshaping from the existing security_info — no external calls — so it's
    fast even for thousands of rows. After this, the slim resources listing reads the
    small sec_summary column instead of detoasting the large security_info blob."""
    from .services.refresh import build_resource_summary
    db = SessionLocal()
    try:
        rows = db.query(Resource).filter(Resource.sec_summary.is_(None)).all()
        if not rows:
            return
        updated = 0
        for r in rows:
            try:
                r.sec_summary = build_resource_summary(r.security_info, r.image)
                updated += 1
            except Exception:
                continue
            if updated % 500 == 0:
                db.commit()
        db.commit()
        log_event("sec_summary.backfill", updated=updated)
    except Exception as e:
        log_event("sec_summary.backfill.error", error=str(e))
        db.rollback()
    finally:
        db.close()


def _register_backend() -> None:
    """Verify backend token and activate if matching."""
    import os
    from datetime import datetime, timezone
    try:
        db = SessionLocal()
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        backend_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
        backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        backend_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        is_default = os.getenv("BACKEND_IS_DEFAULT", "true").lower() == "true"
        backend_type = os.getenv("BACKEND_TYPE", "primary")
        primary_url = os.getenv("PRIMARY_BACKEND_URL", "")
        
        # For secondary backends: register self and ensure primary entry exists locally
        if backend_type == "secondary" and primary_url:
            log_event("backend.federation.mode", name=backend_name, primary=primary_url)

            # Ensure a BackendEndpoint for the primary exists in our local DB.
            # The primary sends X-Backend-Token = our BACKEND_AUTH_TOKEN when
            # forwarding resource-detail requests; we need to validate that token.
            existing_primary = db.query(BackendEndpoint).filter(
                BackendEndpoint.api_url == primary_url.rstrip("/"),
                BackendEndpoint.backend_type == "primary",
            ).one_or_none()

            if existing_primary:
                existing_primary.auth_token = backend_token
                existing_primary.approved = True
                existing_primary.enabled = True
                existing_primary.last_seen_at = datetime.now(timezone.utc)
                db.commit()
                log_event("backend.primary_entry.updated", api_url=primary_url)
            else:
                primary_entry = BackendEndpoint(
                    name="primary-backend",
                    platform="primary",
                    api_url=primary_url.rstrip("/"),
                    auth_token=backend_token,
                    enabled=True,
                    is_default=False,
                    backend_type="primary",
                    approved=True,
                    approved_by="auto-secondary-startup",
                    approved_at=datetime.now(timezone.utc),
                    skip_tls_verify=os.getenv("BACKEND_SKIP_TLS_VERIFY", "false").lower() == "true",
                    use_dynamic_discovery=False,
                    force_sync_enabled=False,
                    last_seen_at=datetime.now(timezone.utc),
                )
                db.add(primary_entry)
                db.commit()
                log_event("backend.primary_entry.created", api_url=primary_url)

            db.close()
            return
        
        # For default backend, auto-approve and generate token if needed
        if is_default:
            existing = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
            if existing:
                existing.last_seen_at = datetime.now(timezone.utc)
                existing.approved = True
                existing.approved_by = "system"
                db.commit()
                log_event("backend.heartbeat", name=backend_name, is_default=True)
            else:
                # Create default backend with auto-approval
                import secrets as sec
                new_backend = BackendEndpoint(
                    name=backend_name,
                    platform=backend_platform,
                    api_url=backend_url,
                    auth_token=sec.token_urlsafe(32),
                    enabled=True,
                    is_default=True,
                    approved=True,
                    approved_by="system",
                    approved_at=datetime.now(timezone.utc),
                    skip_tls_verify=False,
                    last_seen_at=datetime.now(timezone.utc)
                )
                db.add(new_backend)
                db.commit()
                log_event("backend.registered.default", name=backend_name)
        else:
            # Non-default backend: must match token from database
            if not backend_token:
                log_event("backend.verify.no_token", name=backend_name)
                db.close()
                return
            
            # Find backend by name, platform, URL, and token
            matching = db.query(BackendEndpoint).filter(
                BackendEndpoint.name == backend_name,
                BackendEndpoint.platform == backend_platform,
                BackendEndpoint.api_url == backend_url.rstrip("/"),
                BackendEndpoint.auth_token == backend_token
            ).one_or_none()
            
            if matching:
                # Token matches - approve and update heartbeat
                if not matching.approved:
                    matching.approved = True
                    matching.approved_at = datetime.now(timezone.utc)
                    log_event("backend.verified.approved", name=backend_name)
                matching.last_seen_at = datetime.now(timezone.utc)
                db.commit()
                log_event("backend.heartbeat", name=backend_name, approved=True)
            else:
                # No match - log warning but don't block startup
                log_event("backend.verify.no_match", 
                         name=backend_name, 
                         platform=backend_platform,
                         url=backend_url,
                         has_token=bool(backend_token))
        
        db.close()
    except Exception as e:
        log_event("backend.register.error", error=str(e))


async def _async_register_backend() -> None:
    """Handle async federation registration for secondary backends."""
    import os
    backend_type = os.getenv("BACKEND_TYPE", "primary")
    primary_url = os.getenv("PRIMARY_BACKEND_URL", "")
    
    if backend_type == "secondary" and primary_url:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        backend_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
        backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        
        log_event("backend.federation.registering", name=backend_name, primary=primary_url)
        
        try:
            from .services.federation import FederationClient
            fed_client = FederationClient()
            skip_tls = os.getenv("BACKEND_SKIP_TLS_VERIFY", "false").lower() == "true"
            success = await fed_client.self_register(
                api_url=backend_url,
                skip_tls_verify=skip_tls,
                description=f"Auto-registered from {backend_platform}"
            )
            if success:
                log_event("backend.federation.registered", name=backend_name)
            else:
                log_event("backend.federation.register_failed", name=backend_name)
        except Exception as e:
            log_event("backend.federation.register_error", name=backend_name, error=str(e))


@app.on_event("startup")
async def on_startup() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:  # Do not crash app if DB isn't up yet
        import logging
        logging.warning(f"DB create_all skipped: {e}")
    try:
        # Apply lightweight schema migrations (idempotent)
        ensure_schema(engine)
    except Exception as e:
        import logging
        logging.warning(f"Schema migration skipped: {e}")
    try:
        # One-time backfill of resources.sec_summary (R4 perf). Only touches rows where
        # it's NULL, so it's a no-op after the first run / once refresh populates it.
        _backfill_sec_summary()
    except Exception as e:
        import logging
        logging.warning(f"sec_summary backfill skipped: {e}")
    try:
        # Seed default roles (admin, read-only)
        _seed_default_roles()
    except Exception as e:
        import logging
        logging.warning(f"Role seeding skipped: {e}")
    try:
        # Cleanup orphan jobs left in "executing" state from a previous crash/restart
        db = SessionLocal()
        try:
            orphan_jobs = db.query(ImageUpdateJob).filter(
                ImageUpdateJob.status == "executing"
            ).all()
            for oj in orphan_jobs:
                oj.status = "failed"
                oj.finished_at = datetime.now(timezone.utc)
                oj.notes = (oj.notes or "") + "\n[Auto-failed on startup: backend restarted while job was executing]"
            if orphan_jobs:
                db.commit()
                log_event("startup.orphan_jobs_cleaned", count=len(orphan_jobs),
                         job_ids=[j.id for j in orphan_jobs])
        finally:
            db.close()
    except Exception as e:
        import logging
        logging.warning(f"Orphan job cleanup skipped: {e}")
    try:
        # Register this backend in the database (synchronous part)
        _register_backend()
    except Exception as e:
        import logging
        logging.warning(f"Backend registration skipped: {e}")
    
    # Handle async federation registration
    try:
        await _async_register_backend()
    except Exception as e:
        import logging
        logging.warning(f"Async backend registration skipped: {e}")
    # Start background refresh if configured (reads schedule from this backend's config)
    def _get_schedule() -> tuple[str, int]:
        """Returns (schedule_type, interval_seconds). schedule_type: 'cron' or 'interval'"""
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        backend_type = os.getenv("BACKEND_TYPE", "primary")
        is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
        
        try:
            # Federation secondary backends read schedule from config_cache (fetched from primary)
            if is_federation_secondary:
                from .services.config_cache import get_config_cache
                cache = get_config_cache()
                backend_config = cache.get_backend_config()
                
                if not backend_config:
                    log_event("autosync.schedule.not_in_cache", backend=backend_name,
                             reason="Backend config not fetched from primary yet")
                    return ("interval", settings.refresh_interval_seconds)
                
                auto_sync_schedule = backend_config.get("auto_sync_schedule")
                if not auto_sync_schedule:
                    log_event("autosync.schedule.empty", backend=backend_name,
                             reason="auto_sync_schedule not configured in primary", source="federation")
                    return ("interval", settings.refresh_interval_seconds)
                
                val = auto_sync_schedule.strip()
                # If it looks like a cron expression (contains spaces), return it as cron
                if " " in val and len(val.split()) >= 5:
                    log_event("autosync.schedule.detected", backend=backend_name,
                             type="cron", schedule=val, source="federation")
                    return ("cron", 0)
                
                # Otherwise treat as interval in seconds
                try:
                    interval = max(0, int(val))
                    log_event("autosync.schedule.detected", backend=backend_name,
                             type="interval", interval_seconds=interval, source="federation")
                    return ("interval", interval)
                except ValueError:
                    log_event("autosync.schedule.invalid", backend=backend_name,
                             schedule=val, reason="Not a valid cron or interval", source="federation")
            else:
                # Primary backend reads schedule from local database
                db = SessionLocal()
                backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                db.close()
                
                if not backend:
                    log_event("autosync.schedule.not_found", backend=backend_name,
                             reason="Backend record not found in database", source="database")
                    return ("interval", settings.refresh_interval_seconds)
                
                if not backend.auto_sync_schedule:
                    log_event("autosync.schedule.empty", backend=backend_name,
                             reason="auto_sync_schedule is NULL or empty", source="database")
                    return ("interval", settings.refresh_interval_seconds)
                
                val = backend.auto_sync_schedule.strip()
                # If it looks like a cron expression (contains spaces), return it as cron
                if " " in val and len(val.split()) >= 5:
                    log_event("autosync.schedule.detected", backend=backend_name,
                             type="cron", schedule=val, source="database")
                    return ("cron", 0)
                
                # Otherwise treat as interval in seconds
                try:
                    interval = max(0, int(val))
                    log_event("autosync.schedule.detected", backend=backend_name,
                             type="interval", interval_seconds=interval, source="database")
                    return ("interval", interval)
                except ValueError:
                    log_event("autosync.schedule.invalid", backend=backend_name,
                             schedule=val, reason="Not a valid cron or interval", source="database")
        except Exception as e:
            log_event("autosync.schedule.error", backend=backend_name, error=str(e))
        
        # Fallback to env var (legacy interval mode) or disabled
        log_event("autosync.schedule.fallback", backend=backend_name,
                 fallback_interval=settings.refresh_interval_seconds)
        return ("interval", settings.refresh_interval_seconds)
    
    # Start unified auto-sync loop that handles both cron and interval modes dynamically
    def _auto_sync_loop() -> None:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
        
        while True:
            try:
                # Check current schedule type
                stype, interval = _get_schedule()
                
                if stype == "cron":
                    # Execute cron-based scheduling
                    _run_cron_iteration(backend_name)
                elif stype == "interval" and interval > 0:
                    # Execute interval-based scheduling
                    log_event("autosync.start", backend=backend_name, schedule_type="interval", interval_seconds=interval)
                    db = SessionLocal()
                    try:
                        # Check backend type - federation secondary MUST use dynamic discovery
                        backend_type = os.getenv("BACKEND_TYPE", "primary")
                        is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
                        
                        if is_federation_secondary:
                            # Federation backends ALWAYS use dynamic discovery (fetch managed products from primary)
                            use_dynamic = True
                        else:
                            # Primary backend reads use_dynamic from database
                            use_dynamic = False
                            try:
                                backend_exec = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                                if backend_exec:
                                    use_dynamic = backend_exec.use_dynamic_discovery
                            except Exception:
                                pass
                        refresh_from_sources(db, triggered_by="auto", use_dynamic=use_dynamic)
                    finally:
                        db.close()
                    log_event("autosync.done", backend=backend_name)
                    time.sleep(max(60, interval))
                else:
                    # No schedule configured, wait and check again
                    log_event("autosync.disabled", backend=backend_name)
                    time.sleep(60)
            except Exception as e:
                log_event("autosync.error", backend=backend_name, error=str(e))
                time.sleep(60)
    
    def _run_cron_iteration(backend_name: str) -> None:
        """Execute one cron scheduling iteration - calculate next run and wait"""
        from datetime import datetime
        try:
            from croniter import croniter
        except ImportError:
            log_event("autosync.cron_mode", message="croniter library not installed - falling back to disabled")
            time.sleep(60)
            return
        
        try:
            # Read schedule from THIS backend's config (federation-aware)
            backend_type = os.getenv("BACKEND_TYPE", "primary")
            is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
            
            cron_expr = None
            if is_federation_secondary:
                # Secondary backend: read from federation config cache
                from .services.config_cache import get_config_cache
                cache = get_config_cache()
                backend_config = cache.get_backend_config()
                auto_sync_schedule = backend_config.get("auto_sync_schedule") if backend_config else None
                if auto_sync_schedule and " " in auto_sync_schedule:
                    cron_expr = auto_sync_schedule.strip()
                    log_event("autosync.cron.federation_mode", backend=backend_name, 
                             cron=cron_expr, source="federation_cache")
                else:
                    log_event("autosync.cron.no_federation_schedule", backend=backend_name,
                             backend_config_present=bool(backend_config),
                             schedule_value=auto_sync_schedule)
            else:
                # Primary backend: read from local database
                db = SessionLocal()
                backend = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                db.close()
                if backend and backend.auto_sync_schedule and " " in backend.auto_sync_schedule:
                    cron_expr = backend.auto_sync_schedule.strip()
                    log_event("autosync.cron.primary_mode", backend=backend_name, 
                             cron=cron_expr, source="database")
            
            if not cron_expr:
                log_event("autosync.cron.disabled", backend=backend_name, reason="no_cron_schedule",
                         is_federation=is_federation_secondary)
                time.sleep(60)  # Check again in 1 minute
                return
            base_time = datetime.now()
            cron = croniter(cron_expr, base_time)
            next_run = cron.get_next(datetime)
            sleep_seconds = (next_run - base_time).total_seconds()
            
            log_event("autosync.cron.scheduled", backend=backend_name, cron=cron_expr, next_run=next_run.isoformat(), sleep_seconds=int(sleep_seconds))
            
            # Sleep in 60-second intervals to allow schedule changes
            remaining = sleep_seconds
            while remaining > 0:
                sleep_chunk = min(60, remaining)
                time.sleep(sleep_chunk)
                remaining -= sleep_chunk
                
                # Check if schedule changed (federation-aware)
                new_cron_expr = None
                if is_federation_secondary:
                    # Refresh federation config (managed products, schedule, etc.)
                    from .services.federation import get_federation_client
                    from .services.config_cache import get_config_cache
                    try:
                        fed_client = get_federation_client()
                        cache = get_config_cache()
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        config = loop.run_until_complete(fed_client.fetch_shared_config(fed_client.registration_token))
                        loop.close()
                        if config:
                            cache._cache = config
                            cache._last_fetch = datetime.now()
                    except Exception as e:
                        log_event("autosync.cron.config_refresh_failed", backend=backend_name, error=str(e))
                    
                    cache = get_config_cache()
                    backend_config = cache.get_backend_config()
                    new_schedule = backend_config.get("auto_sync_schedule") if backend_config else None
                    if new_schedule and " " in new_schedule:
                        new_cron_expr = new_schedule.strip()
                else:
                    db_check = SessionLocal()
                    backend_check = db_check.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                    db_check.close()
                    if backend_check and backend_check.auto_sync_schedule:
                        new_cron_expr = backend_check.auto_sync_schedule.strip()
                
                if new_cron_expr and new_cron_expr != cron_expr:
                    log_event("autosync.cron.schedule_changed", backend=backend_name, old=cron_expr, new=new_cron_expr)
                    return  # Exit to recalculate with new schedule
            
            # Check again if schedule is still the same before executing (federation-aware)
            final_cron_expr = None
            if is_federation_secondary:
                from .services.config_cache import get_config_cache
                cache = get_config_cache()
                backend_config = cache.get_backend_config()
                final_schedule = backend_config.get("auto_sync_schedule") if backend_config else None
                if final_schedule and " " in final_schedule:
                    final_cron_expr = final_schedule.strip()
            else:
                db_exec = SessionLocal()
                backend_exec = db_exec.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                db_exec.close()
                if backend_exec and backend_exec.auto_sync_schedule:
                    final_cron_expr = backend_exec.auto_sync_schedule.strip()
            
            if final_cron_expr == cron_expr and remaining <= 0:
                # Execute sync
                log_event("autosync.cron.trigger", backend=backend_name, cron=cron_expr)
                db = SessionLocal()
                try:
                    # Check backend type - federation secondary MUST use dynamic discovery
                    backend_type = os.getenv("BACKEND_TYPE", "primary")
                    is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
                    
                    if is_federation_secondary:
                        # Federation backends ALWAYS use dynamic discovery (fetch managed products from primary)
                        use_dynamic = True
                    else:
                        # Primary backend reads use_dynamic from database
                        use_dynamic = False
                        try:
                            backend_exec = db.query(BackendEndpoint).filter(BackendEndpoint.name == backend_name).one_or_none()
                            if backend_exec:
                                use_dynamic = backend_exec.use_dynamic_discovery
                        except Exception:
                            pass
                    refresh_from_sources(db, triggered_by="auto", use_dynamic=use_dynamic)
                finally:
                    db.close()
                log_event("autosync.cron.done", backend=backend_name)
        except Exception as e:
            log_event("autosync.cron.error", backend=backend_name, error=str(e))
            time.sleep(60)
    
    # Start the unified auto-sync thread
    # For federation secondary backends, add a small delay to allow self-registration to complete
    def _delayed_auto_sync_start():
        backend_type = os.getenv("BACKEND_TYPE", "primary")
        is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")
        
        if is_federation_secondary:
            backend_name = os.getenv("BACKEND_NAME", "default-backend")
            log_event("autosync.startup_delay", backend=backend_name,
                     reason="Waiting for federation self-registration and config fetch", delay_seconds=15)
            time.sleep(15)  # Wait for self-registration to complete
            
            # Proactively fetch federation config to populate config_cache
            log_event("autosync.startup.fetching_config", backend=backend_name)
            try:
                from .services.config_cache import get_config_cache
                from .services.federation import get_federation_client
                
                fed_client = get_federation_client()
                cache = get_config_cache()
                
                # Use a system/internal token for initial config fetch
                # Secondary backends use their auth_token to authenticate with primary
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Fetch config using federation client
                config = loop.run_until_complete(fed_client.fetch_shared_config(fed_client.registration_token))
                
                if config:
                    # Manually update cache
                    cache._cache = config
                    from datetime import datetime
                    cache._last_fetch = datetime.now()
                    backend_config = config.get("backend_config", {})
                    latest_version_config = config.get("latest_version_config", {})
                    log_event("autosync.startup.config_fetched", backend=backend_name,
                             auto_sync_schedule=backend_config.get("auto_sync_schedule"),
                             use_dynamic_discovery=backend_config.get("use_dynamic_discovery"),
                             latest_version_mode=backend_config.get("latest_version_mode"),
                             latest_version_global=latest_version_config.get("mode"))
                else:
                    log_event("autosync.startup.config_fetch_failed", backend=backend_name,
                             reason="Failed to fetch config from primary")
                
                loop.close()
            except Exception as e:
                log_event("autosync.startup.config_fetch_error", backend=backend_name, error=str(e))
        
        _auto_sync_loop()
    
    t = threading.Thread(target=_delayed_auto_sync_start, name="auto-sync-unified-loop", daemon=True)
    t.start()
    
    # ========== KUBERNETES WATCH API (real-time resource monitoring) ==========
    try:
        from .services.kube_watch import start_kube_watch
        started = start_kube_watch()
        if started:
            log_event("startup.kube_watch.enabled")
        else:
            log_event("startup.kube_watch.skipped",
                      hint="set ENABLE_KUBE_WATCH=true to activate")
    except Exception as e:
        log_event("startup.kube_watch.error", error=str(e))
    
    # Product list refresh loop (daily at 01:00 AM)
    # Only run on PRIMARY backend - secondary backends get product list from federation config
    backend_type = os.getenv("BACKEND_TYPE", "primary")
    
    if backend_type == "primary":
        def _product_list_refresh_loop():
            """Background thread to refresh product list daily at 01:00 AM"""
            from .services.product_service import refresh_product_list
            import croniter
            
            cron_expr = "0 1 * * *"  # Daily at 01:00 AM
            
            while True:
                try:
                    # Calculate next run time
                    now = datetime.now()
                    cron = croniter.croniter(cron_expr, now)
                    next_run = cron.get_next(datetime)
                    sleep_seconds = (next_run - datetime.now()).total_seconds()
                    
                    log_event("product_list.cron.scheduled", cron=cron_expr, next_run=str(next_run), sleep_seconds=int(sleep_seconds))
                    
                    # Sleep until next run
                    time.sleep(max(sleep_seconds, 1))
                    
                    # Execute refresh
                    log_event("product_list.cron.trigger", cron=cron_expr)
                    db = SessionLocal()
                    try:
                        result = refresh_product_list(db)
                        log_event("product_list.cron.done", result=result)
                    finally:
                        db.close()
                        
                except Exception as e:
                    log_event("product_list.cron.error", error=str(e))
                    time.sleep(3600)  # Sleep 1 hour on error
        
        # Start product list refresh thread (PRIMARY only)
        pt = threading.Thread(target=_product_list_refresh_loop, name="product-list-refresh-loop", daemon=True)
        pt.start()
        log_event("product_list.cron.enabled", backend_type="primary")
    else:
        log_event("product_list.cron.disabled", backend_type=backend_type, reason="secondary_uses_federation_config")
    
    # ========== SCHEDULED IMAGE UPDATE JOB EXECUTOR ==========
    # Background thread to automatically execute approved jobs when their scheduled time arrives
    def _scheduled_job_executor_loop():
        """Background thread to execute scheduled image update jobs"""
        from .models import ImageUpdateJob
        from .services.image_update import get_image_update_service
        
        check_interval = 30  # Check every 30 seconds
        log_event("scheduled_jobs.executor.started", check_interval=check_interval)
        
        while True:
            try:
                time.sleep(check_interval)
                
                db = SessionLocal()
                try:
                    now = datetime.now(timezone.utc)
                    
                    # Cleanup truly stuck "executing" jobs
                    # A job is stuck only if BOTH conditions are met:
                    #   1) started_at is older than the idle timeout
                    #   2) progress_updated_at is also older than the idle timeout
                    # This way, active jobs that are making progress are never killed.
                    from datetime import timedelta
                    from .models import ConfigKV
                    idle_minutes_row = db.query(ConfigKV).filter(ConfigKV.key == "stuck_job_timeout_minutes").first()
                    idle_minutes = int(idle_minutes_row.value) if idle_minutes_row else 60
                    idle_cutoff = now - timedelta(minutes=idle_minutes)
                    
                    executing_jobs = db.query(ImageUpdateJob).filter(
                        ImageUpdateJob.status == "executing",
                        ImageUpdateJob.started_at < idle_cutoff
                    ).all()
                    
                    stuck_jobs = []
                    for ej in executing_jobs:
                        last_activity = ej.progress_updated_at or ej.started_at
                        if last_activity and last_activity < idle_cutoff:
                            stuck_jobs.append(ej)
                        else:
                            log_event("scheduled_jobs.executor.job_still_active",
                                     job_id=ej.id,
                                     last_activity=last_activity.isoformat() if last_activity else None)
                    
                    for stuck_job in stuck_jobs:
                        log_event("scheduled_jobs.executor.stuck_job_cleanup",
                                 job_id=stuck_job.id,
                                 started_at=stuck_job.started_at.isoformat() if stuck_job.started_at else None,
                                 last_progress=stuck_job.progress_updated_at.isoformat() if stuck_job.progress_updated_at else None)
                        stuck_job.status = "failed"
                        stuck_job.finished_at = now
                        stuck_job.notes = (stuck_job.notes or "") + f"\n[Auto-failed: no progress for >{idle_minutes}min]"
                    
                    if stuck_jobs:
                        db.commit()
                        log_event("scheduled_jobs.executor.stuck_jobs_cleaned", count=len(stuck_jobs))
                    
                    # Find approved jobs with scheduled_at in the past
                    pending_jobs = db.query(ImageUpdateJob).filter(
                        ImageUpdateJob.status == "approved",
                        ImageUpdateJob.scheduled_at.isnot(None),
                        ImageUpdateJob.scheduled_at <= now
                    ).all()
                    
                    if pending_jobs:
                        log_event("scheduled_jobs.executor.found", count=len(pending_jobs))
                    
                    for job in pending_jobs:
                        try:
                            log_event("scheduled_jobs.executor.starting", 
                                     job_id=job.id, 
                                     product=job.product_name,
                                     scheduled_at=job.scheduled_at.isoformat() if job.scheduled_at else None)
                            
                            # Execute the job
                            service = get_image_update_service()
                            results = service.execute_job(db, job)
                            
                            log_event("scheduled_jobs.executor.completed",
                                     job_id=job.id,
                                     status=job.status,
                                     total=results.get("total", 0),
                                     success=results.get("success", 0),
                                     failed=results.get("failed", 0))
                                     
                        except Exception as e:
                            log_event("scheduled_jobs.executor.job_error", 
                                     job_id=job.id, 
                                     error=str(e))
                            # Mark job as failed
                            job.status = "failed"
                            job.finished_at = datetime.now(timezone.utc)
                            job.notes = (job.notes or "") + f"\n[Scheduler Error: {str(e)}]"
                            db.commit()
                            
                finally:
                    db.close()
                    
            except Exception as e:
                log_event("scheduled_jobs.executor.error", error=str(e))
                time.sleep(60)  # Sleep 1 minute on error
    
    # Start scheduled job executor thread
    sjt = threading.Thread(target=_scheduled_job_executor_loop, name="scheduled-job-executor", daemon=True)
    sjt.start()
    log_event("scheduled_jobs.executor.enabled")


app.include_router(health.router)
app.include_router(resources.router, prefix="/api", tags=["resources"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(image_update.router, prefix="/api/image-updates", tags=["image-updates"])
app.include_router(compare.router, prefix="/api", tags=["compare"])
