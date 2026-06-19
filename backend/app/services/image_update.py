"""
Image Update Service for patching Kubernetes resources with new images.
Handles the actual Kubernetes API calls to update deployments, statefulsets, etc.
Supports multi-backend federation for updating resources across multiple clusters.
"""
from __future__ import annotations

import json
import os
import ssl
import types
import aiohttp
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Resource, ResourceHistory, ImageUpdateJob, ImageUpdateResult, BackendEndpoint
from ..logging_utils import log_event


def _proxy_timeout_seconds(default: int = 60) -> int:
    """The admin-configurable federation request timeout (Settings → Proxy Timeout,
    ConfigKV `proxy_timeout_seconds`). Used so the Update Image patch-forward honours the
    same tunable timeout as the streaming proxy instead of a hardcoded value."""
    try:
        from ..db import SessionLocal
        from ..models import ConfigKV
        db = SessionLocal()
        try:
            row = db.query(ConfigKV).filter(ConfigKV.key == "proxy_timeout_seconds").first()
            if row and row.value:
                return max(10, int(row.value))
        finally:
            db.close()
    except Exception:
        pass
    return default


async def _fetch_products_from_backend(
    backend: Dict,
    user_token: str,
) -> List[Dict]:
    """Fetch products from a single remote backend.
    
    Uses the backend's auth_token for inter-backend communication,
    falling back to user_token if not available.
    """
    try:
        ssl_context = None
        if backend.get("skip_tls_verify"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
        
        # Use backend's auth_token for inter-backend communication
        # This ensures the remote backend accepts the request
        auth_token = backend.get("auth_token") or user_token
        
        async with aiohttp.ClientSession(connector=connector) as session:
            url = f"{backend['api_url'].rstrip('/')}/api/image-updates/products"
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
            }
            
            log_event(
                "image_update.products.fetching_remote",
                backend=backend["name"],
                url=url,
                using_backend_token=bool(backend.get("auth_token")),
            )
            
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log_event(
                        "image_update.products.fetched_remote",
                        backend=backend["name"],
                        products_count=len(data),
                    )
                    return data
                else:
                    body = await resp.text()
                    log_event(
                        "image_update.products.remote_error",
                        backend=backend["name"],
                        status=resp.status,
                        response=body[:200] if body else None,
                    )
                    return []
    except asyncio.TimeoutError:
        log_event(
            "image_update.products.remote_timeout",
            backend=backend["name"],
        )
        return []
    except Exception as e:
        log_event(
            "image_update.products.remote_error",
            backend=backend["name"],
            error=str(e),
        )
        return []


def _merge_products(all_products: List[List[Dict]]) -> List[Dict]:
    """Merge products from multiple backends into a single list.
    
    Combines platforms, namespaces, resources, and images for products
    with the same product_name.
    """
    merged: Dict[str, Dict] = {}
    
    for products_list in all_products:
        for product in products_list:
            name = product.get("product_name", "")
            if not name:
                continue
            
            if name not in merged:
                merged[name] = {
                    "product_name": name,
                    "platforms": set(),
                    "namespaces": set(),
                    "resources": [],
                    "current_images": set(),
                    "latest_images": set(),
                }
            
            # Merge sets
            merged[name]["platforms"].update(product.get("platforms", []))
            merged[name]["namespaces"].update(product.get("namespaces", []))
            merged[name]["current_images"].update(product.get("current_images", []))
            merged[name]["latest_images"].update(product.get("latest_images", []))
            
            # Merge resources (avoid duplicates by id + container_name)
            # A resource can have multiple containers, each should be a separate entry
            existing_keys = {
                f"{r.get('id')}|{r.get('container_name', '')}" 
                for r in merged[name]["resources"]
            }
            for res in product.get("resources", []):
                unique_key = f"{res.get('id')}|{res.get('container_name', '')}"
                if unique_key not in existing_keys:
                    merged[name]["resources"].append(res)
                    existing_keys.add(unique_key)
    
    # Convert sets to sorted lists
    result = []
    for name, data in merged.items():
        data["platforms"] = sorted(list(data["platforms"]))
        data["namespaces"] = sorted(list(data["namespaces"]))
        data["current_images"] = sorted(list(data["current_images"]))
        data["latest_images"] = sorted(list(data["latest_images"]))
        result.append(data)
    
    return sorted(result, key=lambda x: x["product_name"])


def _image_matches_product(image: str, product: str) -> bool:
    """Check if an image repository path contains the product name."""
    if not image or not product:
        return False
    img_lower = image.lower()
    product_lower = product.lower()
    # Extract repository path (without registry and tag)
    parts = img_lower.split('/')
    last_part = parts[-1].split(':')[0]
    repo_path = '/'.join(parts[1:]).split(':')[0] if len(parts) > 1 else last_part
    # Check if product name appears in repository path
    return product_lower in repo_path or product_lower in last_part


class ImageUpdateService:
    """Service for executing image updates on Kubernetes resources."""
    
    def __init__(self):
        self.kube = None
        self._initialized = False
    
    def _init_kube(self):
        """Lazy initialization of Kubernetes client."""
        if self._initialized:
            return
        
        try:
            from kubernetes import client, config
            from kubernetes.client import ApiException
            
            try:
                config.load_incluster_config()
                log_event("image_update.kube.init", mode="incluster")
            except Exception:
                try:
                    config.load_kube_config()
                    log_event("image_update.kube.init", mode="kubeconfig")
                except Exception as e:
                    log_event("image_update.kube.init.error", error=str(e))
                    return
            
            self.apps_api = client.AppsV1Api()
            self.batch_api = client.BatchV1Api()
            self._initialized = True
            log_event("image_update.kube.ready")
            
        except ImportError:
            log_event("image_update.kube.no_module")
    
    def is_available(self) -> bool:
        """Check if Kubernetes client is available."""
        self._init_kube()
        return self._initialized
    
    def get_resource_state(
        self,
        namespace: str,
        name: str,
        kind: str,
    ) -> Dict:
        """Get current state of a resource (replicas, ready pods, etc.)."""
        if not self.is_available():
            return {"error": "Kubernetes client not available"}
        
        from kubernetes import client
        from kubernetes.client import ApiException
        
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "replicas": None,
            "ready_replicas": None,
            "available_replicas": None,
            "updated_replicas": None,
            "pods": [],
            "error": None,
        }
        
        kind_lower = kind.strip().lower()
        
        try:
            if kind_lower == "deployment":
                dep = self.apps_api.read_namespaced_deployment(name, namespace)
                state["replicas"] = dep.spec.replicas
                state["ready_replicas"] = dep.status.ready_replicas or 0
                state["available_replicas"] = dep.status.available_replicas or 0
                state["updated_replicas"] = dep.status.updated_replicas or 0
                # status.replicas = total non-terminated pods incl. surge/old (NOT spec.replicas);
                # generation/observed_generation let us tell a fresh rollout from a stale status.
                state["status_replicas"] = dep.status.replicas or 0
                state["observed_generation"] = dep.status.observed_generation
                state["generation"] = dep.metadata.generation
                
                # Get selector for pods
                selector = dep.spec.selector.match_labels
                
            elif kind_lower == "statefulset":
                ss = self.apps_api.read_namespaced_stateful_set(name, namespace)
                state["replicas"] = ss.spec.replicas
                state["ready_replicas"] = ss.status.ready_replicas or 0
                state["current_replicas"] = ss.status.current_replicas or 0
                state["updated_replicas"] = ss.status.updated_replicas or 0
                
                selector = ss.spec.selector.match_labels
                
            elif kind_lower == "daemonset":
                ds = self.apps_api.read_namespaced_daemon_set(name, namespace)
                state["desired_number_scheduled"] = ds.status.desired_number_scheduled or 0
                state["current_number_scheduled"] = ds.status.current_number_scheduled or 0
                state["number_ready"] = ds.status.number_ready or 0
                state["updated_number_scheduled"] = ds.status.updated_number_scheduled or 0
                
                selector = ds.spec.selector.match_labels
                
            elif kind_lower == "cronjob":
                # CronJobs don't have running pods to check
                cj = self.batch_api.read_namespaced_cron_job(name, namespace)
                state["active_jobs"] = len(cj.status.active or [])
                state["last_schedule_time"] = cj.status.last_schedule_time.isoformat() if cj.status.last_schedule_time else None
                return state
            else:
                state["error"] = f"Unsupported kind: {kind}"
                return state
            
            # Get pod statuses for deployment/statefulset/daemonset
            core_api = client.CoreV1Api()
            label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
            pods = core_api.list_namespaced_pod(namespace, label_selector=label_selector)
            
            for pod in pods.items:
                pod_info = {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "ready": False,
                    "restart_count": 0,
                    # Terminating pods (deletionTimestamp set) still list with phase=Running during
                    # graceful shutdown; exclude them so a rollout's surge isn't counted as ready.
                    "terminating": pod.metadata.deletion_timestamp is not None,
                    "container_statuses": [],
                }
                
                if pod.status.conditions:
                    for cond in pod.status.conditions:
                        if cond.type == "Ready":
                            pod_info["ready"] = cond.status == "True"
                            break
                
                if pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        container_status = {
                            "name": cs.name,
                            "ready": cs.ready,
                            "restart_count": cs.restart_count,
                            "state": None,
                            "reason": None,
                            "message": None,
                        }
                        
                        if cs.state.running:
                            container_status["state"] = "running"
                        elif cs.state.waiting:
                            container_status["state"] = "waiting"
                            container_status["reason"] = cs.state.waiting.reason
                            container_status["message"] = cs.state.waiting.message
                        elif cs.state.terminated:
                            container_status["state"] = "terminated"
                            container_status["reason"] = cs.state.terminated.reason
                            container_status["exit_code"] = cs.state.terminated.exit_code
                        
                        pod_info["restart_count"] += cs.restart_count
                        pod_info["container_statuses"].append(container_status)
                
                state["pods"].append(pod_info)
            
        except ApiException as e:
            state["error"] = f"API error: {e.status} - {e.reason}"
        except Exception as e:
            state["error"] = str(e)
        
        return state

    def wait_for_rollout_and_health_check(
        self,
        namespace: str,
        name: str,
        kind: str,
        pre_patch_state: Optional[Dict] = None,
        timeout_seconds: int = 300,
        crash_tolerance: int = 120,
        poll_interval: int = 10,
    ) -> Tuple[bool, Dict, str]:
        """Monitor a single resource rollout with smart tolerances.

        Used by federation /health-check endpoint and remote _execute_for_product_worker.
        Tolerates transient CrashLoopBackOff/ImagePullBackOff for crash_tolerance seconds.
        Returns (health_passed, post_state, message).
        """
        import time

        if not self.is_available():
            return False, {}, "Kubernetes client not available"

        kind_lower = kind.strip().lower()
        if kind_lower == "cronjob":
            post_state = self.get_resource_state(namespace, name, kind)
            return True, post_state, "CronJob updated (no rollout to verify)"

        log_event("health_check.start", namespace=namespace, resource=name,
                  kind=kind, timeout=timeout_seconds, crash_tolerance=crash_tolerance)

        start_time = time.time()
        last_state: Dict = {}
        first_error_since: Optional[float] = None
        first_not_ready_since: float = start_time
        last_msg = ""

        while time.time() - start_time < timeout_seconds:
            time.sleep(poll_interval)
            current_state = self.get_resource_state(namespace, name, kind)
            last_state = current_state

            if current_state.get("error"):
                last_msg = f"State error: {current_state['error']}"
                continue

            health = self._analyze_pod_health(current_state, pre_patch_state)
            rollout_ok = self._check_rollout_complete(kind_lower, current_state)
            now = time.time()

            if health["crash_detected"]:
                if first_error_since is None:
                    first_error_since = now
                    log_event("health_check.crash_detected", namespace=namespace,
                              resource=name, error_pods=health["error_pods"][:3])
                if now - first_error_since >= crash_tolerance:
                    msg = f"Crash tolerance ({crash_tolerance}s) exceeded: {health['error_pods'][:3]}"
                    log_event("health_check.crash_rollback", namespace=namespace,
                              resource=name, reason=msg)
                    return False, current_state, msg
                last_msg = f"Crash detected, tolerating ({int(now - first_error_since)}s/{crash_tolerance}s)"
                continue

            if first_error_since is not None:
                log_event("health_check.error_resolved", namespace=namespace,
                          resource=name, elapsed=int(now - first_error_since))
                first_error_since = None

            if rollout_ok and health["all_containers_ready"] and not health["pods_still_starting"]:
                elapsed = int(now - start_time)
                msg = f"Rollout complete after {elapsed}s. {health['running_pods']}/{health['total_active_pods']} pods ready"
                log_event("health_check.passed", namespace=namespace, resource=name,
                          elapsed=elapsed, pods_running=health["running_pods"],
                          pods_total=health["total_active_pods"])
                return True, current_state, msg

            if health["all_containers_ready"] or health["pods_still_starting"]:
                first_not_ready_since = now

            if now - first_not_ready_since >= timeout_seconds:
                not_ready = health["not_ready_containers"][:5]
                msg = f"Stuck: pods not ready for {timeout_seconds}s. {not_ready}"
                log_event("health_check.stuck", namespace=namespace, resource=name, reason=msg)
                return False, current_state, msg

            last_msg = f"Waiting: {health['running_pods']}/{health['total_active_pods']} pods"

        msg = f"Timeout after {timeout_seconds}s. {last_msg}"
        log_event("health_check.timeout", namespace=namespace, resource=name, timeout=timeout_seconds)
        return False, last_state, msg

    def _check_rollout_complete(self, kind_lower: str, state: Dict) -> bool:
        """Check if resource rollout is complete based on replica counts."""
        if kind_lower == "deployment":
            desired = state.get("replicas", 0) or 0
            ready = state.get("ready_replicas", 0)
            updated = state.get("updated_replicas", 0)
            available = state.get("available_replicas", 0)
            current = state.get("status_replicas", 0)   # total pods incl. surge/old/terminating
            gen = state.get("generation")
            observed = state.get("observed_generation")
            # The controller must have observed the new spec — else status is stale (prev rollout).
            if gen is not None and observed is not None and observed < gen:
                return False
            # kubectl-style "rollout complete": all pods updated, NO old/surge/terminating pods
            # remain (current == updated), and the desired count is ready+available.
            return (updated >= desired and current == updated
                    and ready >= desired and available >= desired)
        elif kind_lower == "statefulset":
            desired = state.get("replicas", 0)
            ready = state.get("ready_replicas", 0)
            updated = state.get("updated_replicas", 0)
            return (updated >= desired and ready >= desired and updated == ready)
        elif kind_lower == "daemonset":
            desired = state.get("desired_number_scheduled", 0)
            ready = state.get("number_ready", 0)
            updated = state.get("updated_number_scheduled", 0)
            return (updated >= desired and ready >= desired and updated == ready)
        return False

    def _analyze_pod_health(self, state: Dict, pre_patch_state: Optional[Dict] = None) -> Dict:
        """Analyze pod health from resource state. Returns analysis dict."""
        pods = state.get("pods", [])
        # Exclude terminating pods (rollout surge / old pods being removed) so they aren't
        # counted as ready — they still list with phase=Running during graceful shutdown.
        active_pods = [p for p in pods if p.get("phase") not in ["Succeeded", "Failed", "Unknown"]
                       and not p.get("terminating")]

        result = {
            "crash_detected": False,
            "error_pods": [],
            "pods_still_starting": False,
            "all_containers_ready": True,
            "not_ready_containers": [],
            "running_pods": sum(1 for p in active_pods if p.get("phase") == "Running"),
            "total_active_pods": len(active_pods),
        }

        for pod in pods:
            pod_phase = pod.get("phase", "")
            pod_name = pod.get("name", "unknown")
            if pod_phase in ["Succeeded", "Failed", "Unknown"] or pod.get("terminating"):
                continue

            if pod_phase == "Pending":
                result["pods_still_starting"] = True

            for cs in pod.get("container_statuses", []):
                cs_name = cs.get("name", "unknown")
                cs_ready = cs.get("ready", False)
                cs_state = cs.get("state")

                if cs_state == "waiting" and cs.get("reason") in ["ContainerCreating", "PodInitializing"]:
                    result["pods_still_starting"] = True
                elif cs_state != "running":
                    result["all_containers_ready"] = False
                    result["not_ready_containers"].append(f"{pod_name}/{cs_name}: state={cs_state}")
                elif not cs_ready:
                    result["all_containers_ready"] = False
                    result["not_ready_containers"].append(f"{pod_name}/{cs_name}: running but not ready")

                if cs_state == "waiting" and cs.get("reason") in [
                    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                    "CreateContainerError", "CreateContainerConfigError"
                ]:
                    result["crash_detected"] = True
                    result["error_pods"].append({
                        "pod": pod_name, "container": cs_name,
                        "reason": cs.get("reason"), "message": cs.get("message", "")[:200],
                    })

                pre_pods = pre_patch_state.get("pods", []) if pre_patch_state else []
                pre_restart = 0
                for pre_pod in pre_pods:
                    for pre_cs in pre_pod.get("container_statuses", []):
                        if pre_cs.get("name") == cs_name:
                            pre_restart = pre_cs.get("restart_count", 0)
                            break
                if pre_patch_state and cs.get("restart_count", 0) - pre_restart >= 3:
                    result["crash_detected"] = True
                    result["error_pods"].append({
                        "pod": pod_name, "container": cs_name,
                        "reason": "ExcessiveRestarts",
                        "message": f"Restarts: {pre_restart} -> {cs.get('restart_count')}",
                    })

        return result

    def verify_rollout(self, namespace: str, name: str, kind: str,
                       pre_state: Optional[Dict] = None, timeout: int = 300,
                       poll_interval: int = 10) -> Tuple[bool, str]:
        """Poll until a rollout completes (or times out). Observe-and-report ONLY — never
        rolls back. Used to verify a MANUAL rollback's own rollout so the job isn't marked
        done while the cluster is still rolling out. Returns (ok, message)."""
        import time
        kind_lower = kind.strip().lower()
        start = time.time()
        last = "rollout not yet ready"
        while True:
            state = self.get_resource_state(namespace, name, kind)
            if state.get("error"):
                last = f"state error: {state['error']}"
            else:
                health = self._analyze_pod_health(state, pre_state)
                if (self._check_rollout_complete(kind_lower, state)
                        and health["all_containers_ready"] and not health["pods_still_starting"]):
                    elapsed = int(time.time() - start)
                    return True, (f"Rollback rollout complete after {elapsed}s. "
                                  f"{health['running_pods']}/{health['total_active_pods']} pods ready")
                last = f"not ready: {health.get('not_ready_containers', [])[:3]}"
            if time.time() - start >= timeout:
                return False, f"Rollback rollout still in progress after {timeout}s ({last})"
            time.sleep(poll_interval)

    def smart_watch_monitor(
        self,
        db: Session,
        job: "ImageUpdateJob",
        watched_resources: List[Dict],
        results: Dict,
        stuck_detection: int = 300,
        crash_tolerance: int = 120,
        poll_interval: int = 10,
    ) -> bool:
        """Smart Watch: monitor all patched resources concurrently with tolerances.
        Returns True if an ImagePullBackOff/ErrImagePull failure was detected (job should abort).

        Each entry in watched_resources:
            resource, pre_patch_state, patched_containers, execution_log,
            backend_name, is_local, backend
        """
        import time

        if not watched_resources:
            return

        # Prevent session from expiring ORM objects after each commit.
        # Resource attributes (namespace, name, kind) are read-only during monitoring;
        # avoiding lazy reloads eliminates spurious "concurrent operations" errors.
        original_expire = db.expire_on_commit
        db.expire_on_commit = False

        total = len(watched_resources)
        image_pull_failure = False
        log_event("smart_watch.start", job_id=job.id, resources=total,
                  stuck_detection=stuck_detection, crash_tolerance=crash_tolerance)

        trackers: List[Dict] = []
        for wr in watched_resources:
            r = wr["resource"]
            kind_lower = r.kind.strip().lower()
            trackers.append({
                **wr,
                "kind_lower": kind_lower,
                "status": "monitoring",       # monitoring | ready | rolled_back
                "patch_time": time.time(),
                "first_error_since": None,
                "first_not_ready_since": time.time(),
                "last_error_detail": None,
            })

        self._update_progress(db, job, 0, total, f"Smart Watch: monitoring {total} resources...")

        try:
            while True:
                if self._check_cancel_requested(db, job):
                    log_event("smart_watch.cancelled", job_id=job.id)
                    break

                active = [t for t in trackers if t["status"] == "monitoring"]
                if not active:
                    break

                ready_count = sum(1 for t in trackers if t["status"] == "ready")
                rb_count = sum(1 for t in trackers if t["status"] == "rolled_back")
                self._update_progress(
                    db, job, ready_count + rb_count, total,
                    f"Smart Watch: {ready_count} ready, {rb_count} rolled back, {len(active)} monitoring"
                )

                for t in active:
                    r = t["resource"]
                    exec_log = t["execution_log"]

                    if t["kind_lower"] == "cronjob":
                        t["status"] = "ready"
                        exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                         "level": "info", "message": "CronJob - no rollout to verify"})
                        self._update_smart_watch_result(db, job, r, True, "CronJob updated", exec_log)
                        continue

                    if not t["is_local"]:
                        health_passed, post_state, health_msg = self._remote_smart_watch_check(
                            t, stuck_detection, crash_tolerance)
                        if health_passed is True:
                            t["status"] = "ready"
                            exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                             "level": "info", "message": f"Remote ready: {health_msg}"})
                            self._update_smart_watch_result(db, job, r, True, health_msg, exec_log, post_state)
                        elif health_passed is False:
                            exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                             "level": "error", "message": f"Remote failed: {health_msg}"})
                            self._do_rollback(db, job, t, results, health_msg)
                        continue

                    state = self.get_resource_state(r.namespace, r.resource_name, r.kind)
                    if state.get("error"):
                        exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                         "level": "warning", "message": f"State error: {state['error']}"})
                        continue

                    health = self._analyze_pod_health(state, t["pre_patch_state"])
                    rollout_ok = self._check_rollout_complete(t["kind_lower"], state)
                    now = time.time()

                    if health["crash_detected"]:
                        if t["first_error_since"] is None:
                            t["first_error_since"] = now
                            t["last_error_detail"] = str(health["error_pods"][:3])
                            exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                             "level": "warning",
                                             "message": f"Crash detected, tolerance {crash_tolerance}s: {t['last_error_detail']}"})
                            log_event("smart_watch.crash_detected", job_id=job.id,
                                      resource=r.resource_name, error_pods=health["error_pods"][:3])
                        elif now - t["first_error_since"] >= crash_tolerance:
                            reason = f"Crash tolerance ({crash_tolerance}s) exceeded: {t['last_error_detail']}"
                            exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                             "level": "error", "message": reason})
                            log_event("smart_watch.crash_rollback", job_id=job.id,
                                      resource=r.resource_name, reason=reason)
                            self._do_rollback(db, job, t, results, reason)
                            if any(ep.get("reason") in ("ImagePullBackOff", "ErrImagePull")
                                   for ep in health.get("error_pods", [])):
                                image_pull_failure = True
                                log_event("smart_watch.image_pull_failure", job_id=job.id,
                                          resource=r.resource_name)
                        continue

                    if t["first_error_since"] is not None:
                        elapsed = int(now - t["first_error_since"])
                        exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                         "level": "info", "message": f"Error resolved after {elapsed}s"})
                        log_event("smart_watch.error_resolved", job_id=job.id,
                                  resource=r.resource_name, elapsed=elapsed)
                        t["first_error_since"] = None
                        t["last_error_detail"] = None

                    if rollout_ok and health["all_containers_ready"] and not health["pods_still_starting"]:
                        elapsed = int(now - t["patch_time"])
                        msg = f"Rollout complete after {elapsed}s. {health['running_pods']}/{health['total_active_pods']} pods ready"
                        t["status"] = "ready"
                        exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                         "level": "info", "message": msg})
                        log_event("smart_watch.ready", job_id=job.id,
                                  resource=r.resource_name, elapsed=elapsed)
                        self._update_smart_watch_result(db, job, r, True, msg, exec_log, state)
                        continue

                    if health["all_containers_ready"] or health["pods_still_starting"]:
                        t["first_not_ready_since"] = now

                    if now - t["first_not_ready_since"] >= stuck_detection:
                        not_ready_info = health["not_ready_containers"][:5]
                        reason = f"Stuck detection ({stuck_detection}s) exceeded. Not ready: {not_ready_info}"
                        exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                         "level": "error", "message": reason})
                        log_event("smart_watch.stuck_rollback", job_id=job.id,
                                  resource=r.resource_name, reason=reason)
                        self._do_rollback(db, job, t, results, reason)

                db.commit()

                active_after = [t for t in trackers if t["status"] == "monitoring"]
                if not active_after:
                    break
                time.sleep(poll_interval)
        finally:
            db.expire_on_commit = original_expire

        ready_count = sum(1 for t in trackers if t["status"] == "ready")
        rb_count = sum(1 for t in trackers if t["status"] == "rolled_back")
        self._update_progress(
            db, job, total, total,
            f"Smart Watch complete: {ready_count} ready, {rb_count} rolled back"
        )
        log_event("smart_watch.complete", job_id=job.id, ready=ready_count, rolled_back=rb_count,
                  image_pull_failure=image_pull_failure)
        return image_pull_failure

    def _remote_smart_watch_check(
        self, tracker: Dict, stuck_detection: int, crash_tolerance: int
    ) -> Tuple[Optional[bool], Optional[Dict], str]:
        """Forward smart watch check to remote backend. Returns (passed, state, msg).
        Returns (None, None, msg) if still in progress (not used in current flow).
        """
        r = tracker["resource"]
        backend = tracker["backend"]
        if not backend:
            return False, None, f"No backend for platform: {r.platform}"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            health_passed, post_state, health_msg = loop.run_until_complete(
                self.forward_health_check_to_backend(
                    backend=backend,
                    namespace=r.namespace,
                    name=r.resource_name,
                    kind=r.kind,
                    timeout_seconds=max(stuck_detection, crash_tolerance),
                    crash_tolerance=crash_tolerance,
                )
            )
            return health_passed, post_state, health_msg
        except Exception as e:
            return False, None, f"Remote check error: {str(e)}"
        finally:
            loop.close()

    def _update_smart_watch_result(
        self, db: Session, job: "ImageUpdateJob", resource, passed: bool,
        message: str, exec_log: list, post_state: Optional[Dict] = None,
    ):
        """Update ImageUpdateResult records after smart watch evaluation."""
        resource_results = db.query(ImageUpdateResult).filter(
            ImageUpdateResult.job_id == job.id,
            ImageUpdateResult.namespace == resource.namespace,
            ImageUpdateResult.resource_name == resource.resource_name,
            ImageUpdateResult.status == "success",
        ).all()
        for res in resource_results:
            res.post_patch_state = json.dumps(post_state) if post_state else None
            res.health_check_passed = passed
            res.health_check_message = message
            res.health_checked_at = datetime.now(timezone.utc)
            res.execution_log = json.dumps(exec_log)

    def _do_rollback(self, db: Session, job: "ImageUpdateJob", tracker: Dict, results: Dict, reason: str):
        """Rollback a single resource and update tracking."""
        r = tracker["resource"]
        exec_log = tracker["execution_log"]
        patched = tracker["patched_containers"]
        is_local = tracker["is_local"]
        backend = tracker["backend"]
        backend_name = tracker["backend_name"]

        tracker["status"] = "rolled_back"

        snapshot_enc = tracker.get("pre_patch_manifest")
        if snapshot_enc and is_local:
            # Field-edit ("Update Product") job: restore the exact pre-patch manifest (image +
            # all fields) in one replace, instead of re-patching each container's old image.
            from .crypto_utils import decrypt_value
            try:
                snap = json.loads(decrypt_value(snapshot_enc))
                restore_ok, restore_err = self.restore_workload(r.namespace, r.resource_name, r.kind, snap)
            except Exception as e:
                restore_ok, restore_err = False, f"snapshot decode error: {e}"
            if restore_ok:
                exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                 "level": "info",
                                 "message": "Rollback OK: restored pre-patch manifest"})
                for pc in patched:
                    self._update_resource_after_patch(db, r, pc["name"], pc["old_image"], job_id=job.id)
            else:
                exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                 "level": "error",
                                 "message": f"Restore rollback failed: {restore_err}"})
        elif tracker.get("field_edit") and backend:
            # Remote field-edit job: ask the OWNING backend to restore its own snapshot — an
            # image re-patch wouldn't undo the manifest edits (the snapshot lives on the remote).
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                restore_ok, restore_err = loop.run_until_complete(
                    self.forward_restore_to_backend(backend, job.id, r.namespace, r.resource_name, r.kind))
            finally:
                loop.close()
            if restore_ok:
                exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                 "level": "info",
                                 "message": "Rollback OK: remote restored pre-patch manifest"})
            else:
                exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                 "level": "error",
                                 "message": f"Remote restore rollback failed: {restore_err}"})
        else:
            for pc in patched:
                if is_local:
                    rollback_ok, _, rollback_err = self.patch_resource_image(
                        namespace=r.namespace, name=r.resource_name, kind=r.kind,
                        new_image=pc["old_image"], container_name=pc["name"],
                    )
                elif backend:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        rollback_ok, rollback_err = loop.run_until_complete(
                            self.forward_rollback_to_backend(
                                backend=backend, namespace=r.namespace,
                                name=r.resource_name, kind=r.kind,
                                old_image=pc["old_image"], container_name=pc["name"],
                            )
                        )
                    finally:
                        loop.close()
                else:
                    rollback_ok = False
                    rollback_err = f"No backend for platform: {r.platform}"

                if rollback_ok:
                    exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                     "level": "info",
                                     "message": f"Rollback OK: {pc['name'] or 'main'} -> {pc['old_image']}"})
                    if is_local:
                        self._update_resource_after_patch(db, r, pc["name"], pc["old_image"], job_id=job.id)
                else:
                    exec_log.append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                     "level": "error",
                                     "message": f"Rollback failed: {pc['name'] or 'main'}: {rollback_err}"})

        resource_results = db.query(ImageUpdateResult).filter(
            ImageUpdateResult.job_id == job.id,
            ImageUpdateResult.namespace == r.namespace,
            ImageUpdateResult.resource_name == r.resource_name,
            ImageUpdateResult.status == "success",
        ).all()
        for res in resource_results:
            res.health_check_passed = False
            res.health_check_message = reason
            res.health_checked_at = datetime.now(timezone.utc)
            res.was_rolled_back = True
            res.rollback_reason = reason
            res.status = "rolled_back"
            res.execution_log = json.dumps(exec_log)
            results["success"] -= 1
            results["rolled_back"] += 1
            if backend_name in results["by_backend"]:
                results["by_backend"][backend_name]["success"] -= 1
                results["by_backend"][backend_name]["rolled_back"] += 1
    
    def get_products_with_images(self, db: Session) -> List[Dict]:
        """Get all products with their current and latest images across platforms.
        
        Resources are matched to products in two ways:
        1. By their product_name tag
        2. By container image repository containing the product name
        
        This ensures resources like my-nginx (tagged fluent-bit) still appear
        under 'nginx' product because of the nginx container.
        """
        products: Dict[str, Dict] = {}
        
        # Get all resources with a product_name
        resources = db.query(Resource).filter(
            Resource.product_name.isnot(None),
            Resource.product_name != ""
        ).all()
        
        # First pass: collect all unique product names
        product_names = set()
        for r in resources:
            product_names.add(r.product_name)
        
        # Initialize product dictionaries
        for prod_name in product_names:
            products[prod_name] = {
                "product_name": prod_name,
                "platforms": set(),
                "namespaces": set(),
                "resources": [],
                "current_images": set(),
                "latest_images": set(),
                "_seen_resource_containers": set(),  # Track unique resource+container combos
            }
        
        # Sort product names by length descending for longest-match-first
        all_product_names = sorted(product_names, key=len, reverse=True)

        # Second pass: add each container to the product whose name best matches its image.
        for r in resources:
            containers = []
            if r.security_info and r.security_info.get("containers"):
                containers = r.security_info["containers"]
            
            if containers:
                for c in containers:
                    current_img = c.get("image", "")
                    latest_ver = c.get("latest_version", "")
                    container_name = c.get("name", "")
                    
                    resource_entry = {
                        "id": r.id,
                        "platform": r.platform,
                        "namespace": r.namespace,
                        "resource_name": r.resource_name,
                        "kind": r.kind,
                        "container_name": container_name,
                        "current_image": current_img,
                        "current_version": c.get("current_version") or (current_img.rsplit(":", 1)[-1] if ":" in current_img else ""),
                        "latest_version": latest_ver,
                        "version_diff": c.get("version_diff", "unknown"),
                        # Phase 1: surface Helm ownership so the Update Product wizard can require
                        # the Helm-managed acknowledgment at create (incl. federated/remote resources).
                        "helm_status": r.helm_status,
                        "helm_release_name": r.helm_release_name,
                    }
                    
                    # Find best matching product for this container's image (longest match first)
                    matched_product = None
                    if current_img:
                        for pn in all_product_names:
                            if _image_matches_product(current_img, pn):
                                matched_product = pn
                                break
                    if not matched_product:
                        matched_product = r.product_name
                    
                    if matched_product and matched_product in products:
                        p = products[matched_product]
                        unique_key = f"{r.id}|{container_name}"
                        if unique_key not in p["_seen_resource_containers"]:
                            p["_seen_resource_containers"].add(unique_key)
                            p["platforms"].add(r.platform)
                            p["namespaces"].add(r.namespace)
                            if current_img:
                                p["current_images"].add(current_img)
                                if latest_ver and ":" in current_img:
                                    base_img = current_img.rsplit(":", 1)[0]
                                    p["latest_images"].add(f"{base_img}:{latest_ver}")
                            p["resources"].append(resource_entry.copy())
            else:
                resource_entry = {
                    "id": r.id,
                    "platform": r.platform,
                    "namespace": r.namespace,
                    "resource_name": r.resource_name,
                    "kind": r.kind,
                    "container_name": None,
                    "current_image": r.image,
                    "current_version": r.current_version,
                    "latest_version": r.latest_version,
                    "version_diff": r.version_diff,
                    "helm_status": r.helm_status,
                    "helm_release_name": r.helm_release_name,
                }
                
                matched_product = None
                if r.image:
                    for pn in all_product_names:
                        if _image_matches_product(r.image, pn):
                            matched_product = pn
                            break
                if not matched_product:
                    matched_product = r.product_name
                
                if matched_product and matched_product in products:
                    p = products[matched_product]
                    unique_key = f"{r.id}|"
                    if unique_key not in p["_seen_resource_containers"]:
                        p["_seen_resource_containers"].add(unique_key)
                        p["platforms"].add(r.platform)
                        p["namespaces"].add(r.namespace)
                        if r.image:
                            p["current_images"].add(r.image)
                            if r.latest_version and ":" in r.image:
                                base_img = r.image.rsplit(":", 1)[0]
                                p["latest_images"].add(f"{base_img}:{r.latest_version}")
                        p["resources"].append(resource_entry.copy())
        
        # Convert sets to lists and clean up
        result = []
        for prod_name, data in products.items():
            # Only include products that have at least one resource
            if data["resources"]:
                del data["_seen_resource_containers"]  # Remove internal tracking
                data["platforms"] = sorted(list(data["platforms"]))
                data["namespaces"] = sorted(list(data["namespaces"]))
                data["current_images"] = sorted(list(data["current_images"]))
                data["latest_images"] = sorted(list(data["latest_images"]))
                result.append(data)
        
        return sorted(result, key=lambda x: x["product_name"])
    
    def get_product_details(self, db: Session, product_name: str) -> Optional[Dict]:
        """Get detailed info for a specific product."""
        products = self.get_products_with_images(db)
        for p in products:
            if p["product_name"] == product_name:
                return p
        return None
    
    async def get_products_with_images_federated(
        self,
        db: Session,
        token: str,
        platform_filter: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Get products from local AND remote backends (federated query).
        
        This is used by the primary backend to show all products across
        all platforms in the federation.
        
        Args:
            db: Database session
            token: User's JWT token
            platform_filter: Optional list of platform names to filter.
                           Only backends matching these platforms will be queried.
        """
        # Get the default/local backend to check its platform
        default_backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.is_default == True
        ).first()
        local_platform = default_backend.platform if default_backend else None
        
        # Check if we should include local products
        include_local = True
        if platform_filter and local_platform:
            include_local = local_platform in platform_filter
        
        # Get local products if needed
        local_products = []
        if include_local:
            local_products = self.get_products_with_images(db)
            log_event(
                "image_update.products.local",
                products_count=len(local_products),
                platform=local_platform,
            )
        else:
            log_event(
                "image_update.products.local_skipped",
                platform=local_platform,
                filter=platform_filter,
            )
        
        # Get all enabled remote backends
        backends_query = db.query(BackendEndpoint).filter(
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True,
            BackendEndpoint.is_default == False,  # Skip local/default backend
        )
        
        # Apply platform filter to backends query
        if platform_filter:
            backends_query = backends_query.filter(
                BackendEndpoint.platform.in_(platform_filter)
            )
        
        backends = backends_query.all()
        
        if not backends and not local_products:
            # No backends to query and no local products
            return []
        
        if not backends:
            # No remote backends, return local only
            return local_products
        
        # Prepare backend info for async queries
        backend_infos = []
        for b in backends:
            backend_infos.append({
                "name": b.name,
                "platform": b.platform,
                "api_url": b.api_url,
                "auth_token": b.auth_token,
                "skip_tls_verify": b.skip_tls_verify,
            })
        
        log_event(
            "image_update.products.querying_remotes",
            backends=[b["name"] for b in backend_infos],
            platform_filter=platform_filter,
        )
        
        # Query all remote backends in parallel
        tasks = [
            _fetch_products_from_backend(backend, token)
            for backend in backend_infos
        ]
        
        remote_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect all products (local + remote)
        all_products = [local_products] if local_products else []
        for i, result in enumerate(remote_results):
            if isinstance(result, Exception):
                log_event(
                    "image_update.products.remote_exception",
                    backend=backend_infos[i]["name"],
                    error=str(result),
                )
            elif result:
                all_products.append(result)
        
        # Merge all products
        merged = _merge_products(all_products)
        
        log_event(
            "image_update.products.merged",
            local_count=len(local_products),
            merged_count=len(merged),
        )
        
        return merged
    
    def get_product_details_federated(
        self,
        db: Session,
        product_name: str,
        token: str,
        platforms: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        """Get detailed info for a specific product from selected backends only.
        
        Args:
            platforms: Optional list of platform names to query. If provided,
                      only backends serving those platforms will be queried.
        """
        # Run async method synchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            products = loop.run_until_complete(
                self.get_products_with_images_federated(db, token, platform_filter=platforms)
            )
        finally:
            loop.close()
        
        for p in products:
            if p["product_name"] == product_name:
                return p
        return None
    
    def get_matching_resources(
        self,
        db: Session,
        product_name: str,
        target_platforms: Optional[List[str]] = None,
        target_namespaces: Optional[List[str]] = None,
        target_resources: Optional[List] = None,  # Can be List[int] (IDs) or List[str] (keys)
    ) -> List[Resource]:
        """Get resources matching the update criteria.
        
        Matches resources by:
        1. product_name tag matches
        2. OR any container image matches the product name
        
        Args:
            target_resources: Can be:
                - List of integers (resource IDs) - legacy, local only
                - List of strings (resource keys) - cross-backend compatible
                  Format: "platform|namespace|kind|resource_name"
        """
        # Start with resources that have any product_name
        query = db.query(Resource).filter(
            Resource.product_name.isnot(None),
            Resource.product_name != ""
        )
        
        if target_platforms:
            query = query.filter(Resource.platform.in_(target_platforms))
        
        if target_namespaces:
            query = query.filter(Resource.namespace.in_(target_namespaces))
        
        # Handle resource filtering - can be IDs (int) or keys (str)
        resource_keys_filter: Optional[set] = None
        if target_resources:
            # Check if first element is int (legacy ID mode) or str (key mode)
            if isinstance(target_resources[0], int):
                # Legacy mode: filter by IDs
                query = query.filter(Resource.id.in_(target_resources))
            else:
                # Key mode: store keys for post-filtering
                # Format: "platform|namespace|kind|resource_name"
                resource_keys_filter = set(target_resources)
        
        all_resources = query.all()
        
        # Post-filter by resource keys if specified
        if resource_keys_filter:
            filtered_resources = []
            for r in all_resources:
                resource_key = f"{r.platform}|{r.namespace}|{r.kind}|{r.resource_name}"
                if resource_key in resource_keys_filter:
                    filtered_resources.append(r)
            all_resources = filtered_resources
        
        log_event("image_update.get_resources.query",
                 product_name=product_name,
                 target_platforms=target_platforms,
                 target_namespaces=target_namespaces,
                 target_resources_count=len(target_resources) if target_resources else 0,
                 all_resources_count=len(all_resources))
        
        # Filter to those matching product by name or by image
        matching = []
        for r in all_resources:
            # Check 1: product_name matches
            if r.product_name == product_name:
                log_event("image_update.resource.matched",
                         resource_id=r.id,
                         resource_name=r.resource_name,
                         reason="product_name_match",
                         product_name=r.product_name)
                matching.append(r)
                continue
            
            # Check 2: any container image matches product
            containers = []
            if r.security_info and r.security_info.get("containers"):
                containers = r.security_info["containers"]
            
            if containers:
                for c in containers:
                    img = c.get("image", "")
                    if img and _image_matches_product(img, product_name):
                        log_event("image_update.resource.matched",
                                 resource_id=r.id,
                                 resource_name=r.resource_name,
                                 reason="image_match",
                                 image=img,
                                 product_name=product_name)
                        matching.append(r)
                        break
            elif r.image and _image_matches_product(r.image, product_name):
                log_event("image_update.resource.matched",
                         resource_id=r.id,
                         resource_name=r.resource_name,
                         reason="single_image_match",
                         image=r.image)
                matching.append(r)
        
        log_event("image_update.get_resources.result",
                 product_name=product_name,
                 matching_count=len(matching))
        
        return matching
    
    # ── Phase 1: structured field editing (Update Product) ──────────────────
    # Workload accessors keyed by kind, so the apply/snapshot logic is written once.
    # NOTE: this path is ONLY used for jobs that carry field_edits. Image-only jobs keep
    # using patch_resource_image() below, byte-for-byte unchanged.
    _SNAPSHOT_DROP_META = ("managedFields", "resourceVersion", "uid",
                           "creationTimestamp", "generation", "selfLink")

    def _read_workload(self, kind_lower: str, name: str, namespace: str):
        if kind_lower == "deployment":
            return self.apps_api.read_namespaced_deployment(name, namespace)
        if kind_lower == "statefulset":
            return self.apps_api.read_namespaced_stateful_set(name, namespace)
        if kind_lower == "daemonset":
            return self.apps_api.read_namespaced_daemon_set(name, namespace)
        if kind_lower == "cronjob":
            return self.batch_api.read_namespaced_cron_job(name, namespace)
        return None

    def _patch_workload(self, kind_lower: str, name: str, namespace: str, obj) -> None:
        if kind_lower == "deployment":
            self.apps_api.patch_namespaced_deployment(name=name, namespace=namespace, body=obj)
        elif kind_lower == "statefulset":
            self.apps_api.patch_namespaced_stateful_set(name=name, namespace=namespace, body=obj)
        elif kind_lower == "daemonset":
            self.apps_api.patch_namespaced_daemon_set(name=name, namespace=namespace, body=obj)
        elif kind_lower == "cronjob":
            self.batch_api.patch_namespaced_cron_job(name=name, namespace=namespace, body=obj)

    def _pod_template_of(self, obj, kind_lower: str):
        """Return the V1PodTemplateSpec for any supported kind."""
        if kind_lower == "cronjob":
            return obj.spec.job_template.spec.template
        return obj.spec.template

    def _select_container(self, containers, container_name: Optional[str]):
        """The edited container: by name if given; the sole container if there is one;
        otherwise None (ambiguous → caller refuses)."""
        if container_name:
            for c in containers:
                if c.name == container_name:
                    return c
            return None
        if containers and len(containers) == 1:
            return containers[0]
        return None

    def _snapshot_workload(self, obj) -> dict:
        """Sanitized spec+metadata snapshot — the restore-based-rollback source of truth.
        Drops cluster-managed/noise fields and status but KEEPS real values; the caller
        encrypts it at rest (crypto_utils) so literal env values aren't persisted in plain."""
        full = self.apps_api.api_client.sanitize_for_serialization(obj)
        meta = dict(full.get("metadata") or {})
        for k in self._SNAPSHOT_DROP_META:
            meta.pop(k, None)
        if isinstance(meta.get("annotations"), dict):
            meta["annotations"].pop("kubectl.kubernetes.io/last-applied-configuration", None)
            meta["annotations"].pop("deployment.kubernetes.io/revision", None)
        snap = {
            "kind": full.get("kind"),
            "apiVersion": full.get("apiVersion"),
            "metadata": {k: meta[k] for k in ("name", "namespace", "labels", "annotations") if meta.get(k) is not None},
            "spec": full.get("spec"),
        }
        return snap

    def _apply_one_edit(self, edit: dict, containers, pod_tmpl, obj,
                        default_container: Optional[str], field_changes: list) -> None:
        """Apply one whitelisted, typed edit to the in-memory workload object. Raises
        ValueError on an unknown type/op or a missing target container (caller fails the
        resource cleanly). Affinity is handled in a later step."""
        from kubernetes.client import V1EnvVar, V1ResourceRequirements, V1ObjectMeta
        etype = (edit.get("type") or "").lower()
        op = (edit.get("op") or "set").lower()

        if etype in ("env", "resource", "command", "args"):
            cname = edit.get("container") or default_container
            c = self._select_container(containers, cname)
            if c is None:
                raise ValueError(f"edit '{etype}': container not found/ambiguous (container={cname})")
            if etype == "env":
                key = edit.get("name")
                c.env = c.env or []
                existing = next((e for e in c.env if e.name == key), None)
                if op == "remove":
                    if existing is not None:
                        c.env = [e for e in c.env if e.name != key]
                        field_changes.append({"path": f"container[{c.name}].env[{key}]",
                                              "old": getattr(existing, "value", None), "new": None})
                else:
                    val = edit.get("value")
                    old = getattr(existing, "value", None) if existing is not None else None
                    if existing is not None:
                        existing.value = val
                        existing.value_from = None  # literal value supersedes any valueFrom
                    else:
                        c.env.append(V1EnvVar(name=key, value=val))
                    field_changes.append({"path": f"container[{c.name}].env[{key}]", "old": old, "new": val})
            elif etype == "resource":
                rkind = (edit.get("kind") or "").lower()   # request | limit
                rname = edit.get("name")                    # cpu | memory
                val = edit.get("value")
                if c.resources is None:
                    c.resources = V1ResourceRequirements()
                bucket = (c.resources.requests if rkind == "request" else c.resources.limits) or {}
                old = bucket.get(rname)
                bucket[rname] = val
                if rkind == "request":
                    c.resources.requests = bucket
                else:
                    c.resources.limits = bucket
                field_changes.append({"path": f"container[{c.name}].resources.{rkind}s.{rname}",
                                      "old": old, "new": val})
            elif etype == "command":
                old = c.command
                c.command = edit.get("value")
                field_changes.append({"path": f"container[{c.name}].command", "old": old, "new": c.command})
            elif etype == "args":
                old = c.args
                c.args = edit.get("value")
                field_changes.append({"path": f"container[{c.name}].args", "old": old, "new": c.args})

        elif etype in ("label", "annotation"):
            target = (edit.get("target") or "pod").lower()   # pod | workload
            holder = pod_tmpl if target == "pod" else obj
            meta = holder.metadata
            if meta is None:
                meta = V1ObjectMeta()
                holder.metadata = meta
            attr = "labels" if etype == "label" else "annotations"
            d = getattr(meta, attr) or {}
            key = edit.get("key")
            if op == "remove":
                if key in d:
                    old = d.pop(key, None)
                    field_changes.append({"path": f"{target}.{attr}[{key}]", "old": old, "new": None})
            else:
                old = d.get(key)
                d[key] = edit.get("value")
                field_changes.append({"path": f"{target}.{attr}[{key}]", "old": old, "new": edit.get("value")})
            setattr(meta, attr, d)

        elif etype == "affinity":
            # Pod-template scoped (spec.template.spec.affinity). set replaces (adds if absent),
            # remove deletes. The value is deserialized via the k8s model (validation) — not
            # free-form YAML. subkey targets a sub-block or 'all' for the whole affinity.
            from kubernetes.client import V1Affinity
            sub = (edit.get("subkey") or "all").lower()
            sub_attr = {"podantiaffinity": "pod_anti_affinity", "podaffinity": "pod_affinity",
                        "nodeaffinity": "node_affinity"}.get(sub)
            pod_spec = pod_tmpl.spec
            if op == "remove":
                if sub == "all":
                    if pod_spec.affinity is not None:
                        pod_spec.affinity = None
                        field_changes.append({"path": "affinity", "old": "set", "new": None})
                elif pod_spec.affinity is not None and getattr(pod_spec.affinity, sub_attr, None) is not None:
                    setattr(pod_spec.affinity, sub_attr, None)
                    field_changes.append({"path": f"affinity.{edit.get('subkey')}", "old": "set", "new": None})
            else:  # set
                fake = types.SimpleNamespace(data=json.dumps(edit.get("value") or {}))
                if sub == "all":
                    old = "set" if pod_spec.affinity is not None else None
                    pod_spec.affinity = self.apps_api.api_client.deserialize(fake, "V1Affinity")
                    field_changes.append({"path": "affinity", "old": old, "new": "set"})
                else:
                    if pod_spec.affinity is None:
                        pod_spec.affinity = V1Affinity()
                    model = {"podantiaffinity": "V1PodAntiAffinity", "podaffinity": "V1PodAffinity",
                             "nodeaffinity": "V1NodeAffinity"}[sub]
                    old = "set" if getattr(pod_spec.affinity, sub_attr, None) is not None else None
                    setattr(pod_spec.affinity, sub_attr, self.apps_api.api_client.deserialize(fake, model))
                    field_changes.append({"path": f"affinity.{edit.get('subkey')}", "old": old, "new": "set"})
        else:
            raise ValueError(f"unsupported edit type: {etype}")

    def apply_resource_changes(
        self,
        namespace: str,
        name: str,
        kind: str,
        new_image: Optional[str] = None,
        field_edits: Optional[list] = None,
        container_name: Optional[str] = None,
        allow_helm_managed: bool = True,
        max_retries: int = 3,
    ) -> dict:
        """One read-modify-write applying an optional image change AND a list of structured
        field edits to a workload — so the resource rolls out once. Captures a sanitized,
        encrypted pre-patch snapshot (rollback source of truth), detects Helm ownership, and
        records an applied-edits summary. Used ONLY by field-edit ("Update Product") jobs.

        Returns: {success, old_image, error, pre_patch_manifest, field_changes,
                  helm_managed, helm_release}
        """
        base = {"success": False, "old_image": None, "error": None, "pre_patch_manifest": None,
                "field_changes": [], "helm_managed": False, "helm_release": None}
        if not self.is_available():
            base["error"] = "Kubernetes client not available"
            return base

        from kubernetes.client import ApiException
        import time
        from .crypto_utils import encrypt_value

        kind_lower = kind.strip().lower()
        edits = field_edits or []

        for attempt in range(max_retries):
            try:
                obj = self._read_workload(kind_lower, name, namespace)
                if obj is None:
                    base["error"] = f"Unsupported resource kind: {kind}"
                    return base

                pod_tmpl = self._pod_template_of(obj, kind_lower)
                containers = pod_tmpl.spec.containers
                wf_ann = obj.metadata.annotations or {}
                helm_release = wf_ann.get("meta.helm.sh/release-name")
                helm_managed = bool(helm_release)

                # Extra Helm-managed gate (definitive, live check BEFORE any mutation): refuse
                # unless the job explicitly acknowledged it. A direct patch is reverted by the
                # next helm upgrade; the durable path is an admin-run helm upgrade (CLI).
                if helm_managed and not allow_helm_managed:
                    base["helm_managed"] = True
                    base["helm_release"] = helm_release
                    base["error"] = ("blocked: target is Helm-managed (release "
                                     f"'{helm_release}') and the job has no Helm-managed acknowledgment")
                    log_event("update_product.apply.blocked_helm", namespace=namespace,
                              resource=name, kind=kind, release=helm_release)
                    return base

                # Snapshot the (still pre-patch) object on each attempt; the returned one is
                # from the attempt whose patch actually lands.
                snap = self._snapshot_workload(obj)
                pre_patch_manifest = encrypt_value(json.dumps(snap))

                field_changes: list = []
                old_image = None
                if new_image:
                    tc = self._select_container(containers, container_name)
                    if tc is None:
                        base["error"] = f"Refused: container ambiguous/not found for image patch on {name}"
                        return base
                    old_image = tc.image
                    if tc.image != new_image:
                        field_changes.append({"path": f"container[{tc.name}].image",
                                              "old": tc.image, "new": new_image})
                        tc.image = new_image

                for edit in edits:
                    self._apply_one_edit(edit, containers, pod_tmpl, obj, container_name, field_changes)

                self._patch_workload(kind_lower, name, namespace, obj)
                log_event("update_product.apply.success", namespace=namespace, resource=name,
                          kind=kind, edits=len(edits),
                          image_changed=bool(new_image and old_image != new_image),
                          helm_managed=helm_managed)
                return {"success": True, "old_image": old_image, "error": None,
                        "pre_patch_manifest": pre_patch_manifest, "field_changes": field_changes,
                        "helm_managed": helm_managed, "helm_release": helm_release}

            except ApiException as e:
                if e.status == 409 and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                if e.status == 403:
                    msg = (f"403 Forbidden — the service account cannot patch {kind} '{name}' in "
                           f"'{namespace}'. Grant 'patch','update' on {kind.lower()}s.")
                elif e.status == 404:
                    msg = f"404 — {kind} '{name}' not found in '{namespace}'"
                elif e.status == 422:
                    msg = f"422 — invalid patch for {kind} '{name}': {e.reason}"
                else:
                    msg = f"Kubernetes API error: {e.status} - {e.reason}"
                if getattr(e, "body", None):
                    try:
                        b = json.loads(e.body)
                        if b.get("message"):
                            msg += f" | {b['message'][:200]}"
                    except Exception:
                        pass
                log_event("update_product.apply.error", namespace=namespace, resource=name,
                          kind=kind, status=e.status, error=msg)
                base["error"] = msg
                return base
            except Exception as e:
                log_event("update_product.apply.exception", namespace=namespace, resource=name,
                          kind=kind, error=str(e))
                base["error"] = str(e)
                return base

        base["error"] = "exhausted retries"
        return base

    def restore_workload(self, namespace: str, name: str, kind: str, snapshot: dict,
                         max_retries: int = 3) -> Tuple[bool, Optional[str]]:
        """Restore a workload to a captured pre-patch snapshot (rollback source of truth for
        field-edit jobs). Reads the live object for a fresh resourceVersion, deserializes the
        snapshot into the typed model, and REPLACES the object — so additions we made (e.g. a
        new env var) are removed rather than merged back. Returns (success, error)."""
        if not self.is_available():
            return False, "Kubernetes client not available"
        from kubernetes.client import ApiException
        import time
        kind_lower = kind.strip().lower()
        type_map = {"deployment": "V1Deployment", "statefulset": "V1StatefulSet",
                    "daemonset": "V1DaemonSet", "cronjob": "V1CronJob"}
        model = type_map.get(kind_lower)
        if not model:
            return False, f"Unsupported resource kind: {kind}"
        for attempt in range(max_retries):
            try:
                current = self._read_workload(kind_lower, name, namespace)
                if current is None:
                    return False, f"{kind} '{name}' not found in '{namespace}'"
                fake = types.SimpleNamespace(data=json.dumps(snapshot))
                obj = self.apps_api.api_client.deserialize(fake, model)
                if obj.metadata is None:
                    obj.metadata = current.metadata
                obj.metadata.resource_version = current.metadata.resource_version
                if kind_lower == "deployment":
                    self.apps_api.replace_namespaced_deployment(name=name, namespace=namespace, body=obj)
                elif kind_lower == "statefulset":
                    self.apps_api.replace_namespaced_stateful_set(name=name, namespace=namespace, body=obj)
                elif kind_lower == "daemonset":
                    self.apps_api.replace_namespaced_daemon_set(name=name, namespace=namespace, body=obj)
                elif kind_lower == "cronjob":
                    self.batch_api.replace_namespaced_cron_job(name=name, namespace=namespace, body=obj)
                log_event("update_product.restore.success", namespace=namespace, resource=name, kind=kind)
                return True, None
            except ApiException as e:
                if e.status == 409 and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                msg = f"restore failed: {e.status} - {e.reason}"
                log_event("update_product.restore.error", namespace=namespace, resource=name, kind=kind, error=msg)
                return False, msg
            except Exception as e:
                log_event("update_product.restore.exception", namespace=namespace, resource=name, kind=kind, error=str(e))
                return False, str(e)
        return False, "exhausted retries"

    def patch_resource_image(
        self,
        namespace: str,
        name: str,
        kind: str,
        new_image: str,
        container_name: Optional[str] = None,
        max_retries: int = 3,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Patch a Kubernetes resource with a new image.
        Includes retry logic for 409 Conflict errors (resourceVersion mismatch).
        
        Returns: (success, old_image, error_message)
        """
        if not self.is_available():
            return False, None, "Kubernetes client not available"
        
        from kubernetes.client import ApiException
        import time
        
        kind_lower = kind.strip().lower()
        
        for attempt in range(max_retries):
            try:
                old_image = None
                
                if kind_lower == "deployment":
                    dep = self.apps_api.read_namespaced_deployment(name, namespace)
                    containers = dep.spec.template.spec.containers
                    
                    if container_name is None and len(containers) > 1:
                        return False, None, f"Refused: {name} has {len(containers)} containers but no container_name specified"
                    
                    for c in containers:
                        if container_name is None or c.name == container_name:
                            old_image = c.image
                            c.image = new_image
                            if container_name:
                                break
                    
                    self.apps_api.patch_namespaced_deployment(
                        name=name,
                        namespace=namespace,
                        body=dep
                    )
                    
                elif kind_lower == "statefulset":
                    ss = self.apps_api.read_namespaced_stateful_set(name, namespace)
                    containers = ss.spec.template.spec.containers
                    
                    if container_name is None and len(containers) > 1:
                        return False, None, f"Refused: {name} has {len(containers)} containers but no container_name specified"
                    
                    for c in containers:
                        if container_name is None or c.name == container_name:
                            old_image = c.image
                            c.image = new_image
                            if container_name:
                                break
                    
                    self.apps_api.patch_namespaced_stateful_set(
                        name=name,
                        namespace=namespace,
                        body=ss
                    )
                    
                elif kind_lower == "daemonset":
                    ds = self.apps_api.read_namespaced_daemon_set(name, namespace)
                    containers = ds.spec.template.spec.containers
                    
                    if container_name is None and len(containers) > 1:
                        return False, None, f"Refused: {name} has {len(containers)} containers but no container_name specified"
                    
                    for c in containers:
                        if container_name is None or c.name == container_name:
                            old_image = c.image
                            c.image = new_image
                            if container_name:
                                break
                    
                    self.apps_api.patch_namespaced_daemon_set(
                        name=name,
                        namespace=namespace,
                        body=ds
                    )
                    
                elif kind_lower == "cronjob":
                    cj = self.batch_api.read_namespaced_cron_job(name, namespace)
                    containers = cj.spec.job_template.spec.template.spec.containers
                    
                    for c in containers:
                        if container_name is None or c.name == container_name:
                            old_image = c.image
                            c.image = new_image
                            if container_name:
                                break
                    
                    self.batch_api.patch_namespaced_cron_job(
                        name=name,
                        namespace=namespace,
                        body=cj
                    )
                    
                else:
                    return False, None, f"Unsupported resource kind: {kind}"
                
                log_event(
                    "image_update.patch.success",
                    namespace=namespace,
                    resource=name,
                    kind=kind,
                    old_image=old_image,
                    new_image=new_image,
                    container=container_name,
                )
                
                return True, old_image, None
                
            except ApiException as e:
                # Retry on 409 Conflict (resourceVersion mismatch)
                if e.status == 409 and attempt < max_retries - 1:
                    log_event(
                        "image_update.patch.conflict_retry",
                        namespace=namespace,
                        resource=name,
                        kind=kind,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    time.sleep(0.1 * (attempt + 1))  # Brief backoff
                    continue
                
                # Build detailed error message
                if e.status == 403:
                    error_msg = (
                        f"Kubernetes API error: 403 - Forbidden. "
                        f"The service account does not have permission to patch {kind} '{name}' in namespace '{namespace}'. "
                        f"Please ensure the RBAC role has 'patch' and 'update' permissions for {kind.lower()}s."
                    )
                elif e.status == 404:
                    error_msg = f"Kubernetes API error: 404 - {kind} '{name}' not found in namespace '{namespace}'"
                elif e.status == 422:
                    error_msg = f"Kubernetes API error: 422 - Invalid patch request for {kind} '{name}': {e.reason}"
                else:
                    error_msg = f"Kubernetes API error: {e.status} - {e.reason}"
                
                # Include body message if available
                if hasattr(e, 'body') and e.body:
                    try:
                        import json as json_lib
                        body = json_lib.loads(e.body)
                        if body.get('message'):
                            error_msg += f" | Details: {body['message'][:200]}"
                    except Exception:
                        pass
                
                log_event(
                    "image_update.patch.error",
                    namespace=namespace,
                    resource=name,
                    kind=kind,
                    status=e.status,
                    error=error_msg,
                )
                return False, None, error_msg
                
            except Exception as e:
                error_msg = str(e)
                log_event(
                    "image_update.patch.error",
                    namespace=namespace,
                    resource=name,
                    kind=kind,
                    error=error_msg,
                )
                return False, None, error_msg
        
        return False, None, "Max retries exceeded"
    
    def _update_resource_after_patch(
        self,
        db: Session,
        resource: Resource,
        container_name: Optional[str],
        new_image: str,
        job_id: Optional[int] = None,
    ) -> None:
        """Update the Resource table after a successful patch, including EOL and Twistlock data."""
        import copy
        from .versioning import parse_image, fetch_latest_version, compare_semver
        
        try:
            # Store old values for history - get from the specific container if multi-container
            old_image = resource.image
            old_version = resource.current_version
            
            # For multi-container resources, get the actual old image from security_info.containers
            if container_name and resource.security_info:
                containers = resource.security_info.get("containers", [])
                for c in containers:
                    if c.get("name") == container_name:
                        old_image = c.get("image", old_image)
                        old_version = c.get("current_version", old_version)
                        break
            
            # Extract version from new image
            new_version = None
            if ":" in new_image:
                new_version = new_image.rsplit(":", 1)[-1]
            
            # Parse the new image to fetch additional data
            parsed_image = parse_image(new_image)
            
            # Fetch latest version from registry
            latest_version = None
            version_diff = "unknown"
            if parsed_image:
                try:
                    latest_version = fetch_latest_version(parsed_image)
                    if latest_version and new_version:
                        diff_result = compare_semver(new_version, latest_version)
                        version_diff = diff_result.category if hasattr(diff_result, 'category') else str(diff_result)
                    log_event("image_update.version.fetched", 
                             resource=resource.resource_name, 
                             current=new_version, 
                             latest=latest_version,
                             diff=version_diff)
                except Exception as e:
                    log_event("image_update.version.fetch_error", resource=resource.resource_name, error=str(e))
            
            # Fetch EOL data from endoflife.date API
            eol_data = None
            if resource.product_name and new_version:
                try:
                    import httpx
                    # Try to get EOL info from endoflife.date
                    product_slug = resource.product_name.lower().replace(" ", "-")
                    # Extract major.minor version for EOL lookup
                    version_parts = new_version.split(".")
                    if len(version_parts) >= 2:
                        cycle = f"{version_parts[0]}.{version_parts[1]}"
                        url = f"https://endoflife.date/api/{product_slug}/{cycle}.json"
                        with httpx.Client(timeout=5.0) as client:
                            resp = client.get(url)
                            if resp.status_code == 200:
                                eol_data = resp.json()
                                log_event("image_update.eol.fetched", 
                                         resource=resource.resource_name, 
                                         product=product_slug,
                                         cycle=cycle,
                                         eol=eol_data.get("eol"))
                except Exception as e:
                    log_event("image_update.eol.fetch_error", resource=resource.resource_name, error=str(e))
            
            # Fetch Twistlock CVE data (respecting federation config)
            twistlock_data = None
            twistlock_vulns = []
            try:
                from .image_cache import get_twistlock_config, fetch_twistlock_from_primary, is_primary_backend
                twistlock_config = get_twistlock_config(db)
                use_primary_tw = twistlock_config.get("mode") == "primary" and not is_primary_backend()
                
                if use_primary_tw:
                    twistlock_data = fetch_twistlock_from_primary(db, new_image)
                else:
                    from .twistlock import fetch_image_report
                    twistlock_data = fetch_image_report(new_image)
                
                if twistlock_data:
                    twistlock_vulns = twistlock_data.get("vulnerabilities") or []
                    log_event("image_update.twistlock.fetched", 
                             resource=resource.resource_name, 
                             vulns=len(twistlock_vulns),
                             via="primary" if use_primary_tw else "local")
            except Exception as e:
                log_event("image_update.twistlock.fetch_error", resource=resource.resource_name, error=str(e))
            
            # Fetch Trivy data (respecting federation config)
            trivy_data = None
            try:
                from .image_cache import get_trivy_config, fetch_trivy_from_primary, get_cached_trivy, set_cached_trivy
                trivy_config = get_trivy_config(db)
                use_primary_trv = trivy_config.get("mode") == "primary" and not is_primary_backend()
                
                if use_primary_trv:
                    trivy_data = fetch_trivy_from_primary(db, new_image)
                else:
                    from .trivy import submit_scan as trivy_submit_scan
                    trivy_submit_scan(new_image)
                
                if trivy_data:
                    set_cached_trivy(db, new_image, trivy_data)
                    log_event("image_update.trivy.fetched",
                             resource=resource.resource_name,
                             total=trivy_data.get("vulnerabilityDistribution", {}).get("total", 0),
                             via="primary" if use_primary_trv else "local")
            except Exception as e:
                log_event("image_update.trivy.fetch_error", resource=resource.resource_name, error=str(e))
            
            # Update security_info containers
            if resource.security_info and resource.security_info.get("containers"):
                # Deep copy to ensure SQLAlchemy detects the change
                updated_security_info = copy.deepcopy(resource.security_info)
                containers = updated_security_info["containers"]
                
                # Filter out containers with excluded registries
                from .exclusions import should_skip_image
                filtered_containers = []
                for c in containers:
                    c_image = c.get("image", "")
                    if c_image and should_skip_image(c_image, db):
                        log_event("image_update.container.excluded",
                                 resource=resource.resource_name,
                                 container=c.get("name"),
                                 image=c_image,
                                 reason="excluded_registry")
                        continue  # Skip excluded registry containers
                    
                    if container_name is None or c.get("name") == container_name:
                        c["image"] = new_image
                        if new_version:
                            c["current_version"] = new_version
                        if latest_version:
                            c["latest_version"] = latest_version
                            c["version_diff"] = version_diff
                    filtered_containers.append(c)
                
                updated_security_info["containers"] = filtered_containers
                
                # Update Twistlock data (both vulnerability list and distribution)
                if twistlock_data:
                    updated_security_info["vulnerabilities"] = twistlock_vulns
                    updated_security_info["vulnerability_count"] = len(twistlock_vulns)
                    updated_security_info["twistlock"] = twistlock_data
                    updated_security_info["twistlock_fetched_at"] = datetime.now(timezone.utc).isoformat()
                
                # Update Trivy data
                if trivy_data:
                    updated_security_info["trivy"] = trivy_data
                    updated_security_info["trivy_fetched_at"] = datetime.now(timezone.utc).isoformat()
                
                # Clear stale LLM advice (image changed, old advice is invalid)
                updated_security_info.pop("advice", None)
                updated_security_info.pop("llm_advice", None)
                updated_security_info.pop("llm_advice_at", None)
                
                # Assign the new dict and flag as modified
                resource.security_info = updated_security_info
                flag_modified(resource, "security_info")
            
            # Update main image and version fields
            resource.image = new_image
            if new_version:
                resource.current_version = new_version
            if latest_version:
                resource.latest_version = latest_version
                resource.version_diff = version_diff
            
            # Update EOL data (eol can be false boolean when not yet EOL)
            if eol_data:
                raw_eol = eol_data.get("eol")
                resource.eol_date = str(raw_eol) if isinstance(raw_eol, str) and raw_eol else None
            
            # Clear stale advice on main resource field
            resource.advice = None
            
            # Update timestamp
            now = datetime.now(timezone.utc)
            resource.last_updated_at = now
            resource.last_checked_at = now
            
            # Record history entry for this specific container (only if image actually changed)
            image_changed = old_image != new_image
            version_changed = old_version != new_version
            
            if image_changed or version_changed:
                history_entry = ResourceHistory(
                    resource_id=resource.id,
                    container_name=container_name,
                    image=new_image,
                    version=new_version,
                    latest_version=latest_version or resource.latest_version,
                    version_diff=version_diff,
                    checked_at=now,
                    source="job" if job_id else "manual",
                    job_id=job_id,
                )
                db.add(history_entry)
                log_event(
                    "image_update.history.added",
                    resource_id=resource.id,
                    resource_name=resource.resource_name,
                    container=container_name,
                    old_image=old_image,
                    new_image=new_image,
                    old_version=old_version,
                    new_version=new_version,
                )
            
            # Flush to ensure changes are written
            db.flush()
            
            log_event(
                "image_update.resource.updated",
                resource_id=resource.id,
                resource_name=resource.resource_name,
                container=container_name,
                new_image=new_image,
                new_version=new_version,
                latest_version=latest_version,
                version_diff=version_diff,
            )
            
        except Exception as e:
            log_event(
                "image_update.resource.update_error",
                resource_id=resource.id,
                error=str(e),
            )
    
    def get_backend_for_platform(self, db: Session, platform: str) -> Optional[Dict]:
        """Get the backend endpoint that manages a specific platform."""
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.platform == platform,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True,
        ).first()
        
        if backend:
            return {
                "name": backend.name,
                "platform": backend.platform,
                "api_url": backend.api_url,
                "auth_token": backend.auth_token,
                "is_default": backend.is_default,
                "skip_tls_verify": backend.skip_tls_verify,
            }
        return None
    
    def get_local_platform(self) -> str:
        """Get the platform this backend manages."""
        return os.getenv("BACKEND_PLATFORM", "default-platform")
    
    def is_local_platform(self, platform: str) -> bool:
        """Check if a platform is managed by this backend."""
        return platform == self.get_local_platform()
    
    async def forward_patch_to_backend(
        self,
        backend: Dict,
        namespace: str,
        name: str,
        kind: str,
        new_image: str,
        container_name: Optional[str] = None,
        source_image: Optional[str] = None,
        job_id: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Forward a patch request to a remote backend.

        Returns (success, old_image, error, patched_container) — the remote resolves the
        container from source_image, so it reports back which one it patched.
        
        If source_image is provided, the remote backend will only patch
        containers whose image matches the source_image repository.
        """
        try:
            import ssl
            
            ssl_context = None
            if backend.get("skip_tls_verify"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
            
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"{backend['api_url'].rstrip('/')}/api/image-updates/patch-resource"
                headers = {
                    "Authorization": f"Bearer {backend['auth_token']}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "namespace": namespace,
                    "name": name,
                    "kind": kind,
                    "new_image": new_image,
                    "container_name": container_name,
                    "source_image": source_image,
                    "job_id": job_id,
                }
                
                # Remote applies the patch then returns immediately (enrichment is
                # backgrounded on the remote), so this is normally ~1-2s. Use the
                # admin-configurable Proxy Timeout (Settings) so it's tunable, not hardcoded.
                async with session.post(url, json=payload, headers=headers,
                                        timeout=_proxy_timeout_seconds(60)) as resp:
                    data = await resp.json()
                    patched_container = data.get("container")

                    if resp.status == 200 and data.get("success"):
                        return True, data.get("old_image"), None, patched_container
                    else:
                        return False, data.get("old_image"), data.get("error", "Remote patch failed"), patched_container
                        
        except Exception as e:
            # asyncio.TimeoutError (and some aiohttp errors) stringify to "", which left the
            # UI showing a bare "Failed to forward to backend X:" — include the exception type.
            err_detail = str(e) or e.__class__.__name__
            log_event(
                "image_update.forward.error",
                backend=backend["name"],
                namespace=namespace,
                resource=name,
                error=err_detail,
            )
            return False, None, f"Failed to forward to backend {backend['name']}: {err_detail}", None
    
    async def forward_field_edits_to_backend(
        self,
        backend: Dict,
        namespace: str,
        name: str,
        kind: str,
        field_edits: list,
        new_image: Optional[str] = None,
        container_name: Optional[str] = None,
        allow_helm_managed: bool = False,
        job_id: Optional[int] = None,
    ) -> dict:
        """Forward a field-edit (Update Product) patch to the owning remote backend. The remote
        applies the edits via apply_resource_changes and stores its OWN pre-patch snapshot
        (kept local — never returned). Returns:
        {success, old_image, error, container, field_changes, helm_managed}."""
        base = {"success": False, "old_image": None, "error": None, "container": container_name,
                "field_changes": [], "helm_managed": False}
        try:
            import ssl
            ssl_context = None
            if backend.get("skip_tls_verify"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"{backend['api_url'].rstrip('/')}/api/image-updates/patch-resource"
                headers = {"Authorization": f"Bearer {backend['auth_token']}",
                           "Content-Type": "application/json"}
                payload = {
                    "namespace": namespace, "name": name, "kind": kind,
                    "new_image": new_image or None, "container_name": container_name,
                    "source_image": None, "job_id": job_id,
                    "field_edits": field_edits, "helm_managed_ack": allow_helm_managed,
                }
                async with session.post(url, json=payload, headers=headers,
                                        timeout=_proxy_timeout_seconds(60)) as resp:
                    data = await resp.json()
                    base["container"] = data.get("container") or container_name
                    if resp.status == 200 and data.get("success"):
                        base.update(success=True, old_image=data.get("old_image"),
                                    field_changes=data.get("field_changes") or [],
                                    helm_managed=bool(data.get("helm_managed")))
                    else:
                        base["old_image"] = data.get("old_image")
                        base["helm_managed"] = bool(data.get("helm_managed"))
                        base["error"] = data.get("error", "Remote field-edit failed")
            return base
        except Exception as e:
            err = str(e) or e.__class__.__name__
            log_event("image_update.forward_field_edits.error", backend=backend["name"],
                      namespace=namespace, resource=name, error=err)
            base["error"] = f"Failed to forward field edits to {backend['name']}: {err}"
            return base

    async def forward_health_check_to_backend(
        self,
        backend: Dict,
        namespace: str,
        name: str,
        kind: str,
        pre_patch_state: Optional[Dict] = None,
        timeout_seconds: int = 300,
        crash_tolerance: int = 120,
    ) -> Tuple[bool, Optional[Dict], str]:
        """Forward a Smart Watch health check request to a remote backend."""
        try:
            import ssl

            ssl_context = None
            if backend.get("skip_tls_verify"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None

            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"{backend['api_url'].rstrip('/')}/api/image-updates/health-check"
                headers = {
                    "Authorization": f"Bearer {backend['auth_token']}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "namespace": namespace,
                    "name": name,
                    "kind": kind,
                    "pre_patch_state": pre_patch_state,
                    "timeout_seconds": timeout_seconds,
                    "crash_tolerance": crash_tolerance,
                }
                
                # Use longer timeout for health check as it may wait for rollout
                async with session.post(url, json=payload, headers=headers, timeout=timeout_seconds + 30) as resp:
                    data = await resp.json()
                    
                    if resp.status == 200:
                        return (
                            data.get("health_passed", False),
                            data.get("post_state"),
                            data.get("message", "Health check completed"),
                        )
                    else:
                        return False, None, data.get("error", f"Remote health check failed: {resp.status}")
                        
        except asyncio.TimeoutError:
            return False, None, f"Health check timeout on remote backend {backend['name']}"
        except Exception as e:
            log_event(
                "image_update.forward_health_check.error",
                backend=backend["name"],
                namespace=namespace,
                resource=name,
                error=str(e),
            )
            return False, None, f"Failed to forward health check to {backend['name']}: {str(e)}"
    
    async def forward_rollback_to_backend(
        self,
        backend: Dict,
        namespace: str,
        name: str,
        kind: str,
        old_image: str,
        container_name: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Forward a rollback (patch to old image) to a remote backend.
        
        Returns:
            Tuple of (success, error_message)
        """
        success, _, error, _ = await self.forward_patch_to_backend(
            backend=backend,
            namespace=namespace,
            name=name,
            kind=kind,
            new_image=old_image,
            container_name=container_name,
        )
        return success, error

    async def forward_restore_to_backend(
        self, backend: Dict, job_id: int, namespace: str, name: str, kind: str,
    ) -> Tuple[bool, Optional[str]]:
        """Tell the OWNING backend to restore its own pre-patch snapshot for a field-edit job
        (snapshots never leave that backend). Returns (success, error)."""
        try:
            import ssl
            ssl_context = None
            if backend.get("skip_tls_verify"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"{backend['api_url'].rstrip('/')}/api/image-updates/restore-resource"
                headers = {"Authorization": f"Bearer {backend['auth_token']}",
                           "Content-Type": "application/json"}
                payload = {"job_id": job_id, "namespace": namespace, "name": name, "kind": kind}
                async with session.post(url, json=payload, headers=headers,
                                        timeout=_proxy_timeout_seconds(60)) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("success"):
                        return True, None
                    return False, data.get("error", f"Remote restore failed ({resp.status})")
        except Exception as e:
            err = str(e) or e.__class__.__name__
            log_event("image_update.forward_restore.error", backend=backend["name"],
                      namespace=namespace, resource=name, error=err)
            return False, f"Failed to forward restore to {backend['name']}: {err}"
    
    def execute_job(
        self,
        db: Session,
        job: ImageUpdateJob,
        monitor_rollout: bool = True,
        stuck_detection: int = 300,
        crash_tolerance: int = 120,
        batch_commit_size: int = 10,
        patch_batch_size: int = 0,
        patch_batch_pause: int = 2,
    ) -> Dict:
        """Execute an image update job with Smart Watch monitoring.

        Args:
            db: Database session
            job: The job to execute
            monitor_rollout: If True, Smart Watch monitors all patched resources
            stuck_detection: Seconds before auto-rollback for stuck (not ready) pods
            crash_tolerance: Seconds to tolerate CrashLoopBackOff/ImagePullBackOff
            batch_commit_size: Commit to DB every N resources
            patch_batch_size: Resources per batch (0 = no batching)
            patch_batch_pause: Seconds to pause between batches
        """
        if job.status not in ["approved", "pending_approval", "executing"]:
            if job.status == "pending_approval" and not job.approval_required:
                pass
            else:
                return {
                    "success": False,
                    "error": f"Job cannot be executed in status: {job.status}",
                }

        if job.status != "executing":
            job.status = "executing"
            job.started_at = datetime.now(timezone.utc)
            job.progress_current = 0
            job.progress_total = 1
            job.progress_message = "Initializing job..."
            db.commit()

        log_event("image_update.job.start", job_id=job.id, product=job.product_name)

        try:
            return self._execute_job_internal(
                db, job, monitor_rollout=monitor_rollout,
                stuck_detection=stuck_detection, crash_tolerance=crash_tolerance,
                batch_commit_size=batch_commit_size,
                patch_batch_size=patch_batch_size, patch_batch_pause=patch_batch_pause,
            )
        except Exception as e:
            log_event("image_update.job.exception", job_id=job.id, error=str(e))
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.notes = (job.notes or "") + f"\n[Execution Error: {str(e)}]"
            db.commit()
            return {"success": False, "error": str(e)}
    
    def _update_progress(self, db: Session, job: ImageUpdateJob, current: int, total: int, message: str):
        """Update job progress in database."""
        job.progress_current = current
        job.progress_total = max(total, 1)  # Ensure total is at least 1
        job.progress_message = message
        job.progress_updated_at = datetime.now(timezone.utc)
        db.commit()
        
        log_event(
            "image_update.job.progress_update",
            job_id=job.id,
            current=current,
            total=job.progress_total,
            message=message[:50] + "..." if len(message) > 50 else message,
        )
    
    def _check_cancel_requested(self, db: Session, job: ImageUpdateJob) -> bool:
        """Check if cancellation was requested. Refreshes job from DB."""
        db.refresh(job)
        return job.cancel_requested
    
    def _execute_job_internal(
        self,
        db: Session,
        job: ImageUpdateJob,
        monitor_rollout: bool = True,
        stuck_detection: int = 300,
        crash_tolerance: int = 120,
        batch_commit_size: int = 10,
        patch_batch_size: int = 0,
        patch_batch_pause: int = 2,
    ) -> Dict:
        """Internal job execution logic with Smart Watch monitoring."""
        
        # Parse target filters
        target_platforms = None
        target_namespaces = None
        target_resources = None
        
        if job.target_platforms:
            target_platforms = json.loads(job.target_platforms)
        if job.target_namespaces:
            target_namespaces = json.loads(job.target_namespaces)
        if job.target_resources:
            target_resources = json.loads(job.target_resources)
        # Phase 1: structured field edits. Empty/None => image-only job, and the existing
        # per-container image path below runs UNCHANGED. Non-empty => the field-edit path.
        job_field_edits = json.loads(job.field_edits) if job.field_edits else []

        # Update progress immediately with target info
        platforms_str = ", ".join(target_platforms) if target_platforms else "all"
        self._update_progress(db, job, 0, 1, f"Analyzing targets: {platforms_str}...")
        
        # Identify remote platforms from resource keys
        # Resource key format: "platform|namespace|kind|resource_name"
        remote_resource_keys_by_platform: Dict[str, List[Dict]] = {}
        local_platform = self.get_local_platform()
        
        if target_resources and isinstance(target_resources[0], str) and '|' in target_resources[0]:
            # Parse resource keys to identify remote platforms
            for key in target_resources:
                parts = key.split('|')
                if len(parts) == 4:
                    platform, namespace, kind, resource_name = parts
                    if platform != local_platform:
                        # This is a remote platform resource
                        if platform not in remote_resource_keys_by_platform:
                            remote_resource_keys_by_platform[platform] = []
                        remote_resource_keys_by_platform[platform].append({
                            'platform': platform,
                            'namespace': namespace,
                            'kind': kind,
                            'resource_name': resource_name,
                        })
            
            log_event(
                "image_update.job.remote_resources_detected",
                job_id=job.id,
                remote_platforms=list(remote_resource_keys_by_platform.keys()),
                remote_resource_count=sum(len(v) for v in remote_resource_keys_by_platform.values()),
            )
        
        # Identify non-local platforms that need full job forwarding.
        # This covers both: (a) no specific resources at all, and
        # (b) retry scenarios where forwarding failed — those platforms
        # won't have entries in remote_resource_keys_by_platform.
        remote_platforms_for_forwarding: List[str] = []
        if target_platforms:
            for platform in target_platforms:
                if platform != local_platform and platform not in remote_resource_keys_by_platform:
                    remote_platforms_for_forwarding.append(platform)
            
            if remote_platforms_for_forwarding:
                log_event(
                    "image_update.job.remote_platforms_for_forwarding",
                    job_id=job.id,
                    platforms=remote_platforms_for_forwarding,
                    reason="target_platforms includes non-local platforms without specific resource keys",
                )
        
        # Get matching LOCAL resources only
        resources = self.get_matching_resources(
            db,
            job.product_name,
            target_platforms,
            target_namespaces,
            target_resources,
        )
        
        # Pre-filter by source image:tag to avoid processing non-matching resources
        _pre_source_tag = None
        _pre_source_repo = None
        if job.source_image:
            try:
                from .versioning import parse_image as _pi
                if ':' in job.source_image.split('/')[-1]:
                    _raw, _pre_source_tag = job.source_image.rsplit(':', 1)
                else:
                    _raw = job.source_image
                _pp = _pi(_raw + ":dummy")
                _pre_source_repo = _pp.repository.lower() if _pp else _raw.lower()
            except Exception:
                _pre_source_repo = job.source_image.lower().split(':')[0]

        if _pre_source_repo:
            before_count = len(resources)
            filtered = []
            for r in resources:
                has_match = False
                if r.security_info and r.security_info.get("containers"):
                    for c in r.security_info["containers"]:
                        ci = c.get("image", "")
                        if not ci:
                            continue
                        cr = ci.split(':')[0].lower()
                        if '/' not in cr:
                            cr = 'docker.io/library/' + cr
                        elif '.' not in cr.split('/')[0]:
                            cr = 'docker.io/' + cr
                        if cr == _pre_source_repo or cr.endswith('/' + _pre_source_repo.split('/')[-1]):
                            if _pre_source_tag:
                                ct = ci.split(':')[-1] if ':' in ci else ''
                                if ct != _pre_source_tag:
                                    continue
                            has_match = True
                            break
                elif r.image:
                    cr = r.image.split(':')[0].lower()
                    if cr == _pre_source_repo:
                        if _pre_source_tag:
                            ct = r.image.split(':')[-1] if ':' in r.image else ''
                            has_match = ct == _pre_source_tag
                        else:
                            has_match = True
                if has_match:
                    filtered.append(r)
            resources = filtered
            log_event("image_update.pre_filter",
                     source_repo=_pre_source_repo,
                     source_tag=_pre_source_tag,
                     before=before_count,
                     after=len(resources))
        
        # Group LOCAL resources by platform
        resources_by_platform: Dict[str, List] = {}
        for r in resources:
            if r.platform not in resources_by_platform:
                resources_by_platform[r.platform] = []
            resources_by_platform[r.platform].append(r)
        
        log_event(
            "image_update.job.platforms",
            job_id=job.id,
            local_resources_platforms=list(resources_by_platform.keys()),
            remote_platforms=list(remote_resource_keys_by_platform.keys()),
            local_platform=local_platform,
        )
        
        results = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "rolled_back": 0,
            "details": [],
            "by_backend": {},
        }
        
        # Track resources that need health check (group patches by resource)
        pending_health_checks: List[Dict] = []
        
        # Calculate total resources for progress logging (local + remote)
        local_resource_count = sum(len(res_list) for res_list in resources_by_platform.values())
        remote_resource_count = sum(len(res_list) for res_list in remote_resource_keys_by_platform.values())
        remote_forwarding_count = len(remote_platforms_for_forwarding)  # Platforms to forward to
        total_resources = local_resource_count + remote_resource_count
        processed_count = 0
        
        # Initialize progress tracking
        if total_resources > 0:
            self._update_progress(db, job, 0, total_resources, f"Starting job with {total_resources} resources ({local_resource_count} local, {remote_resource_count} remote)")
        elif remote_forwarding_count > 0:
            # For remote platform forwarding, show backend count as progress
            self._update_progress(db, job, 0, remote_forwarding_count, f"Forwarding to {remote_forwarding_count} remote backend(s)...")
        else:
            self._update_progress(db, job, 0, 1, "Starting job...")
        
        if total_resources > 100:
            log_event(
                "image_update.job.warning.large_job",
                job_id=job.id,
                total_resources=total_resources,
                message="Large job detected. Consider batching updates or disabling health checks."
            )
        
        # Process each platform
        for platform, platform_resources in resources_by_platform.items():
            is_local = self.is_local_platform(platform)
            backend = None if is_local else self.get_backend_for_platform(db, platform)
            
            backend_name = "local" if is_local else (backend["name"] if backend else "unknown")
            results["by_backend"][backend_name] = {"success": 0, "failed": 0, "skipped": 0, "rolled_back": 0}
            
            # Parse target image to get repository for matching
            target_parsed = None
            target_repo = None
            source_repo = None
            source_tag = None
            try:
                from .versioning import parse_image
                target_parsed = parse_image(job.target_image)
                if target_parsed:
                    target_repo = target_parsed.repository.lower()
                
                if job.source_image:
                    if ':' in job.source_image.split('/')[-1]:
                        raw_repo, source_tag = job.source_image.rsplit(':', 1)
                    else:
                        raw_repo = job.source_image
                    source_parsed = parse_image(raw_repo + ":dummy")
                    if source_parsed:
                        source_repo = source_parsed.repository.lower()
                    else:
                        source_repo = raw_repo.lower()
            except Exception:
                pass
            
            if job.source_image and not source_repo:
                parts = job.source_image.lower().split('/')
                if len(parts) >= 2 and ('.' in parts[0] or ':' in parts[0]):
                    source_repo = '/'.join(parts[1:]).split(':')[0]
                else:
                    source_repo = job.source_image.lower().split(':')[0]
            
            log_event("image_update.filter.repos", 
                     source_repo=source_repo,
                     source_tag=source_tag,
                     target_repo=target_repo,
                     job_source_image=job.source_image)
            
            for r in platform_resources:
                # Update progress with detailed info (resource level)
                processed_count += 1
                progress_detail = f"[{platform}] {r.namespace}/{r.kind}/{r.resource_name}"
                self._update_progress(db, job, processed_count, total_resources, progress_detail)
                
                # Build execution log for this resource
                execution_log = []
                
                def add_log(level: str, message: str):
                    execution_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": level,
                        "message": message,
                    })
                
                add_log("info", f"Processing resource {r.resource_name} ({r.kind})")
                
                pre_patch_state = None
                if is_local and monitor_rollout:
                    pre_patch_state = self.get_resource_state(r.namespace, r.resource_name, r.kind)
                    add_log("info", f"Pre-patch state captured: {pre_patch_state.get('ready_replicas', 'N/A')} ready replicas")
                
                # Get containers to update
                containers_to_update = []
                
                if r.security_info and r.security_info.get("containers"):
                    for c in r.security_info["containers"]:
                        container_image = c.get("image", "")
                        
                        container_repo = None
                        if container_image:
                            try:
                                container_parsed = parse_image(container_image)
                                if container_parsed:
                                    container_repo = container_parsed.repository.lower()
                            except Exception:
                                container_repo = container_image.split(':')[0].lower()
                        
                        if source_repo and container_repo:
                            if container_repo != source_repo:
                                add_log("debug", f"Container {c.get('name')} filtered: source repo mismatch")
                                continue
                            if source_tag:
                                container_tag = container_image.split(':')[-1] if ':' in container_image else ''
                                if container_tag != source_tag:
                                    add_log("debug", f"Container {c.get('name')} filtered: tag {container_tag} != {source_tag}")
                                    continue
                        elif target_repo and container_repo:
                            if container_repo != target_repo:
                                add_log("debug", f"Container {c.get('name')} filtered: target mismatch")
                                continue
                        elif container_image and not _image_matches_product(container_image, job.product_name):
                            add_log("debug", f"Container {c.get('name')} filtered: image doesn't match product {job.product_name}")
                            continue
                        
                        add_log("info", f"Container {c.get('name')} matched for update")
                        containers_to_update.append({
                            "name": c.get("name"),
                            "current_image": container_image,
                        })
                else:
                    if not r.image or not _image_matches_product(r.image, job.product_name):
                        add_log("warning", f"Skipping {r.resource_name}: no container info and image doesn't match product {job.product_name}")
                        continue
                    
                    single_repo = r.image.split(':')[0].lower()
                    should_include = True
                    if source_repo and single_repo:
                        should_include = single_repo == source_repo
                        if should_include and source_tag:
                            img_tag = r.image.split(':')[-1] if ':' in r.image else ''
                            should_include = img_tag == source_tag
                    elif target_repo and single_repo:
                        should_include = single_repo == target_repo
                    
                    if should_include:
                        containers_to_update.append({
                            "name": None,
                            "current_image": r.image,
                        })
                
                # Track if any container in this resource was patched
                resource_patched = False
                patched_containers = []
                resource_pre_patch_manifest = None  # set by the field-edit path (rollback source)

                for container in containers_to_update:
                    results["total"] += 1
                    container_name = container.get("name")
                    current_image = container.get("current_image")
                    
                    # Update progress with container detail
                    container_display = container_name or "main"
                    progress_detail = f"[{platform}] {r.namespace}/{r.kind}/{r.resource_name} → {container_display}"
                    self._update_progress(db, job, processed_count, total_resources, progress_detail)
                    
                    # Check for cancellation before each container patch
                    if self._check_cancel_requested(db, job):
                        log_event("image_update.job.cancelled_by_user", job_id=job.id, at_container=container_display)
                        job.status = "cancelled"
                        job.finished_at = datetime.now(timezone.utc)
                        job.cancelled_at = datetime.now(timezone.utc)
                        job.progress_message = f"Cancelled at {r.resource_name}/{container_display}"
                        job.notes = (job.notes or "") + f"\n[Cancelled by user at {r.resource_name}/{container_display}]"
                        db.commit()
                        return {"success": False, "error": "Cancelled by user", "results": results}
                    
                    # Skip if image is already the target — but NOT for field-edit jobs,
                    # which still have manifest changes to apply even when the image matches.
                    if current_image == job.target_image and not job_field_edits:
                        add_log("info", f"Container {container_name or 'main'} skipped: already at target")
                        result = ImageUpdateResult(
                            job_id=job.id,
                            platform=r.platform,
                            namespace=r.namespace,
                            resource_name=r.resource_name,
                            kind=r.kind,
                            container_name=container_name,
                            old_image=current_image,
                            new_image=job.target_image,
                            status="skipped",
                            error_message="Image already at target version",
                            pre_patch_state=json.dumps(pre_patch_state) if pre_patch_state else None,
                            execution_log=json.dumps(execution_log),
                        )
                        db.add(result)
                        results["skipped"] += 1
                        results["by_backend"][backend_name]["skipped"] += 1
                        continue
                    
                    add_log("info", f"Patching {container_name or 'main'}: {current_image} -> {job.target_image}")
                    
                    # Execute the patch
                    field_apply = None
                    if is_local and job_field_edits:
                        # Field-edit ("Update Product") path: image + all field edits in ONE
                        # read-modify-write, with the extra Helm-managed acknowledgment gate.
                        field_apply = self.apply_resource_changes(
                            namespace=r.namespace,
                            name=r.resource_name,
                            kind=r.kind,
                            new_image=job.target_image,
                            field_edits=job_field_edits,
                            container_name=container_name,
                            allow_helm_managed=job.helm_managed_ack,
                        )
                        success = field_apply["success"]
                        old_image = field_apply["old_image"]
                        error = field_apply["error"]
                        if field_apply.get("pre_patch_manifest"):
                            resource_pre_patch_manifest = field_apply["pre_patch_manifest"]
                    elif is_local:
                        success, old_image, error = self.patch_resource_image(
                            namespace=r.namespace,
                            name=r.resource_name,
                            kind=r.kind,
                            new_image=job.target_image,
                            container_name=container_name,
                        )
                    elif backend and job_field_edits:
                        # Remote field edits need the federation forward extension (Phase 1.4).
                        success = False
                        old_image = current_image
                        error = "Field edits on remote backends arrive in Phase 1.4"
                    elif backend:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            success, old_image, error, patched_container = loop.run_until_complete(
                                self.forward_patch_to_backend(
                                    backend=backend,
                                    namespace=r.namespace,
                                    name=r.resource_name,
                                    kind=r.kind,
                                    new_image=job.target_image,
                                    container_name=container_name,
                                    source_image=job.source_image or job.target_image,
                                    job_id=job.id,
                                )
                            )
                            # First path iterates containers locally, but if it didn't know
                            # the name (source_image filtering), use what the remote resolved.
                            if not container_name and patched_container:
                                container_name = patched_container
                        finally:
                            loop.close()
                    else:
                        success = False
                        old_image = current_image
                        error = f"No backend found for platform: {platform}"
                    
                    # Ensure error is never None for failed patches
                    if not success and not error:
                        error = "Unknown error occurred during patch"
                    
                    if success:
                        add_log("info", f"Patch successful for {container_name or 'main'}")
                        resource_patched = True
                        patched_containers.append({
                            "name": container_name,
                            "old_image": old_image or current_image,
                        })
                    else:
                        # Log detailed error information
                        error_detail = error or "Unknown error"
                        add_log("error", f"❌ PATCH FAILED for {container_name or 'main'}")
                        add_log("error", f"   Resource: {r.resource_name} ({r.kind})")
                        add_log("error", f"   Namespace: {r.namespace}")
                        add_log("error", f"   Error: {error_detail}")
                        
                        # Log to backend as well for debugging
                        log_event(
                            "image_update.patch.failed_detail",
                            job_id=job.id,
                            resource=r.resource_name,
                            namespace=r.namespace,
                            kind=r.kind,
                            container=container_name,
                            error=error_detail,
                        )
                    
                    # Create result record (will be updated after health check)
                    result = ImageUpdateResult(
                        job_id=job.id,
                        platform=r.platform,
                        namespace=r.namespace,
                        resource_name=r.resource_name,
                        kind=r.kind,
                        container_name=container_name,
                        old_image=old_image or current_image,
                        new_image=job.target_image,
                        status="success" if success else "failed",
                        error_message=error if not success else None,  # Only store error for failures
                        pre_patch_state=json.dumps(pre_patch_state) if pre_patch_state else None,
                        execution_log=json.dumps(execution_log),
                    )
                    db.add(result)

                    # Phase 1: persist field-edit extras. pre_patch_manifest is the encrypted
                    # restore-based-rollback source; field_changes/helm_managed are audit.
                    if field_apply is not None:
                        result.pre_patch_manifest = field_apply.get("pre_patch_manifest")
                        result.field_changes = json.dumps(field_apply.get("field_changes") or [])
                        result.helm_managed = bool(field_apply.get("helm_managed"))

                    # Note: Don't increment processed_count here - it's already incremented per resource
                    # This loop is for containers within a resource
                    
                    # Batch commit for large jobs (every N results)
                    total_results = results["success"] + results["failed"] + results["skipped"]
                    if total_results % batch_commit_size == 0:
                        db.flush()
                        log_event(
                            "image_update.job.progress",
                            job_id=job.id,
                            processed=processed_count,
                            total=total_resources,
                            percent=int((processed_count / total_resources) * 100),
                            success=results["success"],
                            failed=results["failed"],
                        )
                    
                    if success:
                        results["success"] += 1
                        results["by_backend"][backend_name]["success"] += 1
                        self._update_resource_after_patch(db, r, container_name, job.target_image, job_id=job.id)
                    else:
                        results["failed"] += 1
                        results["by_backend"][backend_name]["failed"] += 1
                    
                    results["details"].append({
                        "platform": r.platform,
                        "backend": backend_name,
                        "namespace": r.namespace,
                        "resource": r.resource_name,
                        "kind": r.kind,
                        "container": container_name,
                        "old_image": old_image or current_image,
                        "new_image": job.target_image,
                        "status": "success" if success else "failed",
                        "error": error,
                    })

                    # Field-edit jobs apply the image + ALL field edits in one read-modify-write,
                    # so it runs once per resource — don't re-apply for any further containers.
                    if job_field_edits:
                        break

                if resource_patched and monitor_rollout:
                    pending_health_checks.append({
                        "resource": r,
                        "pre_patch_state": pre_patch_state,
                        "patched_containers": patched_containers,
                        "execution_log": execution_log,
                        "backend_name": backend_name,
                        "is_local": is_local,
                        "backend": backend,
                        # Phase 1: encrypted snapshot → restore-based rollback (field-edit jobs).
                        "pre_patch_manifest": resource_pre_patch_manifest,
                    })
                
                if (patch_batch_size > 0
                        and processed_count % patch_batch_size == 0
                        and processed_count < total_resources):
                    batch_num = processed_count // patch_batch_size

                    # Smart Watch this batch before moving to the next
                    if monitor_rollout and pending_health_checks:
                        batch_watch_count = len(pending_health_checks)
                        self._update_progress(
                            db, job, processed_count, total_resources,
                            f"Batch {batch_num}: Smart Watch monitoring {batch_watch_count} resources..."
                        )
                        db.commit()
                        ipf = self.smart_watch_monitor(
                            db, job, pending_health_checks, results,
                            stuck_detection=stuck_detection,
                            crash_tolerance=crash_tolerance,
                        )
                        pending_health_checks.clear()

                        if ipf:
                            error_msg = f"Image pull failed after batch {batch_num}. Verify target image exists and is accessible: {job.target_image}"
                            log_event("image_update.job.image_pull_abort", job_id=job.id, batch=batch_num)
                            job.status = "failed"
                            job.finished_at = datetime.now(timezone.utc)
                            job.progress_message = error_msg
                            db.commit()
                            return {"success": False, "error": error_msg, "results": results}

                        if self._check_cancel_requested(db, job):
                            job.status = "cancelled"
                            job.finished_at = datetime.now(timezone.utc)
                            job.cancelled_at = datetime.now(timezone.utc)
                            job.progress_message = f"Cancelled after batch {batch_num}"
                            db.commit()
                            return {"success": False, "error": "Cancelled by user", "results": results}

                    if patch_batch_pause > 0 and not monitor_rollout:
                        self._update_progress(
                            db, job, processed_count, total_resources,
                            f"Batch {batch_num} done ({processed_count}/{total_resources}). Pausing {patch_batch_pause}s..."
                        )
                        import time
                        time.sleep(patch_batch_pause)
        
        # Smart Watch: monitor remaining resources from the last (incomplete) batch
        if pending_health_checks:
            db.commit()
            ipf = self.smart_watch_monitor(
                db, job, pending_health_checks, results,
                stuck_detection=stuck_detection,
                crash_tolerance=crash_tolerance,
            )
            if ipf:
                error_msg = f"Image pull failed. Verify target image exists and is accessible: {job.target_image}"
                log_event("image_update.job.image_pull_abort", job_id=job.id)
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.progress_message = error_msg
                db.commit()
                return {"success": False, "error": error_msg, "results": results}

        db.commit()
        
        # Process remote platform resources (forward to remote backends)
        remote_health_checks: List[Dict] = []
        for remote_platform, remote_resources in remote_resource_keys_by_platform.items():
            backend = self.get_backend_for_platform(db, remote_platform)
            if not backend:
                log_event(
                    "image_update.job.remote_backend_not_found",
                    job_id=job.id,
                    platform=remote_platform,
                )
                for res_info in remote_resources:
                    results["total"] += 1
                    results["failed"] += 1
                    result = ImageUpdateResult(
                        job_id=job.id,
                        platform=remote_platform,
                        namespace=res_info["namespace"],
                        resource_name=res_info["resource_name"],
                        kind=res_info["kind"],
                        container_name=None,
                        old_image=None,
                        new_image=job.target_image,
                        status="failed",
                        error_message=f"Backend not found for platform: {remote_platform}",
                    )
                    db.add(result)
                continue
            
            backend_name = backend.get("name", remote_platform)
            if backend_name not in results["by_backend"]:
                results["by_backend"][backend_name] = {"success": 0, "failed": 0, "skipped": 0, "rolled_back": 0}
            
            for res_info in remote_resources:
                processed_count += 1
                container_name = res_info.get('container_name')
                container_display = container_name or 'all'
                progress_detail = f"[{remote_platform}] {res_info['namespace']}/{res_info['kind']}/{res_info['resource_name']} → {container_display} (remote)"
                self._update_progress(db, job, processed_count, total_resources, progress_detail)
                
                # Check for cancellation
                if self._check_cancel_requested(db, job):
                    log_event("image_update.job.cancelled_by_user", job_id=job.id, at_resource=res_info['resource_name'])
                    job.status = "cancelled"
                    job.finished_at = datetime.now(timezone.utc)
                    job.cancelled_at = datetime.now(timezone.utc)
                    db.commit()
                    return {"success": False, "error": "Cancelled by user", "results": results}
                
                results["total"] += 1
                exec_log = []
                
                exec_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "info",
                    "message": f"Forwarding patch to remote backend {backend_name}",
                })
                
                # Forward patch to remote backend with specific container
                # Pass source_image so remote backend can filter containers
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _fe_field_changes, _fe_helm = None, None
                try:
                    if job_field_edits:
                        # Field-edit (Update Product) forward: the owning remote applies the
                        # edits and keeps its OWN snapshot for restore-based rollback.
                        fe = loop.run_until_complete(
                            self.forward_field_edits_to_backend(
                                backend=backend,
                                namespace=res_info["namespace"],
                                name=res_info["resource_name"],
                                kind=res_info["kind"],
                                field_edits=job_field_edits,
                                new_image=(job.target_image or None),
                                container_name=container_name,
                                allow_helm_managed=job.helm_managed_ack,
                                job_id=job.id,
                            )
                        )
                        success, old_image, error = fe["success"], fe["old_image"], fe["error"]
                        patched_container = fe.get("container")
                        _fe_field_changes, _fe_helm = fe.get("field_changes"), fe.get("helm_managed")
                    else:
                        success, old_image, error, patched_container = loop.run_until_complete(
                            self.forward_patch_to_backend(
                                backend=backend,
                                namespace=res_info["namespace"],
                                name=res_info["resource_name"],
                                kind=res_info["kind"],
                                new_image=job.target_image,
                                container_name=container_name,
                                source_image=job.source_image or job.target_image,
                                job_id=job.id,
                            )
                        )
                finally:
                    loop.close()

                # The remote resolves the container from source_image; surface which one it
                # patched so the result + Smart Watch tracker show it (was blank "-" before).
                if not container_name and patched_container:
                    container_name = patched_container

                if success:
                    if job_field_edits:
                        _ed = len(_fe_field_changes) if _fe_field_changes else len(job_field_edits)
                        _msg = f"Manifest edits applied ({_ed})" + (
                            f"; image {old_image} -> {job.target_image}" if job.target_image else "")
                    else:
                        _msg = f"Patch successful: {old_image} -> {job.target_image}"
                    exec_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": "info",
                        "message": _msg,
                    })
                    
                    results["success"] += 1
                    results["by_backend"][backend_name]["success"] += 1
                    result = ImageUpdateResult(
                        job_id=job.id,
                        platform=remote_platform,
                        namespace=res_info["namespace"],
                        resource_name=res_info["resource_name"],
                        kind=res_info["kind"],
                        container_name=container_name,
                        old_image=old_image,
                        new_image=job.target_image,
                        status="success",
                        execution_log=json.dumps(exec_log),
                        # Field-edit audit; pre_patch_manifest stays on the OWNING remote (not here).
                        field_changes=json.dumps(_fe_field_changes) if _fe_field_changes else None,
                        helm_managed=_fe_helm,
                    )
                else:
                    exec_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": "error",
                        "message": f"Patch failed: {error}",
                    })
                    results["failed"] += 1
                    results["by_backend"][backend_name]["failed"] += 1
                    result = ImageUpdateResult(
                        job_id=job.id,
                        platform=remote_platform,
                        namespace=res_info["namespace"],
                        resource_name=res_info["resource_name"],
                        kind=res_info["kind"],
                        container_name=container_name,
                        old_image=old_image,
                        new_image=job.target_image,
                        status="failed",
                        error_message=error or "Remote patch failed",
                        execution_log=json.dumps(exec_log),
                    )
                
                db.add(result)
                db.commit()  # Commit after each resource for live progress

                # Queue this remote resource for Smart Watch. Health is monitored on the
                # OWNING backend (forwarded) and rollback is forwarded there too. Without
                # this the remote path returned success immediately and never waited for
                # the container to become Ready (Health column stayed "-").
                if success and monitor_rollout:
                    remote_health_checks.append({
                        "resource": types.SimpleNamespace(
                            namespace=res_info["namespace"],
                            resource_name=res_info["resource_name"],
                            kind=res_info["kind"],
                            platform=remote_platform,
                        ),
                        "pre_patch_state": None,  # not used by the remote branch
                        "patched_containers": [{"name": container_name, "old_image": old_image}],
                        "execution_log": exec_log,
                        "backend_name": backend_name,
                        "is_local": False,
                        "backend": backend,
                        # Field-edit jobs roll back via the remote's own snapshot (restore),
                        # not an old-image re-patch.
                        "field_edit": bool(job_field_edits),
                    })

                log_event(
                    "image_update.remote_patch",
                    job_id=job.id,
                    platform=remote_platform,
                    backend=backend_name,
                    resource=res_info["resource_name"],
                    container=container_name,
                    success=success,
                    health_passed=None,
                    error=error,
                )

        db.commit()

        # Smart Watch the remote resources we just patched: wait for rollout health on the
        # owning backend (forwarded) and roll back there on failure. Previously this remote
        # path skipped health monitoring entirely and finished instantly.
        if monitor_rollout and remote_health_checks:
            ipf = self.smart_watch_monitor(
                db, job, remote_health_checks, results,
                stuck_detection=stuck_detection, crash_tolerance=crash_tolerance,
            )
            remote_health_checks = []
            db.commit()
            if ipf:
                error_msg = f"Image pull failed on remote. Verify target image exists and is accessible: {job.target_image}"
                log_event("image_update.job.image_pull_abort", job_id=job.id)
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.progress_message = error_msg
                db.commit()
                return {"success": False, "error": error_msg, "results": results}

        # Process remote platforms that need full job forwarding (no specific resources selected)
        if remote_platforms_for_forwarding:
            # Set initial progress for remote forwarding
            self._update_progress(
                db, job, 0, len(remote_platforms_for_forwarding),
                f"Forwarding to {len(remote_platforms_for_forwarding)} remote backend(s)..."
            )
        
        for idx, remote_platform in enumerate(remote_platforms_for_forwarding):
            backend = self.get_backend_for_platform(db, remote_platform)
            if not backend:
                log_event(
                    "image_update.job.remote_backend_not_found_for_forwarding",
                    job_id=job.id,
                    platform=remote_platform,
                )
                # Record a single failure for missing backend
                results["total"] += 1
                results["failed"] += 1
                result = ImageUpdateResult(
                    job_id=job.id,
                    platform=remote_platform,
                    namespace="(all)",
                    resource_name="(product forwarding failed)",
                    kind="Unknown",
                    container_name=None,
                    old_image=None,
                    new_image=job.target_image,
                    status="failed",
                    error_message=f"Backend not found for platform: {remote_platform}",
                )
                db.add(result)
                continue
            
            backend_name = backend.get("name", remote_platform)
            if backend_name not in results["by_backend"]:
                results["by_backend"][backend_name] = {"success": 0, "failed": 0, "skipped": 0, "rolled_back": 0}
            
            log_event(
                "image_update.job.forwarding_to_backend",
                job_id=job.id,
                platform=remote_platform,
                backend=backend_name,
                product=job.product_name,
            )
            
            # Update progress for this backend
            self._update_progress(
                db, job, idx, len(remote_platforms_for_forwarding),
                f"Connecting to {backend_name}..."
            )
            
            # Forward job execution to remote backend
            try:
                import ssl
                import httpx
                
                ssl_context = None
                verify = True
                if backend.get("skip_tls_verify"):
                    verify = False
                
                base_url = backend['api_url'].rstrip('/')
                url = f"{base_url}/api/image-updates/execute-for-product"
                headers = {
                    "Authorization": f"Bearer {backend['auth_token']}",
                    "Content-Type": "application/json",
                }
                
                payload = {
                    "product_name": job.product_name,
                    "target_image": job.target_image,
                    "source_image": job.source_image,
                    "target_namespaces": target_namespaces,
                    "health_check_enabled": monitor_rollout,
                    "health_check_timeout": max(stuck_detection, crash_tolerance),
                    "crash_tolerance": crash_tolerance,
                    "auto_rollback": True,
                    "job_id": job.id,
                    "patch_batch_size": patch_batch_size,
                    "patch_batch_pause": patch_batch_pause,
                }
                
                log_event(
                    "image_update.job.forwarding_request",
                    job_id=job.id,
                    backend=backend_name,
                    url=url,
                    monitor_rollout=monitor_rollout,
                )
                
                # Step 1: Start async task on remote backend (quick response)
                import time
                with httpx.Client(verify=verify, timeout=30.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    
                    if resp.status_code != 200:
                        error_msg = f"Remote backend returned status {resp.status_code}: {resp.text[:200]}"
                        raise Exception(error_msg)
                    
                    data = resp.json()
                    
                    # Check if this is an async response (has task_id)
                    if data.get("async") and data.get("task_id"):
                        task_id = data["task_id"]
                        log_event(
                            "image_update.job.async_task_started",
                            job_id=job.id,
                            backend=backend_name,
                            task_id=task_id,
                        )
                        
                        # Update progress immediately
                        self._update_progress(
                            db, job, 0, 1,
                            f"[{backend_name}] Task started, waiting for resources..."
                        )
                        
                        # Step 2: Poll for results with adaptive timeout
                        # Instead of a fixed total timeout, reset the idle timer
                        # whenever progress advances. Only timeout if stuck.
                        status_url = f"{base_url}/api/image-updates/execute-for-product/{task_id}/status"
                        idle_timeout = max(stuck_detection, crash_tolerance) + 120
                        poll_interval = 1
                        idle_seconds = 0
                        last_remote_current = -1
                        
                        while idle_seconds < idle_timeout:
                            time.sleep(poll_interval)
                            idle_seconds += poll_interval
                            
                            status_resp = client.get(status_url, headers=headers)
                            if status_resp.status_code != 200:
                                log_event(
                                    "image_update.job.async_poll_error",
                                    job_id=job.id,
                                    backend=backend_name,
                                    task_id=task_id,
                                    status=status_resp.status_code,
                                )
                                self._update_progress(
                                    db, job, 0, 1,
                                    f"[{backend_name}] ⟳ Connecting... (idle {idle_seconds}s)"
                                )
                                continue
                            
                            status_data = status_resp.json()
                            task_status = status_data.get("status")
                            remote_progress = status_data.get("progress", "Processing...")
                            remote_current = status_data.get("current", 0)
                            remote_total_expected = max(status_data.get("total_expected", 1), 1)
                            
                            # Reset idle timer when progress advances
                            if remote_current != last_remote_current:
                                last_remote_current = remote_current
                                idle_seconds = 0
                            
                            progress_msg = f"[{backend_name}] {remote_progress}"
                            self._update_progress(db, job, remote_current, remote_total_expected, progress_msg)
                            
                            log_event(
                                "image_update.job.async_poll",
                                job_id=job.id,
                                backend=backend_name,
                                task_id=task_id,
                                task_status=task_status,
                                progress=remote_progress,
                                idle_seconds=idle_seconds,
                            )
                            
                            if task_status == "completed":
                                data = status_data.get("results", {})
                                remote_total = data.get("total", 0)
                                remote_success = data.get("success_count", 0)
                                self._update_progress(
                                    db, job, 1, 1,
                                    f"[{backend_name}] ✓ Completed: {remote_success}/{remote_total} success"
                                )
                                break
                            elif task_status == "failed":
                                raise Exception(f"Remote task failed: {status_data.get('error')}")
                        else:
                            raise Exception(f"Timeout waiting for remote task after {idle_timeout}s idle (no progress)")
                    
                    # Process results (either sync or async completed)
                    remote_total = data.get("total", 0)
                    remote_success = data.get("success_count", 0)
                    remote_failed = data.get("failed_count", 0)
                    remote_skipped = data.get("skipped_count", 0)
                    remote_rolled_back = data.get("rolled_back_count", 0)
                    
                    results["total"] += remote_total
                    results["success"] += remote_success
                    results["failed"] += remote_failed
                    results["skipped"] += remote_skipped
                    results["rolled_back"] += remote_rolled_back
                    results["by_backend"][backend_name]["success"] += remote_success
                    results["by_backend"][backend_name]["failed"] += remote_failed
                    results["by_backend"][backend_name]["skipped"] += remote_skipped
                    results["by_backend"][backend_name]["rolled_back"] += remote_rolled_back
                    
                    # Store individual results from remote backend
                    for detail in data.get("details", []):
                        # Determine health_check_passed from detail or infer from status
                        health_passed = detail.get("health_passed")
                        if health_passed is None:
                            # Infer from status if not explicitly set
                            health_passed = detail.get("status") == "success"
                        
                        result = ImageUpdateResult(
                            job_id=job.id,
                            platform=remote_platform,
                            namespace=detail.get("namespace"),
                            resource_name=detail.get("resource"),
                            kind=detail.get("kind"),
                            container_name=detail.get("container"),
                            old_image=detail.get("old_image"),
                            new_image=job.target_image,
                            status=detail.get("status"),
                            error_message=detail.get("error"),
                            health_check_passed=health_passed,
                            health_check_message=detail.get("health_message"),
                        )
                        db.add(result)
                    
                    log_event(
                        "image_update.job.forwarding_complete",
                        job_id=job.id,
                        backend=backend_name,
                        total=remote_total,
                        success=remote_success,
                        failed=remote_failed,
                        skipped=remote_skipped,
                        rolled_back=remote_rolled_back,
                    )
                        
            except Exception as e:
                error_msg = f"Failed to forward to backend {backend_name}: {str(e)}"
                log_event(
                    "image_update.job.forwarding_exception",
                    job_id=job.id,
                    backend=backend_name,
                    error=str(e),
                )
                results["total"] += 1
                results["failed"] += 1
                results["by_backend"][backend_name]["failed"] += 1
                result = ImageUpdateResult(
                    job_id=job.id,
                    platform=remote_platform,
                    namespace="(all)",
                    resource_name="(product forwarding failed)",
                    kind="Unknown",
                    container_name=None,
                    old_image=None,
                    new_image=job.target_image,
                    status="failed",
                    error_message=error_msg,
                )
                db.add(result)
        
        db.commit()
        
        # Update job status
        job.finished_at = datetime.now(timezone.utc)
        
        # Determine final status
        if results["rolled_back"] > 0:
            job.status = "completed_with_rollbacks"
        elif results["failed"] > 0:
            job.status = "failed"
        else:
            job.status = "completed"
        
        db.commit()
        
        log_event(
            "image_update.job.complete",
            job_id=job.id,
            status=job.status,
            total=results["total"],
            success=results["success"],
            failed=results["failed"],
            skipped=results["skipped"],
            rolled_back=results["rolled_back"],
            by_backend=results["by_backend"],
        )
        
        return {"success": True, "results": results}


# Singleton instance
_service: Optional[ImageUpdateService] = None


def get_image_update_service() -> ImageUpdateService:
    """Get the singleton image update service instance."""
    global _service
    if _service is None:
        _service = ImageUpdateService()
    return _service

