"""LLM-powered security advice generation.

Uses an OpenAI-compatible API (local or remote) to generate
contextual security advice based on vulnerability scan results.

Rate-limit aware: token-bucket limiter enforces 100 requests/hour.
Results are cached in security_info alongside other scan data.
"""
from __future__ import annotations

import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import httpx

from ..db import SessionLocal
from ..models import ConfigKV
from ..logging_utils import log_event

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_pending: set[str] = set()
_pending_lock = threading.Lock()

MAX_WORKERS = 2
RATE_LIMIT_PER_HOUR = 100

# Token-bucket rate limiter (thread-safe)
_bucket_lock = threading.Lock()
_bucket_tokens = float(RATE_LIMIT_PER_HOUR)
_bucket_last_refill = time.monotonic()


def _rate_limit_acquire() -> bool:
    """Try to consume one token. Returns True if allowed, False if rate-limited."""
    global _bucket_tokens, _bucket_last_refill
    with _bucket_lock:
        now = time.monotonic()
        elapsed = now - _bucket_last_refill
        _bucket_tokens = min(RATE_LIMIT_PER_HOUR, _bucket_tokens + elapsed * (RATE_LIMIT_PER_HOUR / 3600))
        _bucket_last_refill = now
        if _bucket_tokens >= 1:
            _bucket_tokens -= 1
            return True
        return False

DEFAULT_SECURITY_PROMPT = (
    "You are a Kubernetes container security expert. "
    "Assess the given vulnerability information and give short, clear, actionable advice. "
    "RULE: Only recommend the version provided in the 'Latest' field. Do NOT invent version numbers. "
    "If Latest is 'unknown' or equals Current, use a general phrasing like 'upgrade to the latest version'. "
    "Do not recommend floating tags (e.g. redis:7-alpine). "
    "If the current tag is a floating tag, mention both moving to a specific version and re-pulling the floating tag (docker pull) as separate options. "
    "Limit the answer to 3-5 sentences. Write in Markdown."
)

DEFAULT_UPGRADE_PROMPT = (
    "You are a Kubernetes and open-source software upgrade expert. "
    "Produce a detailed upgrade assessment based on the given current and target version information. "
    "Include:\n"
    "1. Whether a direct upgrade is possible or intermediate versions are needed (upgrade path)\n"
    "2. Breaking changes and caveats for major version transitions\n"
    "3. Migration steps, if any are required\n"
    "4. Urgency assessment based on the security findings\n"
    "5. RULE: Only recommend the version provided in the 'Latest Version' field. Do NOT invent version numbers. Do not use floating tags.\n"
    "Write in Markdown. Be short and clear."
)

DEFAULT_EOL_STATUS_PROMPT = (
    "You are an expert on open-source software release and support lifecycles. "
    "The user gives a product, the version they run, and (when available) the container "
    "image repository. This product has NO entry on endoflife.date, so judge from your "
    "general knowledge whether that version is still supported/maintained upstream.\n"
    "IMPORTANT: when an image repository is given, identify the EXACT product from its "
    "path — the bare product name is often just a vendor or chart name and is too generic "
    "(e.g. 'apache' covers APISIX, Kafka, Tomcat...; the repository's final path segment, "
    "such as '<vendor>/apisix' or '<vendor>/pmm-server', names the real product). "
    "If you cannot confidently identify the product or its lifecycle, answer UNKNOWN "
    "rather than guessing.\n"
    "Respond in EXACTLY this two-line format, nothing else:\n"
    "STATUS: <SUPPORTED|EOL|UNKNOWN>\n"
    "NOTE: <one concise sentence, max ~120 chars; do not invent version numbers>\n"
    "Use UNKNOWN whenever you are not reasonably sure."
)

_prompt_cache: dict[str, tuple[float, str]] = {}
_PROMPT_CACHE_TTL = 300  # 5 minutes


_FEDERATION_PROMPT_KEYS = {
    "llm_prompt_security": "security",
    "llm_prompt_upgrade": "upgrade",
    "llm_prompt_eol_status": "eol_status",
}


def get_prompt(key: str) -> str:
    """Read a prompt from ConfigKV (primary) or federation cache (secondary). Falls back to hardcoded defaults."""
    now = time.monotonic()
    cached = _prompt_cache.get(key)
    if cached and (now - cached[0]) < _PROMPT_CACHE_TTL:
        return cached[1]

    defaults = {
        "llm_prompt_security": DEFAULT_SECURITY_PROMPT,
        "llm_prompt_upgrade": DEFAULT_UPGRADE_PROMPT,
        "llm_prompt_eol_status": DEFAULT_EOL_STATUS_PROMPT,
    }
    value = ""

    import os
    is_secondary = os.getenv("BACKEND_TYPE", "secondary") != "primary"

    if is_secondary:
        try:
            from .config_cache import get_config_cache
            cache = get_config_cache()
            prompts = cache.get_llm_prompts()
            fed_key = _FEDERATION_PROMPT_KEYS.get(key, "")
            value = prompts.get(fed_key, "")
        except Exception:
            pass

    if not value or not value.strip():
        try:
            db = SessionLocal()
            row = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
            db.close()
            value = row.value if row and row.value and row.value.strip() else ""
        except Exception:
            pass

    if not value or not value.strip():
        value = defaults.get(key, "")

    _prompt_cache[key] = (now, value)
    return value


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="llm")
                log_event("llm.pool.started", workers=MAX_WORKERS)
    return _executor


def _get_llm_config(db=None) -> dict:
    """Read the active LLM provider from ConfigKV (JSON array)."""
    import json as _json
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        row = db.query(ConfigKV).filter(ConfigKV.key == "llm_providers").one_or_none()
        providers = []
        if row and row.value:
            try:
                providers = _json.loads(row.value)
            except Exception:
                pass

        # Fallback: old flat keys (migration support)
        if not providers:
            def _val(key: str, default: str = "") -> str:
                r = db.query(ConfigKV).filter(ConfigKV.key == key).one_or_none()
                return r.value if r else default
            old_url = _val("llm_base_url")
            if old_url:
                return {
                    "enabled": _val("llm_enabled", "false") == "true",
                    "base_url": old_url,
                    "model": _val("llm_model", "llm"),
                    "api_key": _val("llm_api_key", "dummy"),
                    "max_tokens": int(_val("llm_max_tokens", "1000")),
                    "temperature": float(_val("llm_temperature", "0.3")),
                }
            return {"enabled": False, "base_url": "", "model": "", "api_key": "", "max_tokens": 1000, "temperature": 0.3}

        active = next((p for p in providers if p.get("active")), None)
        if not active:
            return {"enabled": False, "base_url": "", "model": "", "api_key": "", "max_tokens": 1000, "temperature": 0.3}

        from .crypto_utils import decrypt_value
        return {
            "enabled": True,
            "base_url": active.get("base_url", ""),
            "model": active.get("model", "llm"),
            "api_key": decrypt_value(active.get("api_key", "")),
            "api_key_header": active.get("api_key_header", ""),
            "max_tokens": int(active.get("max_tokens", 1000)),
            "temperature": float(active.get("temperature", 0.3)),
        }
    finally:
        if close_db:
            db.close()


def _build_auth_headers(cfg: dict) -> dict:
    """Build auth headers from config. Returns empty dict if no header configured."""
    header = cfg.get("api_key_header", "")
    key = cfg.get("api_key", "")
    if header and key:
        return {header: key}
    return {}


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from model output. Handles three shapes
    reasoning models produce: a complete block, an UNCLOSED <think> (the closing tag got cut
    off by max_tokens — drop from the tag to the end), and a dangling </think> with no opener
    (keep only the answer after it). Without this an unclosed block leaks raw chain-of-thought
    into the parsed note/advice."""
    if not text:
        return ""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    low = t.lower()
    if "</think>" in low and "<think>" not in low:
        t = t[low.rfind("</think>") + len("</think>"):]
    t = re.sub(r"<think>.*$", "", t, flags=re.DOTALL | re.IGNORECASE)
    return t.strip()


def _build_security_prompt(resource_name: str, image: str,
                           current_version: str, latest_version: str,
                           version_diff: str, twistlock: dict | None,
                           trivy: dict | None, eol_date: str | None) -> str:
    parts = [
        f"Resource: {resource_name}",
        f"Image: {image}",
        f"Current: {current_version}, Latest: {latest_version or 'unknown'}, Diff: {version_diff or 'unknown'}",
    ]

    if eol_date:
        parts.append(f"EOL Date: {eol_date}")

    if twistlock:
        d = twistlock.get("vulnerabilityDistribution", {})
        rf = twistlock.get("riskFactorCount", 0)
        parts.append(
            f"Twistlock: C:{d.get('critical', 0)} H:{d.get('high', 0)} "
            f"M:{d.get('medium', 0)} L:{d.get('low', 0)} RF:{rf}"
        )

    if trivy:
        d = trivy.get("vulnerabilityDistribution", {})
        parts.append(
            f"Trivy: C:{d.get('critical', 0)} H:{d.get('high', 0)} "
            f"M:{d.get('medium', 0)} L:{d.get('low', 0)} Total:{d.get('total', 0)}"
        )
        vulns = trivy.get("vulnerabilities", [])
        top = [v for v in vulns if v.get("severity") in ("CRITICAL", "HIGH")][:10]
        for v in top:
            parts.append(
                f"  - {v.get('id', '?')} ({v.get('severity', '?')}) "
                f"pkg={v.get('pkgName', '')} installed={v.get('installedVersion', '')} "
                f"fixed={v.get('fixedVersion', 'N/A')}"
            )

    parts.append("\nAssess this image's security posture and recommend the priority action.")
    return "\n".join(parts)


def generate_security_advice(resource_name: str, image: str,
                             current_version: str, latest_version: str,
                             version_diff: str, twistlock: dict | None,
                             trivy: dict | None, eol_date: str | None) -> str | None:
    """Synchronously call LLM API and return advice text. Returns None on failure."""
    cfg = _get_llm_config()
    if not cfg["enabled"] or not cfg["base_url"]:
        return None

    if not _rate_limit_acquire():
        log_event("llm.rate_limited", resource=resource_name, image=image)
        return None

    prompt = _build_security_prompt(
        resource_name, image, current_version, latest_version,
        version_diff, twistlock, trivy, eol_date
    )

    try:
        log_event("llm.request.start", resource=resource_name, image=image)
        with httpx.Client(verify=False, timeout=60) as client:
            resp = client.post(
                f"{cfg['base_url']}/chat/completions",
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": get_prompt("llm_prompt_security")},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": cfg["max_tokens"],
                    "temperature": cfg["temperature"],
                },
                headers=_build_auth_headers(cfg),
            )
            resp.raise_for_status()
            data = resp.json()

        raw = data["choices"][0]["message"]["content"]
        advice = _strip_think_tags(raw)
        tokens = data.get("usage", {}).get("total_tokens", 0)
        log_event("llm.request.ok", resource=resource_name, tokens=tokens)
        return advice
    except Exception as e:
        log_event("llm.request.error", resource=resource_name, error=str(e))
        return None


def _build_upgrade_prompt(product_name: str, image: str,
                          current_version: str, latest_version: str,
                          version_diff: str, twistlock: dict | None,
                          trivy: dict | None, eol_date: str | None) -> str:
    parts = [
        f"Product: {product_name}",
        f"Image: {image}",
        f"Current Version: {current_version}",
        f"Latest Version: {latest_version or 'unknown'}",
        f"Version Diff: {version_diff or 'unknown'}",
    ]
    if eol_date:
        parts.append(f"EOL Date: {eol_date}")

    if twistlock:
        d = twistlock.get("vulnerabilityDistribution", {})
        parts.append(f"Twistlock: C:{d.get('critical', 0)} H:{d.get('high', 0)} M:{d.get('medium', 0)} L:{d.get('low', 0)}")

    if trivy:
        d = trivy.get("vulnerabilityDistribution", {})
        parts.append(f"Trivy: C:{d.get('critical', 0)} H:{d.get('high', 0)} M:{d.get('medium', 0)} L:{d.get('low', 0)}")
        vulns = trivy.get("vulnerabilities", [])
        top = [v for v in vulns if v.get("severity") in ("CRITICAL", "HIGH")][:5]
        if top:
            parts.append("Critical/High CVEs:")
            for v in top:
                parts.append(f"  - {v.get('id', '?')} ({v.get('severity')}) pkg={v.get('pkgName', '')} fixed={v.get('fixedVersion', 'N/A')}")

    parts.append(
        f"\nI want to upgrade {product_name} from {current_version} to {latest_version or 'the latest version'}. "
        "Assess the upgrade path, breaking changes, migration steps, and points to watch for."
    )
    return "\n".join(parts)


def generate_upgrade_advice(product_name: str, image: str,
                            current_version: str, latest_version: str,
                            version_diff: str, twistlock: dict | None,
                            trivy: dict | None, eol_date: str | None) -> str | None:
    """Synchronously call LLM for upgrade path advice. Returns None on failure."""
    cfg = _get_llm_config()
    if not cfg["enabled"] or not cfg["base_url"]:
        return None

    if not _rate_limit_acquire():
        log_event("llm.rate_limited", product=product_name, image=image)
        return None

    prompt = _build_upgrade_prompt(
        product_name, image, current_version, latest_version,
        version_diff, twistlock, trivy, eol_date
    )

    try:
        log_event("llm.upgrade.start", product=product_name, image=image)
        with httpx.Client(verify=False, timeout=60) as client:
            resp = client.post(
                f"{cfg['base_url']}/chat/completions",
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": get_prompt("llm_prompt_upgrade")},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": cfg["max_tokens"],
                    "temperature": cfg["temperature"],
                },
                headers=_build_auth_headers(cfg),
            )
            resp.raise_for_status()
            data = resp.json()

        raw = data["choices"][0]["message"]["content"]
        advice = _strip_think_tags(raw)
        tokens = data.get("usage", {}).get("total_tokens", 0)
        log_event("llm.upgrade.ok", product=product_name, tokens=tokens)
        return advice
    except Exception as e:
        log_event("llm.upgrade.error", product=product_name, error=str(e))
        return None


def _background_generate(resource_id: int, resource_name: str, image: str,
                         current_version: str, latest_version: str,
                         version_diff: str, twistlock: dict | None,
                         trivy: dict | None, eol_date: str | None) -> None:
    """Background worker: generate advice and store it in DB."""
    cache_key = f"llm:{image}"
    try:
        advice = generate_security_advice(
            resource_name, image, current_version, latest_version,
            version_diff, twistlock, trivy, eol_date
        )
        if advice:
            db = SessionLocal()
            try:
                from ..models import Resource
                res = db.query(Resource).filter(Resource.id == resource_id).one_or_none()
                if res and res.security_info:
                    from datetime import datetime, timezone
                    si = dict(res.security_info)
                    si["advice"] = advice
                    si["llm_advice"] = True
                    si["llm_advice_at"] = datetime.now(timezone.utc).isoformat()
                    res.security_info = si
                    res.advice = advice
                    # Keep the grid's precomputed summary in sync with security_info.
                    try:
                        from .refresh import build_resource_summary
                        res.sec_summary = build_resource_summary(si, res.image)
                    except Exception:
                        pass
                    db.commit()
                    log_event("llm.advice.saved", resource=resource_name)
            finally:
                db.close()
    except Exception as e:
        log_event("llm.background.error", resource=resource_name, error=str(e))
    finally:
        with _pending_lock:
            _pending.discard(cache_key)


def submit_advice_request(resource_id: int, resource_name: str, image: str,
                          current_version: str, latest_version: str,
                          version_diff: str, twistlock: dict | None,
                          trivy: dict | None, eol_date: str | None) -> None:
    """Submit a non-blocking LLM advice request for background processing."""
    cfg = _get_llm_config()
    if not cfg["enabled"] or not cfg["base_url"]:
        return

    cache_key = f"llm:{image}"
    with _pending_lock:
        if cache_key in _pending:
            return
        _pending.add(cache_key)

    _get_executor().submit(
        _background_generate,
        resource_id, resource_name, image,
        current_version, latest_version, version_diff,
        twistlock, trivy, eol_date
    )
    log_event("llm.advice.submitted", resource=resource_name)


def get_pending_count() -> int:
    with _pending_lock:
        return len(_pending)


# ===========================================================================
# LLM support-status fallback when there is no public EOL (endoflife.date) data
# ===========================================================================

def _parse_eol_status(raw: str) -> tuple[str, str]:
    """Parse the LLM's two-line answer into (status, note). Lenient; defaults to
    'unknown' when not clearly stated."""
    status = "unknown"
    note = ""
    for line in (raw or "").splitlines():
        l = line.strip()
        u = l.upper()
        if u.startswith("STATUS:"):
            v = l.split(":", 1)[1].strip().lower()
            if "support" in v or "maintain" in v or "active" in v:
                status = "supported"
            elif "eol" in v or "end" in v or "unsupport" in v or "deprecat" in v:
                status = "eol"
            else:
                status = "unknown"
        elif u.startswith("NOTE:"):
            note = l.split(":", 1)[1].strip()
    if not note:
        # Fallback: first usable line, but never a tag-like/reasoning line (so a stray
        # <think> fragment can't leak into the displayed note).
        for line in (raw or "").splitlines():
            s = line.strip()
            if s and not s.startswith("<"):
                note = s
                break
    return status, note[:480]


def _image_repo_hint(image: str) -> str:
    """Extract the repository path from a full image ref to disambiguate the product.
    Drops the registry host, tag and digest; the namespace/repo names the real product
    far more precisely than a bare vendor/chart name. Product-agnostic. Examples:
      'harbor.example.com/docker-proxy/percona/pmm-server:3.8.1' -> 'docker-proxy/percona/pmm-server'
      'apache/apisix:3.9'                                        -> 'apache/apisix'
      'nginx:1.29'                                               -> 'nginx'
    """
    if not image:
        return ""
    ref = image.strip().split("@", 1)[0]  # drop digest
    path, sep, last = ref.rpartition("/")
    last = last.split(":", 1)[0]          # drop :tag from the final segment only
    ref = f"{path}/{last}" if sep else last
    parts = ref.split("/")
    # drop a leading registry host (looks like a hostname: has a dot/colon, or is localhost)
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]
    return "/".join(p for p in parts if p)


def generate_eol_status(product_name: str, current_version: str, image: str = "") -> Optional[dict]:
    """Ask the LLM whether `product_name` `current_version` is still supported upstream.
    `image` (optional) is the container image ref; its repository path disambiguates the
    exact product when `product_name` is a generic vendor/chart name.
    Returns {'status': supported|eol|unknown, 'note': str} or None on failure."""
    cfg = _get_llm_config()
    if not cfg["enabled"] or not cfg["base_url"]:
        return None
    if not _rate_limit_acquire():
        log_event("llm.eol.rate_limited", product=product_name)
        return None
    repo = _image_repo_hint(image)
    user = (f"Product: {product_name}\n"
            f"Version: {current_version or 'unknown'}\n"
            + (f"Container image repository: {repo}\n" if repo else "")
            + "Identify the exact product (prefer the image repository path over the bare "
              "product name), then judge whether this version is still supported/maintained upstream.")
    try:
        log_event("llm.eol.start", product=product_name, version=current_version)
        with httpx.Client(verify=False, timeout=60) as client:
            resp = client.post(
                f"{cfg['base_url']}/chat/completions",
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": get_prompt("llm_prompt_eol_status")},
                        {"role": "user", "content": user},
                    ],
                    # Headroom for reasoning models: a <think> pass can exceed a tight cap,
                    # leaving no tokens for the STATUS/NOTE answer (-> always "unknown").
                    "max_tokens": min(int(cfg["max_tokens"]), 1024),
                    "temperature": cfg["temperature"],
                },
                headers=_build_auth_headers(cfg),
            )
            resp.raise_for_status()
            data = resp.json()
        raw = _strip_think_tags(data["choices"][0]["message"]["content"])
        status, note = _parse_eol_status(raw)
        log_event("llm.eol.ok", product=product_name, status=status)
        return {"status": status, "note": note}
    except Exception as e:
        log_event("llm.eol.error", product=product_name, error=str(e))
        return None


def _fetch_eol_status_from_primary(primary_url: str, auth_token: str,
                                   product_name: str, current_version: str,
                                   image: str = "") -> Optional[dict]:
    """Secondary backends with llm_mode=primary: ask the primary to produce the status."""
    try:
        url = f"{primary_url.rstrip('/')}/api/federation/llm-advice"
        params = {"advice_type": "eol_status", "product": product_name,
                  "version": current_version or "", "image": image or "", "resource_name": product_name}
        with httpx.Client(verify=False, timeout=60) as client:
            resp = client.get(url, params=params, headers={"X-Backend-Token": auth_token})
            if resp.status_code == 200:
                d = resp.json()
                if d.get("status"):
                    return {"status": d["status"], "note": d.get("note", "")}
        log_event("llm.eol.federation.no_data", product=product_name)
    except Exception as e:
        log_event("llm.eol.federation.error", product=product_name, error=str(e))
    return None


def _apply_eol_status_to_resources(product_name: str, current_version: str,
                                   status: str, note: str) -> None:
    """Write the LLM support status onto every resource of this product+version that has
    NO public EOL date (own DB session; dedicated columns, won't clash with refresh)."""
    try:
        db = SessionLocal()
        try:
            from ..models import Resource
            q = db.query(Resource).filter(
                Resource.product_name == product_name,
                (Resource.eol_date.is_(None)) | (Resource.eol_date == ""),
            )
            if current_version:
                q = q.filter(Resource.current_version == current_version)
            updated = 0
            for res in q.all():
                if res.eol_support_status != status or res.eol_support_note != note:
                    res.eol_support_status = status
                    res.eol_support_note = note
                    updated += 1
            if updated:
                db.commit()
                log_event("llm.eol.applied", product=product_name, status=status, count=updated)
        finally:
            db.close()
    except Exception as e:
        log_event("llm.eol.apply_error", product=product_name, error=str(e))


def _background_eol_status(product_name: str, current_version: str,
                           primary_url: Optional[str], auth_token: Optional[str],
                           image: str = "") -> None:
    cache_key = f"eolllm:{product_name}:{current_version}"
    try:
        if primary_url and auth_token:
            result = _fetch_eol_status_from_primary(primary_url, auth_token, product_name, current_version, image)
        else:
            result = generate_eol_status(product_name, current_version, image)
        if result and result.get("status"):
            _apply_eol_status_to_resources(product_name, current_version,
                                           result["status"], result.get("note", ""))
    except Exception as e:
        log_event("llm.eol.background_error", product=product_name, error=str(e))
    finally:
        with _pending_lock:
            _pending.discard(cache_key)


def submit_eol_status_request(product_name: str, current_version: str,
                              primary_url: Optional[str] = None,
                              auth_token: Optional[str] = None,
                              image: str = "") -> None:
    """Non-blocking: produce an LLM support status for a product+version with no public
    EOL data, and apply it to matching resources. For llm_mode=primary secondaries pass
    primary_url+auth_token (the primary's LLM is used); otherwise the local LLM is used.
    `image` (optional) disambiguates the exact product from its repository path."""
    if not product_name:
        return
    if not primary_url:  # local mode needs an enabled local LLM
        cfg = _get_llm_config()
        if not cfg["enabled"] or not cfg["base_url"]:
            return
    cache_key = f"eolllm:{product_name}:{current_version}"
    with _pending_lock:
        if cache_key in _pending:
            return
        _pending.add(cache_key)
    _get_executor().submit(_background_eol_status, product_name, current_version, primary_url, auth_token, image)
    log_event("llm.eol.submitted", product=product_name, version=current_version)
