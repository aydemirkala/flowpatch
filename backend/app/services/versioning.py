from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

import urllib.request
from urllib.error import HTTPError, URLError
from ..logging_utils import log_event
from ..config import settings


# Accept optional 'v' prefix (e.g., v1.15.12) and optional suffixes (-rc, +meta)
SEMVER_RE = re.compile(r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:[-+].*)?$")

# Extended: 2+ numeric components, leading zeros allowed for calver-style tags
# (e.g., 1.2, 1.2.3, 25.04.8.4.1, v1.15.12-rc1, 25.04.8.4.1-debian-11-r3)
EXTENDED_SEMVER_RE = re.compile(r"^v?\d+(?:\.\d+)+(?:[-+].*)?$")


def _parse_extended_tuple(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse version into a variable-length int tuple. Supports any number of
    numeric components (>=2) with optional 'v' prefix and optional -/+ suffix."""
    if not version:
        return None
    if not EXTENDED_SEMVER_RE.match(version):
        return None
    s = version[1:] if version.startswith("v") else version
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        return None


@dataclass(frozen=True)
class ParsedImage:
    registry: str
    repository: str
    tag: Optional[str]


def parse_image(image: str | None) -> Optional[ParsedImage]:
    if not image:
        return None
    parts = image.split("/")
    if len(parts) == 1:
        registry = "docker.io"
        repository = parts[0]
    else:
        has_explicit_registry = "." in parts[0] or ":" in parts[0]
        registry = parts[0] if has_explicit_registry else "docker.io"
        # When a registry is explicitly provided (including docker.io), drop it from the repository path
        repository = "/".join(parts[1:]) if has_explicit_registry else "/".join(parts)
    repo, tag = _split_tag(repository)
    # Default tag to 'latest' when missing
    if tag is None or tag == "":
        tag = "latest"
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return ParsedImage(registry=registry, repository=repo, tag=tag)


def _split_tag(repository: str) -> Tuple[str, Optional[str]]:
    if ":" in repository:
        repo, tag = repository.rsplit(":", 1)
        return repo, tag
    return repository, None


@dataclass(frozen=True)
class VersionDiff:
    category: str  # major|minor|patch|same|unknown


def compare_semver(current: Optional[str], latest: Optional[str]) -> VersionDiff:
    if not current or not latest:
        return VersionDiff("unknown")
    cur = _parse_extended_tuple(current)
    lat = _parse_extended_tuple(latest)
    if not cur or not lat or len(cur) < 2 or len(lat) < 2:
        return VersionDiff("unknown")
    if lat <= cur:
        # Treat older or equal "latest" as same
        return VersionDiff("same")
    # Custom policy: change in major OR minor component => "major"; everything else => "minor"
    if lat[0] > cur[0]:
        return VersionDiff("major")
    if lat[1] > cur[1]:
        return VersionDiff("major")
    return VersionDiff("minor")


def fetch_latest_version(parsed: ParsedImage) -> Optional[str]:
    try:
        log_event("version.fetch_latest.start", registry=parsed.registry, repo=parsed.repository)
        # Optionally treat a private registry's Docker Hub proxy/mirror paths as Docker Hub
        # aliases. Configured via PRIVATE_REGISTRY_HOST + REGISTRY_DOCKERHUB_PREFIXES;
        # disabled when PRIVATE_REGISTRY_HOST is empty.
        effective_registry = parsed.registry
        effective_repo = parsed.repository

        private_host = settings.private_registry_host
        if private_host and private_host in parsed.registry:
            for prefix in settings.registry_dockerhub_prefixes:
                if parsed.repository.startswith(prefix):
                    effective_registry = "docker.io"
                    # Strip the proxy prefix from repository
                    effective_repo = parsed.repository.split("/", 1)[1] if "/" in parsed.repository else parsed.repository
                    log_event("version.proxy_redirect", original_registry=parsed.registry, original_repo=parsed.repository, effective_registry=effective_registry, effective_repo=effective_repo)
                    break

        latest: Optional[str] = None

        # Special-case Dapr images: use GitHub tags as the source of truth
        if "ghcr.io" in effective_registry and parsed.repository.startswith("dapr/"):
            try:
                gh_tags = _fetch_github_repo_tags("dapr", "dapr")
                latest = _select_highest_semver(gh_tags)
                if latest:
                    log_event("version.fetch_latest.success", source="github", latest=latest)
                    return latest
            except Exception as e:
                log_event("version.github.error", error=str(e), fallback="ghcr")
            # Fallback to GHCR tags if GitHub API is rate limited/unavailable
            try:
                latest = _select_highest_semver(_fetch_ghcr_tags(parsed.repository))
                log_event("version.fetch_latest.success", source="ghcr", latest=latest)
            except Exception as e:
                log_event("version.ghcr.error", error=str(e))
                latest = None
        elif "quay.io" in effective_registry:
            latest = _fetch_quay_latest(parsed.repository)
            log_event("version.fetch_latest.success", source="quay", latest=latest)
        elif "docker.io" in effective_registry or effective_registry in {"registry-1.docker.io"}:
            latest = _fetch_dockerhub_latest(effective_repo)
            log_event("version.fetch_latest.success", source="dockerhub", latest=latest)
        elif "ghcr.io" in effective_registry:
            latest = _fetch_ghcr_latest(parsed.repository)
            log_event("version.fetch_latest.success", source="ghcr", latest=latest)

        if latest:
            return latest

        # Fallback chain: try to resolve via mirror/private registry name conventions.
        # Only runs when the primary lookup yielded nothing.
        return _fallback_latest_version(parsed, effective_registry)
    except Exception as e:
        log_event("version.fetch_latest.error", error=str(e))
        return None


_KNOWN_UPSTREAM_REGISTRIES = ("docker.io", "ghcr.io", "quay.io", "registry-1.docker.io")


def _fallback_latest_version(parsed: ParsedImage, effective_registry: str) -> Optional[str]:
    """Try to resolve latest version for mirror/private registry images.

    Strategy (in order):
      1. If repository contains 'library/<x>', try docker.io with that segment.
      2. Try last path segment as library/<name> on docker.io.
      3. Try last two path segments as vendor/repo on ghcr.io.

    Skipped if the effective registry is already a known upstream (already tried).
    """
    if any(r in effective_registry for r in _KNOWN_UPSTREAM_REGISTRIES):
        return None
    repo = parsed.repository or ""
    if not repo:
        return None

    # 1) library/<x> substring → docker.io official image
    if "/library/" in repo:
        idx = repo.find("/library/")
        candidate = repo[idx + 1:]
        try:
            latest = _fetch_dockerhub_latest(candidate)
            log_event("version.fallback.library_substr",
                      repo=repo, candidate=candidate, latest=latest)
            if latest:
                return latest
        except Exception as e:
            log_event("version.fallback.library_substr.error",
                      repo=repo, error=str(e))

    # 2) Last segment → docker.io/library/<name>
    last_seg = repo.split("/")[-1]
    if last_seg:
        candidate = f"library/{last_seg}"
        try:
            latest = _fetch_dockerhub_latest(candidate)
            log_event("version.fallback.last_segment_dockerhub",
                      repo=repo, candidate=candidate, latest=latest)
            if latest:
                return latest
        except Exception as e:
            log_event("version.fallback.last_segment_dockerhub.error",
                      repo=repo, error=str(e))

    # 3) Last 2 segments → docker.io/<vendor>/<repo>
    parts = [p for p in repo.split("/") if p]
    if len(parts) >= 2:
        candidate = "/".join(parts[-2:])
        try:
            latest = _fetch_dockerhub_latest(candidate)
            log_event("version.fallback.vendor_repo_dockerhub",
                      repo=repo, candidate=candidate, latest=latest)
            if latest:
                return latest
        except Exception as e:
            log_event("version.fallback.vendor_repo_dockerhub.error",
                      repo=repo, error=str(e))

    # 4) vendor/repo → ghcr.io
    if len(parts) >= 2:
        candidate = "/".join(parts[-2:])
        try:
            latest = _fetch_ghcr_latest(candidate)
            log_event("version.fallback.vendor_repo_ghcr",
                      repo=repo, candidate=candidate, latest=latest)
            if latest:
                return latest
        except Exception as e:
            log_event("version.fallback.vendor_repo_ghcr.error",
                      repo=repo, error=str(e))

    return None


def _fetch_quay_latest(repository: str) -> Optional[str]:
    # Quay API: https://quay.io/api/v1/repository/{repo}/tag/?onlyActiveTags=true
    url = f"https://quay.io/api/v1/repository/{repository}/tag/?onlyActiveTags=true"
    with urllib.request.urlopen(url, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    tags = [t.get("name") for t in data.get("tags", []) if t.get("name")]
    log_event("version.tags", source="quay", repo=repository, count=len(tags))
    return _select_highest_semver(tags)


def _fetch_dockerhub_latest(repository: str) -> Optional[str]:
    tags = _fetch_dockerhub_tags(repository)
    log_event("version.tags", source="dockerhub", repo=repository, count=len(tags))
    latest = _select_highest_semver(tags)
    # Fallback: if bitnami repo has no tags, try bitnamilegacy
    if not latest and repository.startswith("bitnami/"):
        legacy_repo = repository.replace("bitnami/", "bitnamilegacy/", 1)
        log_event("version.fallback.bitnamilegacy", original_repo=repository, fallback_repo=legacy_repo)
        tags = _fetch_dockerhub_tags(legacy_repo)
        log_event("version.tags", source="dockerhub", repo=legacy_repo, count=len(tags))
        latest = _select_highest_semver(tags)
    return latest


def _fetch_ghcr_latest(repository: str) -> Optional[str]:
    # GHCR requires an auth token even for public repos
    token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull"
    with urllib.request.urlopen(token_url, timeout=5) as resp:  # nosec - simple GET
        token_data = json.loads(resp.read().decode("utf-8"))
    token = token_data.get("token")
    if not token:
        return None

    tags_url = f"https://ghcr.io/v2/{repository}/tags/list?n=200"
    req = urllib.request.Request(tags_url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    tags = [t for t in data.get("tags", []) if isinstance(t, str)]
    log_event("version.tags", source="ghcr", repo=repository, count=len(tags))
    return _select_highest_semver(tags)


def _normalize_display(tag: str) -> str:
    """Strip optional 'v' prefix and -/+ suffix; keep original numeric formatting
    (preserves leading zeros in calver-style tags like 25.04.9.4.1)."""
    s = tag[1:] if tag.startswith("v") else tag
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s


def _select_highest_semver(tags: list[str]) -> Optional[str]:
    parsed: List[Tuple[Tuple[int, ...], str]] = []
    for tag in tags:
        t = _parse_extended_tuple(tag)
        if t is not None:
            parsed.append((t, _normalize_display(tag)))
    if not parsed:
        return None
    parsed.sort(key=lambda x: x[0], reverse=True)
    return parsed[0][1]


def _extract_semver_tags(tags: List[str]) -> List[str]:
    """Return tags that are valid semver-like (>=2 numeric components),
    with optional 'v' prefix and -/+ suffix stripped. Original numeric
    formatting (including leading zeros) is preserved."""
    seen: Dict[str, Tuple[int, ...]] = {}
    for tag in tags:
        t = _parse_extended_tuple(tag)
        if t is None:
            continue
        display = _normalize_display(tag)
        seen[display] = t
    return sorted(seen.keys(), key=lambda k: seen[k], reverse=True)


def fetch_all_semver_tags(parsed: ParsedImage) -> List[str]:
    """Fetch all semver-looking tags for a registry/repo, sorted descending.

    Returns normalized tags (e.g., '1.15.12').
    """
    try:
        # Special-case Dapr images: use GitHub tags as the source of truth
        if "ghcr.io" in parsed.registry and parsed.repository.startswith("dapr/"):
            gh_tags = _fetch_github_repo_tags("dapr", "dapr")
            tags = _extract_semver_tags(gh_tags)
            if tags:
                log_event("version.tags", source="github", repo=parsed.repository, count=len(tags))
                return tags
            ghcr_tags = _extract_semver_tags(_fetch_ghcr_tags(parsed.repository))
            log_event("version.tags", source="ghcr", repo=parsed.repository, count=len(ghcr_tags))
            return ghcr_tags
        if "quay.io" in parsed.registry:
            tags = _fetch_quay_tags(parsed.repository)
            log_event("version.tags", source="quay", repo=parsed.repository, count=len(tags))
            return _extract_semver_tags(tags)
        if "docker.io" in parsed.registry or parsed.registry in {"registry-1.docker.io"}:
            tags = _fetch_dockerhub_tags(parsed.repository)
            log_event("version.tags", source="dockerhub", repo=parsed.repository, count=len(tags))
            return _extract_semver_tags(tags)
        if "ghcr.io" in parsed.registry:
            tags = _fetch_ghcr_tags(parsed.repository)
            log_event("version.tags", source="ghcr", repo=parsed.repository, count=len(tags))
            return _extract_semver_tags(tags)
        return []
    except Exception as e:
        log_event("version.tags.error", error=str(e), repo=parsed.repository)
        return []


def compute_upgrade_paths(current: Optional[str], all_tags_desc: List[str]) -> Dict[str, List[str]]:
    """Compute upgrade paths from current to latest, next minor, and next major.

    Returns dict with keys: to_latest, to_next_minor, to_next_major.
    Each list is ascending path (exclusive of current, inclusive of target).
    """
    result: Dict[str, List[str]] = {"to_latest": [], "to_next_minor": [], "to_next_major": []}
    if not current:
        return result
    cur_tuple = _parse_extended_tuple(current)
    if not cur_tuple or len(cur_tuple) < 2:
        return result

    def _tag_t(tag: str) -> Tuple[int, ...]:
        parsed = _parse_extended_tuple(tag)
        return parsed if parsed else (0,)

    # tags are sorted desc; build paths by filtering and then reversing (to ascending)
    higher = [t for t in all_tags_desc if _tag_t(t) > cur_tuple]
    result["to_latest"] = list(reversed(higher))

    cur_major, cur_minor = cur_tuple[0], cur_tuple[1]
    minor_candidates = [t for t in higher if _tag_t(t)[:1] == (cur_major,) and len(_tag_t(t)) >= 2 and _tag_t(t)[1] > cur_minor]
    result["to_next_minor"] = list(reversed(minor_candidates))

    major_candidates = [t for t in higher if _tag_t(t) and _tag_t(t)[0] > cur_major]
    result["to_next_major"] = list(reversed(major_candidates))
    return result


def _fetch_quay_tags(repository: str) -> List[str]:
    url = f"https://quay.io/api/v1/repository/{repository}/tag/?onlyActiveTags=true"
    with urllib.request.urlopen(url, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    return [t.get("name") for t in data.get("tags", []) if t.get("name")]


def _fetch_dockerhub_tags(repository: str, max_tags: int = 1000) -> List[str]:
    """
    Fetch Docker Hub tags for a repository.
    
    Args:
        repository: Repository name (e.g., 'library/nginx' or 'posthog/posthog')
        max_tags: Maximum number of tags to fetch (default: 1000, to prevent slow queries)
    
    Returns:
        List of tag names, limited to max_tags
    """
    def _request(url: str) -> Optional[dict]:
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "patchmgmt/1.0")
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec - simple GET
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError):
            return None

    # Try registry.hub first then hub.docker.com
    urls = [
        f"https://registry.hub.docker.com/v2/repositories/{repository}/tags?page_size=100",
        f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100",
    ]
    all_tags: List[str] = []
    for base in urls:
        url = base
        pages_fetched = 0
        max_pages = (max_tags + 99) // 100  # Round up: 1000 tags = 10 pages
        
        while url and pages_fetched < max_pages:
            data = _request(url)
            if not data:
                break
            all_tags.extend([r.get("name") for r in data.get("results", []) if isinstance(r, dict) and r.get("name")])
            url = data.get("next")
            pages_fetched += 1
            
            # Stop if we've reached the limit
            if len(all_tags) >= max_tags:
                all_tags = all_tags[:max_tags]
                log_event("version.tags.limit_reached", repo=repository, fetched=len(all_tags), max=max_tags)
                break
                
        if all_tags:
            break
    return all_tags


def _fetch_ghcr_tags(repository: str) -> List[str]:
    token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repository}:pull"
    with urllib.request.urlopen(token_url, timeout=5) as resp:  # nosec - simple GET
        token_data = json.loads(resp.read().decode("utf-8"))
    token = token_data.get("token")
    if not token:
        return []
    tags_url = f"https://ghcr.io/v2/{repository}/tags/list?n=200"
    req = urllib.request.Request(tags_url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    return [t for t in data.get("tags", []) if isinstance(t, str)]


def _fetch_github_repo_tags(owner: str, repo: str) -> List[str]:
    """Fetch tag names from GitHub repository (public), first page up to 100.

    For Dapr, we use https://api.github.com/repos/dapr/dapr/tags.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github.v3+json")
    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    # items are objects with 'name'
    if isinstance(data, list):
        return [item.get("name") for item in data if isinstance(item, dict) and item.get("name")]
    return []


def _fetch_github_releases(owner: str, repo: str) -> List[dict]:
    """Fetch releases (first page up to 100) for a GitHub repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github.v3+json")
    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - simple GET
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def _parse_version_tuple(version: str) -> Optional[Tuple[int, int, int]]:
    """Internal helper. Returns the first 3 numeric components (padded with 0 if shorter)."""
    t = _parse_extended_tuple(version)
    if not t:
        return None
    padded = (t + (0, 0, 0))[:3]
    return (padded[0], padded[1], padded[2])


def parse_version_tuple(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Public helper to safely parse potential semver strings.

    Accepts optional 'v' prefix and suffixes like '-debian-11-r3'. Versions with
    more than 3 numeric components (e.g., '25.04.8.4.1') are truncated to the
    first 3 to keep the tuple shape stable for callers. Returns None if not
    semver-like.
    """
    if not version:
        return None
    return _parse_version_tuple(version)


def compute_cves_for_image(parsed: ParsedImage, current: Optional[str], latest: Optional[str]) -> List[str]:
    """Return list of CVE identifiers between current (exclusive) and latest (inclusive).

    Currently implemented for Dapr images (ghcr.io/dapr/*) using GitHub releases tags and notes.
    
    NOTE: This feature is DISABLED because:
    1. It only works for Dapr images
    2. Requires external GitHub API access (network issues on some backends)
    3. Twistlock already provides comprehensive CVE scanning
    """
    # DISABLED: Return empty list - Twistlock provides CVE data
    return []
    
    # Original implementation (kept for reference):
    if not current or not latest:
        return []
    cur_t = _parse_version_tuple(current)
    lat_t = _parse_version_tuple(latest)
    if not cur_t or not lat_t:
        return []
    # Only handle Dapr images via GitHub releases for now
    if "ghcr.io" not in parsed.registry or not parsed.repository.startswith("dapr/"):
        return []
    try:
        releases = _fetch_github_releases("dapr", "dapr")
    except Exception as e:
        # Network errors (timeout, etc.) - skip CVE fetch, don't fail the sync
        log_event("security.cves.fetch_error", repo=parsed.repository, error=str(e))
        return []
    cves: List[str] = []
    for rel in releases:
        tag = rel.get("tag_name") or ""
        # Normalize possible 'v' prefix
        if tag.startswith("v"):
            tag_norm = tag[1:]
        else:
            tag_norm = tag
        vt = _parse_version_tuple(tag_norm)
        if not vt:
            continue
        if cur_t < vt <= lat_t:
            body = rel.get("body") or ""
            for m in re.findall(r"CVE-\d{4}-\d+", body):
                if m not in cves:
                    cves.append(m)
    if cves:
        log_event("security.cves", repo=parsed.repository, current=current, latest=latest, count=len(cves), cves=cves)
    return cves


def build_upgrade_advice(version_diff: str | None, vulnerabilities: List[str]) -> str:
    if vulnerabilities:
        vuln_part = f"Resolve CVEs: {', '.join(vulnerabilities)}. "
    else:
        vuln_part = ""
    if version_diff == "major":
        return f"Plan a major upgrade with testing. {vuln_part}".strip()
    if version_diff == "minor":
        return f"Apply patch upgrades during maintenance. {vuln_part}".strip()
    if version_diff == "same":
        return f"Up to date. {vuln_part}".strip()
    return f"Review available versions. {vuln_part}".strip()
