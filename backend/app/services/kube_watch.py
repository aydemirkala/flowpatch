"""
Kubernetes Watch API — real-time resource monitoring.

Watches deployments, statefulsets, daemonsets for ADDED/MODIFIED events.
When the *watched product's own container* changes, triggers a full single-resource
refresh (version + manifest + EOL + Trivy/Twistlock) via refresh_from_sources.

Risk mitigations:
  - Automatic reconnect with exponential backoff
  - Container-spec dedup: only refresh when the matched container's spec changes,
    so sibling/app-container churn and rollout status churn are ignored
  - Active image-update job guard: skip refresh while a patch job owns the product
  - Managed-product list refreshed every 5 min (picks up new products)
  - Cronjob keeps running as a safety net
  - All errors caught & logged — never crashes the main process

Toggle: ENABLE_KUBE_WATCH=true  (default: false)
"""

import os
import json
import time
import queue
import hashlib
import threading
from typing import Optional

from ..logging_utils import log_event
from ..db import SessionLocal
from .versioning import parse_image

_INITIAL_BACKOFF = 5
_MAX_BACKOFF = 300
_BACKOFF_MULTIPLIER = 2
_WATCH_TIMEOUT = 300
_MANAGED_REFRESH_INTERVAL = 300
_MANAGED_RETRY_INTERVAL = 20   # fast retry while patterns are empty (startup, esp. remote backends)
_STATE_MAX_KEYS = 10000

# Bounded, deduplicated refresh pipeline. Watch threads only enqueue (near-instant,
# so the K8s stream never lags); a small worker pool runs the heavy full refresh.
# This caps concurrency, coalesces bursts (mass simultaneous updates), and naturally
# rate-limits external scanners (registry/EOL/Trivy/Twistlock).
_WORKER_COUNT = int(os.getenv("KUBE_WATCH_WORKERS", "2"))
_QUEUE_MAXSIZE = 2000
_work_queue: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_pending: set[str] = set()          # resource keys queued or in-flight (dedup)
_pending_lock = threading.Lock()
_workers_started = False
_workers_lock = threading.Lock()

# Shared reference to the live managed-product patterns, set by start_kube_watch().
# Held in a one-element list so background threads and reload_managed_patterns()
# observe updates without re-binding the global.
_managed_ref: list = [{}]


class _StateTracker:
    """Thread-safe map of resource-key -> last-seen container-spec hash.

    A refresh is triggered only when the matched container's hash changes, so
    repeated events with an unchanged watched container (status churn, sibling
    container updates) are skipped.
    """

    def __init__(self):
        self._seen: dict[str, str] = {}
        self._lock = threading.Lock()

    def last(self, key: str) -> Optional[str]:
        with self._lock:
            return self._seen.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            if key not in self._seen and len(self._seen) >= _STATE_MAX_KEYS:
                # Bound memory: rare on real clusters. Clearing causes at most one
                # extra refresh per resource afterwards (next event re-seeds).
                log_event("kube_watch.state.cleared", size=len(self._seen))
                self._seen.clear()
            self._seen[key] = value


_state = _StateTracker()


def _load_managed_patterns() -> dict[str, str]:
    """Return {product_name: match_pattern} from DB or federation cache."""
    db = SessionLocal()
    try:
        from .dynamic_sources import _get_product_match_patterns
        return _get_product_match_patterns(db)
    finally:
        db.close()


def _match_product(containers: list, patterns: dict[str, list[str]]):
    """Longest-pattern-wins product detection.

    Returns (product_name, matched_container) for the single container whose
    image matches a managed pattern best, or (None, None) if nothing matches.
    """
    best_name = None
    best_len = 0
    best_container = None
    for c in containers:
        image = getattr(c, "image", None)
        if not image:
            continue
        parsed = parse_image(image)
        if not parsed:
            continue
        repo = parsed.repository.lower()
        for pname, pats in patterns.items():
            for pat in pats:
                if pat in repo and len(pat) > best_len:
                    best_name = pname
                    best_len = len(pat)
                    best_container = c
    return best_name, best_container


def _extract_containers(obj) -> list:
    """Pull container objects (incl. init containers) from a workload event."""
    out: list = []
    try:
        spec = obj.spec.template.spec
        out.extend(spec.containers or [])
        out.extend(spec.init_containers or [])
    except Exception:
        pass
    return out


def _container_hash(container) -> str:
    """Stable hash of a container's full spec (image + env + args + resources +
    volumeMounts + ...). Changes only when the container's own yaml changes."""
    try:
        from kubernetes import client
        data = client.ApiClient().sanitize_for_serialization(container)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except Exception:
        # Fallback: at least track the image so version changes are caught.
        img = getattr(container, "image", "") or ""
        return hashlib.sha256(img.encode("utf-8")).hexdigest()


def _has_active_job(db, product_name: str) -> bool:
    """True if an image-update job for this product is currently executing.

    While a patch job owns the product it updates the Resource row itself, so the
    watch must not refresh concurrently (avoids races and history noise)."""
    try:
        from ..models import ImageUpdateJob
        return db.query(ImageUpdateJob).filter(
            ImageUpdateJob.status == "executing",
            ImageUpdateJob.product_name == product_name,
        ).first() is not None
    except Exception as e:
        log_event("kube_watch.active_job.check_error", product=product_name, error=str(e))
        return False


def _enqueue(dkey: str, ns: str, name: str, kind: str, product: str, chash: str) -> None:
    """Queue a single-resource refresh, collapsing duplicates.

    If the resource is already queued or in-flight, skip — the pending task reads
    live K8s state and will reflect the latest spec anyway."""
    with _pending_lock:
        if dkey in _pending:
            return
        _pending.add(dkey)
    try:
        _work_queue.put_nowait((dkey, ns, name, kind, product, chash))
    except queue.Full:
        # Queue saturated (very large burst). Drop now and let the next event or the
        # nightly cron pick it up; reset the hash so a future event re-enqueues.
        with _pending_lock:
            _pending.discard(dkey)
        _state.set(dkey, "")
        log_event("kube_watch.queue.full", resource=dkey, product=product)


def _process_refresh(ns: str, name: str, kind: str, product: str, chash: str) -> None:
    """Run a full single-resource refresh (version + manifest + EOL + Trivy/Twistlock).

    Does NOT create a SyncLog (log_sync=False) — watch refreshes would otherwise flood
    the Sync Logs page; the change is recorded in resource_history (source=watch).
    On success the triggering container-spec hash is persisted so a backend restart
    doesn't re-refresh unchanged resources (see _seed_state_from_db)."""
    platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    db = SessionLocal()
    try:
        # Skip while an image-update job owns this product — the job updates the
        # Resource row itself; refreshing now would race.
        if _has_active_job(db, product):
            log_event("kube_watch.skip.active_job",
                      product=product, namespace=ns, resource=name)
            return

        from .refresh import refresh_from_sources
        from .sources_reader import SourceItem
        item = SourceItem(
            platform=platform,
            namespace=ns,
            resource_name=name,
            kind=kind,
            product_name=product,
        )
        result = refresh_from_sources(
            db, triggered_by="watch",
            target_items=[item], skip_cleanup=True, log_sync=False,
        )
        log_event("kube_watch.refresh.done", product=product, resource=name, **result)

        # Persist the spec hash so restarts skip this (unchanged) resource on the
        # initial ADDED replay (the in-memory state is re-seeded from this column).
        try:
            from ..models import Resource
            db.query(Resource).filter(
                Resource.platform == platform,
                Resource.namespace == ns,
                Resource.resource_name == name,
                Resource.kind == kind,
            ).update({Resource.watch_spec_hash: chash}, synchronize_session=False)
            db.commit()
        except Exception as e:
            log_event("kube_watch.hash_persist.error", resource=name, error=str(e))
            db.rollback()
    finally:
        db.close()


def _refresh_worker(worker_id: int) -> None:
    """Consume the refresh queue forever."""
    log_event("kube_watch.worker.start", worker=worker_id)
    while True:
        dkey, ns, name, kind, product, chash = _work_queue.get()
        try:
            _process_refresh(ns, name, kind, product, chash)
        except Exception as e:
            log_event("kube_watch.worker.error", resource=dkey, product=product, error=str(e))
        finally:
            with _pending_lock:
                _pending.discard(dkey)
            _work_queue.task_done()


def _start_workers() -> None:
    """Start the refresh worker pool once."""
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        for i in range(max(1, _WORKER_COUNT)):
            threading.Thread(
                target=_refresh_worker, args=(i,),
                name=f"kube-watch-refresh-{i}", daemon=True,
            ).start()
        _workers_started = True
    log_event("kube_watch.workers.started", count=max(1, _WORKER_COUNT))


def _watch_loop(kind: str, api_method, managed_ref: list):
    """Watch one resource kind forever, reconnecting on failures."""
    from kubernetes import watch as kw

    backoff = _INITIAL_BACKOFF
    log_event("kube_watch.stream.start", kind=kind)

    while True:
        w = kw.Watch()
        try:
            for event in w.stream(api_method, timeout_seconds=_WATCH_TIMEOUT):
                try:
                    etype = event.get("type", "")
                    obj = event.get("object")
                    if obj is None or etype == "DELETED":
                        continue

                    name = obj.metadata.name
                    ns = obj.metadata.namespace or "unknown"
                    dkey = f"{kind}/{ns}/{name}"

                    containers = _extract_containers(obj)
                    if not containers:
                        continue

                    product, matched = _match_product(containers, managed_ref[0])
                    if not product or matched is None:
                        continue

                    # Dedup: only act when the *watched* container's spec changed.
                    # Sibling/app container churn and rollout status churn leave this
                    # hash unchanged and are skipped.
                    chash = _container_hash(matched)
                    if _state.last(dkey) == chash:
                        continue
                    _state.set(dkey, chash)

                    log_event("kube_watch.event", kind=kind, event_type=etype,
                              namespace=ns, resource=name, product=product)

                    # Hand off to the worker pool — keep this stream responsive so a
                    # mass burst never makes the watch lag (heavy refresh runs async).
                    _enqueue(dkey, ns, name, kind, product, chash)

                except Exception as e:
                    log_event("kube_watch.event.error", kind=kind, error=str(e))

            # Stream ended normally (timeout) — reset backoff
            backoff = _INITIAL_BACKOFF

        except Exception as e:
            log_event("kube_watch.stream.disconnected", kind=kind,
                      error=str(e), backoff_s=backoff)
        finally:
            try:
                w.stop()
            except Exception:
                pass

        time.sleep(backoff)
        backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)


def _managed_refresh_loop(ref: list):
    """Periodically reload managed product patterns so new additions are picked up.

    While patterns are empty (e.g. a remote backend whose federation config cache
    isn't ready yet at startup) retry fast so real-time coverage begins in ~20s
    instead of waiting a full 5-minute cycle; settle to the normal interval once
    patterns are loaded."""
    while True:
        try:
            interval = _MANAGED_REFRESH_INTERVAL if ref[0] else _MANAGED_RETRY_INTERVAL
            time.sleep(interval)
            ref[0] = _load_managed_patterns()
            log_event("kube_watch.managed.refreshed", count=len(ref[0]))
        except Exception as e:
            log_event("kube_watch.managed.error", error=str(e))


def _seed_state_from_db() -> int:
    """Seed the in-memory dedup state from persisted watch_spec_hash values.

    On restart this makes the watch's initial ADDED replay match the stored hashes,
    so unchanged resources are NOT re-refreshed (no startup refresh burst). Resources
    that changed while the backend was down have a different hash → still refreshed."""
    seeded = 0
    db = SessionLocal()
    try:
        from ..models import Resource
        rows = db.query(
            Resource.kind, Resource.namespace, Resource.resource_name, Resource.watch_spec_hash
        ).filter(Resource.watch_spec_hash.isnot(None)).all()
        for kind, ns, name, h in rows:
            if not h:
                continue
            dkey = f"{(kind or '').lower()}/{ns or 'unknown'}/{name}"
            _state.set(dkey, h)
            seeded += 1
    except Exception as e:
        log_event("kube_watch.state.seed_error", error=str(e))
    finally:
        db.close()
    log_event("kube_watch.state.seeded", count=seeded)
    return seeded


def reload_managed_patterns() -> None:
    """Reload managed-product patterns immediately (in-process).

    Called by the managed-product add/update/remove endpoints so this backend's
    watch picks up a newly managed product without waiting for the 5-minute loop.
    Safe to call even when the watch is disabled (no-op effect)."""
    try:
        _managed_ref[0] = _load_managed_patterns()
        log_event("kube_watch.managed.reloaded", count=len(_managed_ref[0]))
    except Exception as e:
        log_event("kube_watch.managed.reload_error", error=str(e))


def start_kube_watch() -> bool:
    """
    Entry point — call from on_startup.
    Returns True if watch threads were started, False otherwise.
    """
    enabled = os.getenv("ENABLE_KUBE_WATCH", "false").lower() == "true"
    if not enabled:
        log_event("kube_watch.disabled")
        return False

    try:
        from kubernetes import client, config as kconfig
    except ImportError:
        log_event("kube_watch.error", error="kubernetes module not installed")
        return False

    from ..config import settings
    try:
        if settings.kube_incluster:
            kconfig.load_incluster_config()
        elif settings.kubecfg_path:
            kconfig.load_kube_config(config_file=settings.kubecfg_path)
        else:
            kconfig.load_kube_config()
    except Exception as e:
        log_event("kube_watch.init.error", error=str(e))
        return False

    apps = client.AppsV1Api()

    # Namespace-scoped vs cluster-scoped watch
    watch_ns = os.getenv("KUBE_WATCH_NAMESPACE", "").strip()

    if watch_ns:
        targets = [
            ("deployment", lambda **kw: apps.list_namespaced_deployment(watch_ns, **kw)),
            ("statefulset", lambda **kw: apps.list_namespaced_stateful_set(watch_ns, **kw)),
            ("daemonset", lambda **kw: apps.list_namespaced_daemon_set(watch_ns, **kw)),
        ]
    else:
        targets = [
            ("deployment", apps.list_deployment_for_all_namespaces),
            ("statefulset", apps.list_stateful_set_for_all_namespaces),
            ("daemonset", apps.list_daemon_set_for_all_namespaces),
        ]

    try:
        initial = _load_managed_patterns()
    except Exception as e:
        log_event("kube_watch.managed.init_error", error=str(e))
        initial = {}

    # Use the module-level ref so reload_managed_patterns() updates the same dict
    # the watch threads read.
    _managed_ref[0] = initial

    # Seed dedup state from persisted hashes so the initial ADDED replay doesn't
    # re-refresh unchanged resources after a restart.
    _seed_state_from_db()

    # Start the refresh worker pool before the watch threads begin enqueuing.
    _start_workers()

    for kind, method in targets:
        threading.Thread(
            target=_watch_loop, args=(kind, method, _managed_ref),
            name=f"kube-watch-{kind}", daemon=True,
        ).start()

    threading.Thread(
        target=_managed_refresh_loop, args=(_managed_ref,),
        name="kube-watch-managed-refresh", daemon=True,
    ).start()

    log_event("kube_watch.started",
              kinds=["deployment", "statefulset", "daemonset"],
              managed_products=len(initial),
              namespace=watch_ns or "all")
    return True
