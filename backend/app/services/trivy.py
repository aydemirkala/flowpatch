"""Trivy vulnerability scanner integration.

Uses the trivy CLI binary with --server flag for client-server mode.
Scans run in a background thread pool so they don't block the sync loop.
Results are stored in the image_cache DB table for later retrieval.

Supports federation: secondary backends can fetch results from the primary.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Tuple

from ..db import SessionLocal
from ..models import ConfigKV
from ..logging_utils import log_event


_trivy_binary: Optional[str] = None
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_pending_images: set[str] = set()
_pending_lock = threading.Lock()

MAX_WORKERS = 4


def _get_executor() -> ThreadPoolExecutor:
    """Lazy-init thread pool for background scans."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="trivy")
                log_event("trivy.pool.started", workers=MAX_WORKERS)
    return _executor


def _find_trivy_binary() -> Optional[str]:
    """Find the trivy binary path. Cached after first lookup."""
    global _trivy_binary
    if _trivy_binary is not None:
        return _trivy_binary if _trivy_binary else None

    path = shutil.which("trivy")
    if path:
        _trivy_binary = path
        log_event("trivy.binary.found", path=path)
    else:
        _trivy_binary = ""
        log_event("trivy.binary.not_found",
                  hint="Install trivy: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh")
    return _trivy_binary if _trivy_binary else None


def _effective_config() -> Tuple[Optional[str], bool]:
    """Get Trivy server URL and TLS verify setting."""
    url_cfg = os.getenv("TRIVY_URL", "")
    verify_cfg = os.getenv("TRIVY_VERIFY", "false").lower() == "true"

    backend_type = os.getenv("BACKEND_TYPE", "secondary")
    is_secondary = backend_type == "secondary"

    if is_secondary:
        try:
            from ..services.config_cache import get_config_cache
            cache = get_config_cache()
            trivy_cfg = cache.get_trivy_config()
            if not trivy_cfg and hasattr(cache, "_cache"):
                trivy_cfg = (cache._cache or {}).get("trivy_config", {})
            if trivy_cfg and trivy_cfg.get("url"):
                url_cfg = trivy_cfg["url"]
                verify_cfg = not trivy_cfg.get("skip_tls_verify", True)
                return url_cfg, verify_cfg
        except Exception as e:
            log_event("trivy.config.federation_error", error=str(e))

    try:
        db = SessionLocal()
        kv = {r.key: r.value for r in db.query(ConfigKV).filter(
            ConfigKV.key.in_(["trivy_url", "trivy_skip_tls_verify"])
        )}
        db.close()
        url_cfg = kv.get("trivy_url", url_cfg)
        if kv.get("trivy_skip_tls_verify") is not None:
            verify_cfg = str(kv["trivy_skip_tls_verify"]).lower() != "true"
    except Exception as e:
        log_event("trivy.config.read_error", error=str(e))

    return url_cfg, verify_cfg


def _max_detail_vulns() -> int:
    """Max number of detailed vulnerability entries to KEEP in the stored report
    (0 = unlimited). The distribution COUNTS (critical/high/medium/low/total) are
    always computed over every vulnerability and are never affected — only the
    verbose per-CVE list is capped, to keep security_info / image_cache small.
    Configurable from the Admin UI (ConfigKV 'trivy_max_detail_vulns'); env
    fallback TRIVY_MAX_DETAIL_VULNS; default 20."""
    default = 20
    raw: Optional[str] = os.getenv("TRIVY_MAX_DETAIL_VULNS")
    try:
        db = SessionLocal()
        try:
            row = db.query(ConfigKV).filter(ConfigKV.key == "trivy_max_detail_vulns").first()
            if row and row.value not in (None, ""):
                raw = row.value
        finally:
            db.close()
    except Exception:
        pass
    try:
        if raw is None or str(raw).strip() == "":
            return default
        n = int(str(raw).strip())
        return n if n >= 0 else default
    except Exception:
        return default


_DOCKERHUB_HOSTS = ("docker.io/", "registry-1.docker.io/", "index.docker.io/")


def _dockerhub_proxy() -> str:
    """Optional registry proxy to pull Docker Hub images through (e.g. an internal
    Harbor) when direct docker.io access is rate-limited/blocked. When set, a Trivy
    scan rewrites 'docker.io/<repo>' -> '<proxy>/<repo>' for the PULL only — the cache
    key and stored result stay under the original docker.io image, so lookups by the
    resource's real image still hit. Configurable from the Admin UI (ConfigKV
    'trivy_dockerhub_proxy'); env fallback TRIVY_DOCKERHUB_PROXY; default empty (off).
    Example value: 'intprod-harbor.example.com/docker-proxy'."""
    val = os.getenv("TRIVY_DOCKERHUB_PROXY", "")
    try:
        db = SessionLocal()
        try:
            row = db.query(ConfigKV).filter(ConfigKV.key == "trivy_dockerhub_proxy").first()
            if row and row.value not in (None, ""):
                val = row.value
        finally:
            db.close()
    except Exception:
        pass
    return (val or "").strip().rstrip("/")


def _scan_timeout() -> int:
    """Per-image Trivy scan subprocess timeout in seconds. Configurable from the Admin UI
    (ConfigKV 'trivy_scan_timeout_seconds'); env fallback TRIVY_SCAN_TIMEOUT; default 300.
    A rate-limited docker.io pull can hang to this ceiling, so it is bounded 30..1800."""
    val = os.getenv("TRIVY_SCAN_TIMEOUT", "")
    try:
        db = SessionLocal()
        try:
            row = db.query(ConfigKV).filter(ConfigKV.key == "trivy_scan_timeout_seconds").first()
            if row and row.value not in (None, ""):
                val = row.value
        finally:
            db.close()
    except Exception:
        pass
    try:
        return max(30, min(1800, int(val)))
    except (TypeError, ValueError):
        return 300


def _dockerhub_repo(image: str) -> Optional[str]:
    """If `image` resolves to a Docker Hub (docker.io) image, return its repo path without
    the registry host (e.g. 'daprio/dashboard:0.14.0' or 'library/nginx:1.25'); otherwise
    return None. Handles both the explicit 'docker.io/...' prefixes AND the common bare form
    ('nginx', 'daprio/dashboard') that Docker normalizes to docker.io but which the old
    prefix-only check missed — so those bypassed the proxy and hit docker.io rate limits."""
    repo = None
    for host in _DOCKERHUB_HOSTS:
        if image.startswith(host):
            repo = image[len(host):]
            break
    if repo is None:
        # No explicit docker.io prefix. The leading path segment is a registry host only if
        # it contains '.' or ':' (port) or is 'localhost' AND there is a '/' after it.
        first = image.split("/", 1)[0]
        if "/" in image and ("." in first or ":" in first or first == "localhost"):
            return None  # explicit non-Docker-Hub registry (quay.io/..., harbor/..., etc.)
        repo = image
    # Official images (single name segment, no org) live under 'library/' on docker.io.
    name = repo.split("@", 1)[0].split(":", 1)[0]
    if "/" not in name:
        repo = f"library/{repo}"
    return repo


def _scan_image_ref(image: str) -> str:
    """Return the image reference Trivy should actually pull. If a Docker Hub proxy is
    configured and `image` is a Docker Hub image (explicit prefix OR bare name), rewrite it
    to pull via the proxy; otherwise return `image` unchanged."""
    proxy = _dockerhub_proxy()
    if not proxy:
        return image
    repo = _dockerhub_repo(image)
    if repo is None:
        return image
    return f"{proxy}/{repo}"


def fetch_trivy_report(image: str) -> Optional[dict[str, Any]]:
    """Scan a container image using trivy CLI with --server flag (synchronous).

    Called by the background worker. Not meant to be called from the sync loop directly.
    """
    trivy_bin = _find_trivy_binary()
    if not trivy_bin:
        return None

    trivy_url, verify = _effective_config()
    if not trivy_url:
        return None

    # Pull via the configured Docker Hub proxy (internal Harbor) when set, to dodge
    # docker.io rate limits / egress blocks. Cache key stays the ORIGINAL image below.
    scan_ref = _scan_image_ref(image)
    if scan_ref != image:
        log_event("trivy.scan.via_proxy", original=image, scan_ref=scan_ref)

    cmd = [
        trivy_bin, "image",
        "--server", trivy_url,
        "--format", "json",
        "--quiet",
    ]
    if not verify:
        cmd.append("--insecure")
    cmd.append(scan_ref)

    scan_timeout = _scan_timeout()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=scan_timeout)

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:500]
            log_event("trivy.fetch.cli_error", image=image,
                      returncode=result.returncode, stderr=stderr)
            return None

        if not result.stdout.strip():
            log_event("trivy.fetch.empty_output", image=image)
            return None

        data = json.loads(result.stdout)
        return _normalize_trivy_response(data, image)

    except subprocess.TimeoutExpired:
        log_event("trivy.fetch.timeout", image=image, timeout=scan_timeout)
        return None
    except json.JSONDecodeError as e:
        log_event("trivy.fetch.parse_error", image=image, error=str(e))
        return None
    except Exception as e:
        log_event("trivy.fetch.error", image=image, error=str(e))
        return None


def _background_scan(image: str) -> None:
    """Worker function: scan image and store result in DB cache."""
    try:
        log_event("trivy.bg.start", image=image)
        report = fetch_trivy_report(image)

        from .image_cache import set_cached_trivy
        db = SessionLocal()
        try:
            set_cached_trivy(db, image, report, error=None if report else "scan_failed")
            if report:
                dist = report.get("vulnerabilityDistribution", {})
                log_event("trivy.bg.done", image=image, total=dist.get("total", 0))
                # Push the fresh result into resources using this image so the grid
                # shows Trivy data without a manual re-sync (targeted + change-guarded;
                # lazy import avoids the refresh<->trivy import cycle).
                try:
                    from .refresh import apply_trivy_to_resources
                    apply_trivy_to_resources(db, image, report)
                except Exception as e:
                    log_event("trivy.bg.apply_error", image=image, error=str(e))
            else:
                log_event("trivy.bg.no_result", image=image)
        finally:
            db.close()
    except Exception as e:
        log_event("trivy.bg.error", image=image, error=str(e))
    finally:
        with _pending_lock:
            _pending_images.discard(image)


def submit_scan(image: str) -> None:
    """Submit an image for background Trivy scanning. Non-blocking, deduplicates."""
    if not _find_trivy_binary():
        return
    if not _effective_config()[0]:
        return

    with _pending_lock:
        if image in _pending_images:
            return
        _pending_images.add(image)

    try:
        _get_executor().submit(_background_scan, image)
        log_event("trivy.bg.submitted", image=image, pending=len(_pending_images))
    except Exception as e:
        log_event("trivy.bg.submit_error", image=image, error=str(e))
        with _pending_lock:
            _pending_images.discard(image)


def submit_scans(images: list[str]) -> None:
    """Submit multiple images for background scanning."""
    for img in images:
        submit_scan(img)


def get_pending_count() -> int:
    """Return number of scans currently in queue/running."""
    with _pending_lock:
        return len(_pending_images)


def _normalize_trivy_response(data: dict, image: str) -> dict[str, Any]:
    """Convert Trivy scan response to a standardized format similar to Twistlock."""
    results = data.get("Results") or data.get("results") or []

    all_vulns = []
    dist = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0, "total": 0}

    for result in results:
        vulns = result.get("Vulnerabilities") or result.get("vulnerabilities") or []
        target = result.get("Target") or result.get("target") or ""
        result_type = result.get("Type") or result.get("type") or ""

        for v in vulns:
            sev = (v.get("Severity") or v.get("severity") or "UNKNOWN").upper()
            vuln_entry = {
                "id": v.get("VulnerabilityID") or v.get("vulnerabilityID") or "",
                "severity": sev,
                "pkgName": v.get("PkgName") or v.get("pkgName") or "",
                "installedVersion": v.get("InstalledVersion") or v.get("installedVersion") or "",
                "fixedVersion": v.get("FixedVersion") or v.get("fixedVersion") or "",
                "title": v.get("Title") or v.get("title") or "",
                "target": target,
                "type": result_type,
                "primaryURL": v.get("PrimaryURL") or v.get("primaryURL") or "",
                "cvss": _extract_cvss(v),
            }
            all_vulns.append(vuln_entry)

            if sev == "CRITICAL":
                dist["critical"] += 1
            elif sev == "HIGH":
                dist["high"] += 1
            elif sev == "MEDIUM":
                dist["medium"] += 1
            elif sev == "LOW":
                dist["low"] += 1
            else:
                dist["unknown"] += 1
            dist["total"] += 1

    log_event("trivy.fetch.success", image=image,
              critical=dist["critical"], high=dist["high"],
              medium=dist["medium"], low=dist["low"], total=dist["total"])

    # Cap the DETAILED list only — counts above stay complete so risk/badge
    # calculations are unaffected. Keep the most severe entries first.
    truncated = False
    if all_vulns:
        _rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
        all_vulns.sort(key=lambda v: _rank.get((v.get("severity") or "UNKNOWN"), 5))
        cap = _max_detail_vulns()
        if cap and len(all_vulns) > cap:
            all_vulns = all_vulns[:cap]
            truncated = True

    return {
        "vulnerabilities": all_vulns if all_vulns else None,
        "vulnerabilityDistribution": dist,
        "vulnerabilities_truncated": truncated,
        "source": "trivy",
    }


def _extract_cvss(vuln: dict) -> Optional[float]:
    """Extract the highest CVSS score from a Trivy vulnerability entry."""
    cvss = vuln.get("CVSS") or vuln.get("cvss")
    if not cvss or not isinstance(cvss, dict):
        return None
    best = 0.0
    for source_data in cvss.values():
        if isinstance(source_data, dict):
            score = source_data.get("V3Score") or source_data.get("v3Score") or 0
            if score > best:
                best = score
    return best if best > 0 else None
