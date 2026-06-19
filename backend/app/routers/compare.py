"""
Comparison API for comparing resources between clusters/backends.
Uses cached YAML manifests from database for high performance.
Supports federation - can compare resources across remote backends.
"""
from typing import List, Optional, Dict, Any, Tuple
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select
import os
import re
from jose import jwt

from ..db import get_db
from ..models import BackendEndpoint, Resource
from ..logging_utils import log_event

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

JWT_SECRET = os.getenv("JWT_SECRET", "changeme-secret-PLEASE-SET-ENV")
JWT_ALG = "HS256"


def _decode_role(token: str) -> str:
    """Decode JWT token and return user role."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return str(payload.get("role", ""))
    except Exception:
        return ""


def _decode_token_claims(token: str) -> dict:
    """Decode JWT token and return all claims (role, username, etc)."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return {
            "role": str(payload.get("role", "")),
            "username": str(payload.get("sub", ""))
        }
    except Exception:
        return {"role": "", "username": ""}


# Pattern for Kubernetes random suffixes
# Examples: -v8bq9, -2fsjr, -mk4pb, -5xvnm
# MUST contain at least one digit to avoid matching meaningful suffixes like -redis, -kafka
# Uses lookahead (?=.*[0-9]) to require at least one digit
K8S_RANDOM_SUFFIX_PATTERN = re.compile(r'-(?=[a-z0-9]*[0-9])[a-z0-9]{5}$')

# Pattern for ServiceAccount token secrets: <name>-token-<5-char-random>
K8S_TOKEN_SECRET_PATTERN = re.compile(r'-token-[a-z0-9]{5}$')

# Pattern for dockercfg secrets: <name>-dockercfg-<5-char-random>
K8S_DOCKERCFG_PATTERN = re.compile(r'-dockercfg-[a-z0-9]{5}$')


def normalize_k8s_name(name: str, obj_type: str) -> str:
    """
    Normalize Kubernetes object name by removing random suffixes.
    
    Some K8s objects (Secrets, Routes) have random suffixes like -v8bq9 added by
    controllers. This function removes them for comparison purposes.
    
    IMPORTANT: For token/dockercfg secrets, we keep the type suffix to avoid
    matching different secret types together.
    
    Examples:
        - dapr-operator-dockercfg-v8bq9 -> dapr-operator-dockercfg (keeps -dockercfg)
        - autoquerywebapi-2fsjr -> autoquerywebapi
        - kafka-ui-provectus-token-pmlvz -> kafka-ui-provectus-token (keeps -token)
        - my-sa-dockercfg-abc12 -> my-sa-dockercfg (keeps -dockercfg)
        - my-route-k8s9d -> my-route
    
    Args:
        name: The Kubernetes object name
        obj_type: The type of object (secret, route, etc.)
        
    Returns:
        Normalized name without random suffix (but keeping type suffix for secrets)
    """
    # Only normalize for types that commonly have random suffixes
    if obj_type.lower() in ['secret', 'route']:
        # For secrets: keep -token or -dockercfg suffix to distinguish types
        # ServiceAccount token pattern: <name>-token-xxxxx -> <name>-token
        if K8S_TOKEN_SECRET_PATTERN.search(name):
            # Remove only the random part, keep -token
            return K8S_TOKEN_SECRET_PATTERN.sub('-token', name)
        # dockercfg pattern: <name>-dockercfg-xxxxx -> <name>-dockercfg
        if K8S_DOCKERCFG_PATTERN.search(name):
            # Remove only the random part, keep -dockercfg
            return K8S_DOCKERCFG_PATTERN.sub('-dockercfg', name)
        # Generic random suffix: <name>-xxxxx -> <name>
        return K8S_RANDOM_SUFFIX_PATTERN.sub('', name)
    return name


def build_normalized_name_map(names: set, obj_type: str) -> Dict[str, List[str]]:
    """
    Build a map of normalized names to original names.
    
    Args:
        names: Set of original names
        obj_type: The type of object
        
    Returns:
        Dict mapping normalized name -> list of original names
    """
    name_map: Dict[str, List[str]] = {}
    for name in names:
        normalized = normalize_k8s_name(name, obj_type)
        if normalized not in name_map:
            name_map[normalized] = []
        name_map[normalized].append(name)
    return name_map


class CompareRequest(BaseModel):
    """Request body for comparison"""
    cluster1: str  # Backend name for cluster 1
    cluster1_namespaces: List[str] = []  # Empty = all namespaces
    cluster1_kinds: List[str] = []  # Empty = all kinds
    cluster2: str  # Backend name for cluster 2
    cluster2_namespaces: List[str] = []
    cluster2_kinds: List[str] = []
    sync_before_compare: bool = False  # If True, sync selected resources before comparing


# Named arrays that should be compared by key field instead of index
# Format: { "path_suffix": "key_field" }
# Pipe-separated key fields are tried in order (e.g., "configMapRef.name|secretRef.name")
NAMED_ARRAY_KEYS = {
    "env": "name",
    "envFrom": "configMapRef.name|secretRef.name",
    "volumeMounts": "name",
    "volumes": "name",
    "ports": "containerPort",
    "containers": "name",
    "initContainers": "name",
    "imagePullSecrets": "name",
    "hostAliases": "ip",
    "tolerations": "key",
    "topologySpreadConstraints": "topologyKey",
}

# String/primitive arrays: compare by value (set-based) instead of index
VALUE_ARRAY_KEYS = {"args", "command", "finalizers"}


def _get_array_item_key(item: Any, key_field: str) -> Optional[str]:
    """Extract the key value from an array item for matching.
    Supports pipe-separated fallback fields (e.g., 'configMapRef.name|secretRef.name').
    """
    if not isinstance(item, dict):
        return None
    
    candidates = key_field.split("|")
    for single_field in candidates:
        single_field = single_field.strip()
        parts = single_field.split(".")
        value = item
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        if value is not None:
            if len(candidates) > 1:
                return f"{parts[0]}:{value}"
            return str(value)
    
    return None


def _get_named_array_key_field(path: str) -> Optional[str]:
    """
    Check if the current path ends with a named array field.
    Returns the key field to use for matching, or None for index-based comparison.
    e.g., "spec.template.spec.containers[kafka].env" -> "env" -> NAMED_ARRAY_KEYS["env"]
    """
    # Get the last dot-separated component first, then strip any [...]
    last_component = path.rsplit(".", 1)[-1] if "." in path else path
    if "[" in last_component:
        last_component = last_component.split("[")[0]
    
    return NAMED_ARRAY_KEYS.get(last_component)


def _is_value_array_path(path: str) -> bool:
    """Check if path refers to a primitive array that should use value-based comparison."""
    last = path.rsplit(".", 1)[-1] if "." in path else path
    if "[" in last:
        last = last.split("[")[0]
    return last in VALUE_ARRAY_KEYS


def _compare_value_array(
    arr1: List, arr2: List, path: str
) -> List[Dict[str, Any]]:
    """Compare two arrays of primitive values using set-based comparison.
    Shows which values exist only in each cluster instead of misleading index diffs.
    """
    differences = []
    set1 = [str(v) for v in arr1]
    set2 = [str(v) for v in arr2]

    only_in_1 = sorted(set(set1) - set(set2))
    only_in_2 = sorted(set(set2) - set(set1))

    for val in only_in_1:
        differences.append({
            "path": path,
            "type": "missing_in_cluster2",
            "value1": val[:500],
            "value2": None
        })
    for val in only_in_2:
        differences.append({
            "path": path,
            "type": "missing_in_cluster1",
            "value1": None,
            "value2": val[:500]
        })
    return differences


def find_differences(obj1: Any, obj2: Any, path: str = "") -> List[Dict[str, Any]]:
    """
    Find differences between two objects.
    Returns a list of differences with their paths.
    
    For known named arrays (env, volumeMounts, etc.), items are matched by
    their key field (e.g., 'name') instead of array index.
    For string arrays (args, command), items are compared by value (set-based).
    """
    differences = []
    
    if type(obj1) != type(obj2):
        differences.append({
            "path": path or "root",
            "type": "type_mismatch",
            "value1": str(obj1)[:200] if obj1 is not None else None,
            "value2": str(obj2)[:200] if obj2 is not None else None
        })
        return differences
    
    if isinstance(obj1, dict):
        all_keys = set(obj1.keys()) | set(obj2.keys())
        for key in all_keys:
            new_path = f"{path}.{key}" if path else key
            val1 = obj1.get(key)
            val2 = obj2.get(key)
            
            if key not in obj1:
                differences.append({
                    "path": new_path,
                    "type": "missing_in_cluster1",
                    "value1": None,
                    "value2": str(val2)[:500] if val2 is not None else None
                })
            elif key not in obj2:
                differences.append({
                    "path": new_path,
                    "type": "missing_in_cluster2",
                    "value1": str(val1)[:500] if val1 is not None else None,
                    "value2": None
                })
            else:
                differences.extend(find_differences(val1, val2, new_path))
    
    elif isinstance(obj1, list):
        key_field = _get_named_array_key_field(path)
        
        if key_field and obj1 and obj2 and isinstance(obj1[0], dict):
            differences.extend(_compare_named_array(obj1, obj2, path, key_field))
        elif _is_value_array_path(path) and obj1 and obj2 and not isinstance(obj1[0], dict):
            differences.extend(_compare_value_array(obj1, obj2, path))
        else:
            max_len = max(len(obj1), len(obj2)) if obj1 or obj2 else 0
            for i in range(max_len):
                new_path = f"{path}[{i}]"
                if i >= len(obj1):
                    differences.append({
                        "path": new_path,
                        "type": "missing_in_cluster1",
                        "value1": None,
                        "value2": str(obj2[i])[:500]
                    })
                elif i >= len(obj2):
                    differences.append({
                        "path": new_path,
                        "type": "missing_in_cluster2",
                        "value1": str(obj1[i])[:500],
                        "value2": None
                    })
                else:
                    differences.extend(find_differences(obj1[i], obj2[i], new_path))
    
    else:
        if obj1 != obj2:
            differences.append({
                "path": path or "root",
                "type": "value_different",
                "value1": str(obj1)[:500] if obj1 is not None else None,
                "value2": str(obj2)[:500] if obj2 is not None else None
            })
    
    return differences


def _compare_named_array(
    arr1: List[Dict], 
    arr2: List[Dict], 
    path: str, 
    key_field: str
) -> List[Dict[str, Any]]:
    """
    Compare two arrays by matching items based on a key field (e.g., 'name').
    This provides more meaningful diffs for arrays like env, volumeMounts, etc.
    """
    differences = []
    
    # Build lookup maps by key
    map1: Dict[str, Dict] = {}
    map2: Dict[str, Dict] = {}
    
    for item in arr1:
        key = _get_array_item_key(item, key_field)
        if key:
            map1[key] = item
    
    for item in arr2:
        key = _get_array_item_key(item, key_field)
        if key:
            map2[key] = item
    
    all_keys = set(map1.keys()) | set(map2.keys())
    
    for key in sorted(all_keys):
        # Use key name in path for clearer output
        # e.g., "env[KAFKA_CFG_BUFFER_SIZE]" instead of "env[36]"
        item_path = f"{path}[{key}]"
        
        if key not in map1:
            # Item only in cluster2
            item2 = map2[key]
            differences.append({
                "path": item_path,
                "type": "missing_in_cluster1",
                "value1": None,
                "value2": str(item2)[:500]
            })
        elif key not in map2:
            # Item only in cluster1
            item1 = map1[key]
            differences.append({
                "path": item_path,
                "type": "missing_in_cluster2",
                "value1": str(item1)[:500],
                "value2": None
            })
        else:
            # Both have it - compare the items (excluding the key field itself)
            item1 = map1[key]
            item2 = map2[key]
            
            if item1 != item2:
                item_diffs = find_differences(item1, item2, item_path)
                
                # Filter out the key field itself (already matched by definition)
                # Use exact path match to avoid hiding nested fields like valueFrom.configMapKeyRef.name
                exclude_paths = set()
                for kf in key_field.split("|"):
                    kf = kf.strip()
                    exclude_paths.add(f"{item_path}.{kf}")
                
                item_diffs = [d for d in item_diffs
                             if d.get("path", "") not in exclude_paths]
                
                differences.extend(item_diffs)
    
    return differences


async def _quick_sync_resources_for_compare(
    backend_name: str, 
    resources: List[Dict[str, Any]], 
    token: str, 
    db: Session
) -> None:
    """
    Quick sync resources for comparison - only fetches manifest and related objects.
    SKIPS: Twistlock, EOL, latest version checks (much faster).
    
    For local backend: calls quick-sync-manifest with JWT token
    For remote backend: calls quick-sync-manifest with X-Backend-Token
    """
    import httpx
    
    local_backend = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend_name == local_backend:
        local_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            for r in resources:
                try:
                    url = f"{local_url.rstrip('/')}/api/resources/quick-sync-manifest"
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    }
                    body = {
                        "namespace": r["namespace"],
                        "resource_name": r["name"],
                        "kind": r["kind"]
                    }
                    
                    resp = await client.post(url, json=body, headers=headers)
                    
                    if resp.status_code == 200:
                        log_event("compare.quick_sync.success", 
                                 backend=backend_name, resource=r["name"])
                    else:
                        log_event("compare.quick_sync.error", 
                                 backend=backend_name, resource=r["name"], 
                                 status=resp.status_code)
                except Exception as e:
                    log_event("compare.quick_sync.error", 
                             backend=backend_name, resource=r["name"], error=str(e))
    else:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.name == backend_name,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True
        ).first()
        
        if not backend:
            log_event("compare.quick_sync.backend_not_found", backend=backend_name)
            return
        
        verify_ssl = not backend.skip_tls_verify
        
        async with httpx.AsyncClient(timeout=60.0, verify=verify_ssl) as client:
            for r in resources:
                try:
                    url = f"{backend.api_url.rstrip('/')}/api/resources/quick-sync-manifest"
                    headers = {
                        "X-Backend-Token": backend.auth_token,
                        "Content-Type": "application/json"
                    }
                    body = {
                        "namespace": r["namespace"],
                        "resource_name": r["name"],
                        "kind": r["kind"]
                    }
                    
                    resp = await client.post(url, json=body, headers=headers)
                    
                    if resp.status_code == 200:
                        log_event("compare.quick_sync.success", 
                                 backend=backend_name, resource=r["name"])
                    else:
                        log_event("compare.quick_sync.error", 
                                 backend=backend_name, resource=r["name"], 
                                 status=resp.status_code)
                except Exception as e:
                    log_event("compare.quick_sync.error", 
                             backend=backend_name, resource=r["name"], error=str(e))


async def _mini_discovery_for_compare(
    backend_name: str,
    resources_to_check: List[Dict[str, Any]],
    token: str,
    db: Session
) -> List[Dict[str, Any]]:
    """
    Mini-discovery: Check if resources exist in Kubernetes (without being in DB).
    Returns list of resources that EXIST in K8s with their manifests.
    Does NOT save to database - just for compare purposes.
    
    This is used to find resources that are "only in cluster1" but actually
    exist in cluster2's Kubernetes (just not synced to DB yet).
    """
    import httpx
    
    found_resources = []
    local_backend = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend_name == local_backend:
        local_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            for r in resources_to_check:
                try:
                    url = f"{local_url.rstrip('/')}/api/resources/check-exists-in-k8s"
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    }
                    body = {
                        "namespace": r["namespace"],
                        "resource_name": r["name"],
                        "kind": r["kind"]
                    }
                    
                    resp = await client.post(url, json=body, headers=headers)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("exists"):
                            found_resources.append({
                                "name": r["name"],
                                "namespace": r["namespace"],
                                "kind": r["kind"],
                                "manifest": data.get("manifest"),
                                "related_objects": data.get("related_objects"),
                                "product_name": r.get("product_name")
                            })
                            log_event("compare.mini_discovery.found", 
                                     backend=backend_name, resource=r["name"])
                        else:
                            log_event("compare.mini_discovery.not_found", 
                                     backend=backend_name, resource=r["name"])
                except Exception as e:
                    log_event("compare.mini_discovery.error", 
                             backend=backend_name, resource=r["name"], error=str(e))
    else:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.name == backend_name,
            BackendEndpoint.enabled == True,
            BackendEndpoint.approved == True
        ).first()
        
        if not backend:
            log_event("compare.mini_discovery.backend_not_found", backend=backend_name)
            return found_resources
        
        verify_ssl = not backend.skip_tls_verify
        
        async with httpx.AsyncClient(timeout=30.0, verify=verify_ssl) as client:
            for r in resources_to_check:
                try:
                    url = f"{backend.api_url.rstrip('/')}/api/resources/check-exists-in-k8s"
                    headers = {
                        "X-Backend-Token": backend.auth_token,
                        "Content-Type": "application/json"
                    }
                    body = {
                        "namespace": r["namespace"],
                        "resource_name": r["name"],
                        "kind": r["kind"]
                    }
                    
                    resp = await client.post(url, json=body, headers=headers)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("exists"):
                            found_resources.append({
                                "name": r["name"],
                                "namespace": r["namespace"],
                                "kind": r["kind"],
                                "manifest": data.get("manifest"),
                                "related_objects": data.get("related_objects"),
                                "product_name": r.get("product_name")
                            })
                            log_event("compare.mini_discovery.found", 
                                     backend=backend_name, resource=r["name"])
                        else:
                            log_event("compare.mini_discovery.not_found", 
                                     backend=backend_name, resource=r["name"])
                except Exception as e:
                    log_event("compare.mini_discovery.error", 
                             backend=backend_name, resource=r["name"], error=str(e))
    
    return found_resources


@router.get("/compare/backends")
def list_backends_for_compare(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List available backends for comparison"""
    backends = db.query(BackendEndpoint).filter(
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).all()
    
    result = [
        {
            "name": b.name,
            "platform": b.platform,
            "api_url": b.api_url
        }
        for b in backends
    ]
    
    # Ensure local backend is always in the list
    local_name = os.getenv("BACKEND_NAME", "default-backend")
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    local_url = os.getenv("BACKEND_URL", "http://localhost:8000")
    
    if not any(b["name"] == local_name for b in result):
        result.insert(0, {
            "name": local_name,
            "platform": local_platform,
            "api_url": local_url
        })
    
    return result


@router.get("/compare/namespaces/{backend_name}")
async def list_namespaces_for_backend(
    backend_name: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[str]:
    """List namespaces available in a backend (from cached resources)"""
    import httpx
    
    local_backend = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend_name == local_backend:
        # Local backend - query from database
        result = db.execute(
            select(Resource.namespace).distinct()
        ).scalars().all()
        return sorted(list(set(result)))
    
    # Remote backend - forward request
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == backend_name,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).first()
    
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend not found: {backend_name}")
    
    try:
        verify_ssl = not backend.skip_tls_verify
        async with httpx.AsyncClient(timeout=30.0, verify=verify_ssl) as client:
            url = f"{backend.api_url.rstrip('/')}/api/compare/local-namespaces"
            headers = {
                "Authorization": f"Bearer {backend.auth_token}",
                "X-Backend-Token": backend.auth_token,
            }
            log_event("compare.namespaces.remote_call", backend=backend_name,
                      url=url, has_token=bool(backend.auth_token),
                      skip_tls=backend.skip_tls_verify)
            resp = await client.get(url, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                log_event("compare.namespaces.remote_ok", backend=backend_name, count=len(data))
                return data
            else:
                log_event("compare.namespaces.remote_error", backend=backend_name,
                          status=resp.status_code, body=resp.text[:500])
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except httpx.HTTPError as e:
        log_event("compare.namespaces.remote_unreachable", backend=backend_name, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to reach backend: {str(e)}")


@router.get("/compare/local-namespaces")
def list_local_namespaces(
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> List[str]:
    """List namespaces from local database (cached resources).
    Accepts either X-Backend-Token (backend-to-backend) or Bearer JWT."""
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
        token_str = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
        try:
            jwt.decode(token_str, JWT_SECRET, algorithms=[JWT_ALG])
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    result = db.execute(
        select(Resource.namespace).distinct()
    ).scalars().all()
    return sorted(list(set(result)))


@router.get("/compare/resources/{backend_name}")
async def list_resources_for_backend(
    backend_name: str,
    namespaces: str = "",
    kinds: str = "",
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List resources available in a backend for comparison (from cache)"""
    import httpx
    
    ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    kind_list = [k.strip().lower() for k in kinds.split(",") if k.strip()]
    
    local_backend = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend_name == local_backend:
        return _list_local_resources_from_db(db, ns_list, kind_list)
    
    # Remote backend
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == backend_name,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).first()
    
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend not found: {backend_name}")
    
    try:
        verify_ssl = not backend.skip_tls_verify
        async with httpx.AsyncClient(timeout=60.0, verify=verify_ssl) as client:
            url = f"{backend.api_url.rstrip('/')}/api/compare/local-resources"
            params = {"namespaces": namespaces, "kinds": kinds}
            headers = {
                "Authorization": f"Bearer {backend.auth_token}",
                "X-Backend-Token": backend.auth_token,
            }
            resp = await client.get(url, headers=headers, params=params)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to reach backend: {str(e)}")


@router.get("/compare/local-resources")
def list_local_resources(
    namespaces: str = "",
    kinds: str = "",
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List resources from local database.
    Accepts either X-Backend-Token (backend-to-backend) or Bearer JWT."""
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
        token_str = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
        try:
            jwt.decode(token_str, JWT_SECRET, algorithms=[JWT_ALG])
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    kind_list = [k.strip().lower() for k in kinds.split(",") if k.strip()]
    return _list_local_resources_from_db(db, ns_list, kind_list)


def _list_local_resources_from_db(
    db: Session,
    namespaces: List[str],
    kinds: List[str]
) -> List[Dict[str, Any]]:
    """Query resources from local database with optional filters.
    
    Only returns resources that:
    1. Have a product_name (belong to managed products)
    2. Have a cached manifest_yaml
    """
    query = select(Resource)
    
    # Apply namespace filter
    if namespaces:
        query = query.where(Resource.namespace.in_(namespaces))
    
    # Apply kind filter (default to deployment/statefulset/daemonset)
    if kinds:
        query = query.where(Resource.kind.in_(kinds))
    else:
        query = query.where(Resource.kind.in_(["deployment", "statefulset", "daemonset"]))
    
    # Only include resources that have cached manifest AND belong to managed products
    query = query.where(Resource.manifest_yaml.isnot(None))
    query = query.where(Resource.product_name.isnot(None))
    query = query.where(Resource.product_name != "")
    
    resources = db.execute(query).scalars().all()
    
    return [
        {
            "namespace": r.namespace,
            "name": r.resource_name,
            "kind": r.kind,
            "product_name": r.product_name,
            "has_manifest": r.manifest_yaml is not None,
            "has_related_objects": r.related_objects is not None and any(len(v) > 0 for v in (r.related_objects or {}).values()),
            "manifest_updated_at": r.manifest_updated_at.isoformat() if r.manifest_updated_at else None
        }
        for r in resources
    ]


@router.get("/compare/cached-yaml/{backend_name}")
async def get_cached_yaml(
    backend_name: str,
    namespace: str,
    name: str,
    kind: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get cached YAML manifest from database"""
    import httpx
    
    local_backend = os.getenv("BACKEND_NAME", "default-backend")
    
    if backend_name == local_backend:
        return _get_local_cached_yaml(db, namespace, name, kind)
    
    # Remote backend
    backend = db.query(BackendEndpoint).filter(
        BackendEndpoint.name == backend_name,
        BackendEndpoint.enabled == True,
        BackendEndpoint.approved == True
    ).first()
    
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend not found: {backend_name}")
    
    try:
        verify_ssl = not backend.skip_tls_verify
        async with httpx.AsyncClient(timeout=30.0, verify=verify_ssl) as client:
            url = f"{backend.api_url.rstrip('/')}/api/compare/local-cached-yaml"
            params = {"namespace": namespace, "name": name, "kind": kind}
            headers = {
                "Authorization": f"Bearer {backend.auth_token}",
                "X-Backend-Token": backend.auth_token,
            }
            resp = await client.get(url, headers=headers, params=params)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to reach backend: {str(e)}")


@router.get("/compare/local-cached-yaml")
def get_local_cached_yaml(
    namespace: str,
    name: str,
    kind: str,
    x_backend_token: str = Header(None, alias="X-Backend-Token"),
    authorization: str = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get cached YAML from local database.
    Accepts either X-Backend-Token (backend-to-backend) or Bearer JWT."""
    if x_backend_token:
        my_auth_token = os.getenv("BACKEND_AUTH_TOKEN", "")
        backend_type = os.getenv("BACKEND_TYPE", "secondary")
        
        if backend_type == "secondary" and my_auth_token:
            if x_backend_token != my_auth_token:
                raise HTTPException(status_code=403, detail="Invalid backend token")
        else:
            be = db.query(BackendEndpoint).filter(
                BackendEndpoint.auth_token == x_backend_token,
                BackendEndpoint.approved == True
            ).one_or_none()
            if not be:
                raise HTTPException(status_code=403, detail="Invalid backend token")
    elif authorization:
        token_str = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else authorization
        try:
            jwt.decode(token_str, JWT_SECRET, algorithms=[JWT_ALG])
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    return _get_local_cached_yaml(db, namespace, name, kind)


def _get_local_cached_yaml(db: Session, namespace: str, name: str, kind: str) -> Dict[str, Any]:
    """Internal function to get cached YAML from database"""
    resource = db.execute(
        select(Resource).where(
            Resource.namespace == namespace,
            Resource.resource_name == name,
            Resource.kind == kind.lower()
        )
    ).scalar_one_or_none()
    
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource not found: {namespace}/{name} ({kind})")
    
    if not resource.manifest_yaml:
        raise HTTPException(status_code=404, detail=f"No cached manifest for: {namespace}/{name}. Please run SYNC first.")
    
    return {
        "manifest": resource.manifest_yaml,
        "related_objects": resource.related_objects,
        "updated_at": resource.manifest_updated_at.isoformat() if resource.manifest_updated_at else None
    }


@router.post("/compare/execute")
async def execute_comparison(
    request: CompareRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Execute comparison between two clusters using cached YAML manifests.
    Much faster than real-time K8s API calls - uses database-cached data.
    
    Role requirements:
    - Compare only: Admin or Analyst role required
    - Sync before compare: Admin role required (same as SYNC page)
    
    If sync_before_compare is True, syncs all matching resources before comparing.
    This performs the full sync (image, twistlock, EOL, replicas, manifest).
    """
    # Admin or Analyst role required for executing comparisons
    role = _decode_role(token)
    if role not in ["admin", "analyst"]:
        raise HTTPException(status_code=403, detail="Admin or Analyst role required to execute comparisons")
    
    # If sync_before_compare is enabled, admin role is required (same as SYNC page)
    if request.sync_before_compare and role != "admin":
        raise HTTPException(
            status_code=403, 
            detail="Admin role required to sync before compare. Analysts can only compare cached data."
        )
    
    log_event("compare.execute.start", 
              cluster1=request.cluster1, cluster2=request.cluster2,
              sync_before_compare=request.sync_before_compare, role=role)
    
    # Get resources from both clusters (from cache) - initial fetch
    resources1 = await list_resources_for_backend(
        request.cluster1,
        ",".join(request.cluster1_namespaces),
        ",".join(request.cluster1_kinds),
        token, db
    )
    
    resources2 = await list_resources_for_backend(
        request.cluster2,
        ",".join(request.cluster2_namespaces),
        ",".join(request.cluster2_kinds),
        token, db
    )
    
    # If sync_before_compare is enabled, do quick sync (manifest only, no Twistlock/EOL)
    if request.sync_before_compare:
        log_event("compare.quick_sync.start", 
                  cluster1=request.cluster1, cluster2=request.cluster2,
                  resources1_count=len(resources1), resources2_count=len(resources2))
        
        # Quick sync resources on both clusters (manifest + related objects only)
        await _quick_sync_resources_for_compare(request.cluster1, resources1, token, db)
        await _quick_sync_resources_for_compare(request.cluster2, resources2, token, db)
        
        log_event("compare.quick_sync.complete")
        
        # Re-fetch resources after sync to get updated manifests
        resources1 = await list_resources_for_backend(
            request.cluster1,
            ",".join(request.cluster1_namespaces),
            ",".join(request.cluster1_kinds),
            token, db
        )
        
        resources2 = await list_resources_for_backend(
            request.cluster2,
            ",".join(request.cluster2_namespaces),
            ",".join(request.cluster2_kinds),
            token, db
        )
    
    # Create lookup maps by name+kind (ignoring namespace for matching)
    map1 = {f"{r['name']}:{r['kind']}": r for r in resources1}
    map2 = {f"{r['name']}:{r['kind']}": r for r in resources2}
    
    # Find common resources (by name+kind)
    common_keys = set(map1.keys()) & set(map2.keys())
    only_in_1_keys = set(map1.keys()) - set(map2.keys())
    only_in_2_keys = set(map2.keys()) - set(map1.keys())
    
    # Mini-discovery: Check if "only in X" resources actually exist in other cluster's K8s
    # This finds resources that are in K8s but not yet synced to database
    discovered_in_2 = {}  # Resources found in cluster2's K8s (for only_in_1 items)
    discovered_in_1 = {}  # Resources found in cluster1's K8s (for only_in_2 items)
    
    if request.sync_before_compare and (only_in_1_keys or only_in_2_keys):
        log_event("compare.mini_discovery.start",
                  only_in_1_count=len(only_in_1_keys),
                  only_in_2_count=len(only_in_2_keys))
        
        # Check if "only in cluster1" resources exist in cluster2's K8s
        if only_in_1_keys:
            resources_to_check = [map1[k] for k in only_in_1_keys]
            found_in_2 = await _mini_discovery_for_compare(
                request.cluster2, resources_to_check, token, db
            )
            for r in found_in_2:
                key = f"{r['name']}:{r['kind']}"
                discovered_in_2[key] = r
        
        # Check if "only in cluster2" resources exist in cluster1's K8s
        if only_in_2_keys:
            resources_to_check = [map2[k] for k in only_in_2_keys]
            found_in_1 = await _mini_discovery_for_compare(
                request.cluster1, resources_to_check, token, db
            )
            for r in found_in_1:
                key = f"{r['name']}:{r['kind']}"
                discovered_in_1[key] = r
        
        log_event("compare.mini_discovery.complete",
                  discovered_in_cluster2=len(discovered_in_2),
                  discovered_in_cluster1=len(discovered_in_1))
    
    # Update common_keys with discovered resources
    newly_common_keys = set(discovered_in_2.keys()) | set(discovered_in_1.keys())
    common_keys = common_keys | (set(discovered_in_2.keys()) & only_in_1_keys) | (set(discovered_in_1.keys()) & only_in_2_keys)
    
    # Update only_in lists (remove resources that were discovered in other cluster)
    only_in_1_keys = only_in_1_keys - set(discovered_in_2.keys())
    only_in_2_keys = only_in_2_keys - set(discovered_in_1.keys())
    
    results = {
        "cluster1": request.cluster1,
        "cluster2": request.cluster2,
        "common_count": len(common_keys),
        "only_in_cluster1": [map1[k] for k in only_in_1_keys],
        "only_in_cluster2": [map2[k] for k in only_in_2_keys],
        "comparisons": [],
        "discovery_info": {
            "discovered_in_cluster2": len(discovered_in_2),
            "discovered_in_cluster1": len(discovered_in_1)
        } if request.sync_before_compare else None
    }
    
    # Compare common resources using cached YAML or discovered manifests
    for key in common_keys:
        # Determine source of data for each side
        # r1: from map1 (DB) or discovered_in_1 (K8s discovery)
        # r2: from map2 (DB) or discovered_in_2 (K8s discovery)
        r1 = map1.get(key) or discovered_in_1.get(key)
        r2 = map2.get(key) or discovered_in_2.get(key)
        
        if not r1 or not r2:
            continue
        
        try:
            # Initialize response variables
            yaml1_response = {}
            yaml2_response = {}
            
            # Get YAML - either from cache (DB) or from discovery result
            if key in map1:
                # From database cache
                yaml1_response = await get_cached_yaml(
                    request.cluster1, r1["namespace"], r1["name"], r1["kind"],
                    token, db
                )
                yaml1 = yaml1_response.get("manifest", {})
                related1 = yaml1_response.get("related_objects") or {}
            else:
                # From mini-discovery (K8s direct)
                yaml1 = r1.get("manifest", {})
                related1 = r1.get("related_objects") or {}
            
            if key in map2:
                # From database cache
                yaml2_response = await get_cached_yaml(
                    request.cluster2, r2["namespace"], r2["name"], r2["kind"],
                    token, db
                )
                yaml2 = yaml2_response.get("manifest", {})
                related2 = yaml2_response.get("related_objects") or {}
            else:
                # From mini-discovery (K8s direct)
                yaml2 = r2.get("manifest", {})
                related2 = r2.get("related_objects") or {}
            
            # Find differences in main workload
            diffs = find_differences(yaml1, yaml2)
            
            # Find differences in related objects
            related_diffs = {}
            
            # PDB and HPA: Full manifest comparison
            for rel_type in ["pdb", "hpa"]:
                objs1 = related1.get(rel_type, [])
                objs2 = related2.get(rel_type, [])
                
                if objs1 or objs2:
                    # Create lookup by name (these are full manifests)
                    map_r1 = {obj.get("metadata", {}).get("name", ""): obj for obj in objs1}
                    map_r2 = {obj.get("metadata", {}).get("name", ""): obj for obj in objs2}
                    
                    all_names = set(map_r1.keys()) | set(map_r2.keys())
                    
                    type_diffs = []
                    for obj_name in all_names:
                        if obj_name not in map_r1:
                            type_diffs.append({
                                "name": obj_name,
                                "type": "missing_in_cluster1",
                                "differences": []
                            })
                        elif obj_name not in map_r2:
                            type_diffs.append({
                                "name": obj_name,
                                "type": "missing_in_cluster2",
                                "differences": []
                            })
                        else:
                            obj_diffs = find_differences(map_r1[obj_name], map_r2[obj_name])
                            if obj_diffs:
                                type_diffs.append({
                                    "name": obj_name,
                                    "type": "different",
                                    "differences": obj_diffs[:50]
                                })
                    
                    if type_diffs:
                        related_diffs[rel_type] = type_diffs
            
            # Service, ConfigMap, Secret, Route, PVC: Full manifest comparison
            # These now contain full manifest objects (not just names)
            # Special handling:
            # - Secret/ConfigMap: data is hashed, show "data differs" if hash differs
            # - Route: name normalization (5-char random suffix)
            # - PVC: volumeName normalized
            for rel_type in ["service", "configmap", "secret", "route", "pvc"]:
                objs1 = related1.get(rel_type, [])
                objs2 = related2.get(rel_type, [])
                
                if objs1 or objs2:
                    type_diffs = []
                    
                    # Build lookup by name - now these are full manifests
                    def get_obj_name(obj):
                        if isinstance(obj, dict):
                            return obj.get("metadata", {}).get("name", "")
                        return str(obj)  # Fallback for old format (just names)
                    
                    # Check if data is new format (list of dicts) or old format (list of strings)
                    is_new_format1 = objs1 and isinstance(objs1[0], dict)
                    is_new_format2 = objs2 and isinstance(objs2[0], dict)
                    
                    if is_new_format1 and is_new_format2:
                        # New format: full manifest comparison
                        map_r1 = {get_obj_name(obj): obj for obj in objs1}
                        map_r2 = {get_obj_name(obj): obj for obj in objs2}
                        
                        # For routes and secrets: build normalized name maps (they have random suffixes)
                        if rel_type in ["route", "secret"]:
                            norm_map1 = build_normalized_name_map(set(map_r1.keys()), rel_type)
                            norm_map2 = build_normalized_name_map(set(map_r2.keys()), rel_type)
                            
                            all_norm_names = set(norm_map1.keys()) | set(norm_map2.keys())
                            
                            for norm_name in all_norm_names:
                                orig_names1 = norm_map1.get(norm_name, [])
                                orig_names2 = norm_map2.get(norm_name, [])
                                
                                if not orig_names1:
                                    # Missing in cluster1
                                    for orig_name in orig_names2:
                                        type_diffs.append({
                                            "name": orig_name,
                                            "normalized_name": norm_name if norm_name != orig_name else None,
                                            "type": "missing_in_cluster1",
                                            "differences": []
                                        })
                                elif not orig_names2:
                                    # Missing in cluster2
                                    for orig_name in orig_names1:
                                        type_diffs.append({
                                            "name": orig_name,
                                            "normalized_name": norm_name if norm_name != orig_name else None,
                                            "type": "missing_in_cluster2",
                                            "differences": []
                                        })
                                else:
                                    # Both have it - compare manifests
                                    obj1 = map_r1.get(orig_names1[0], {})
                                    obj2 = map_r2.get(orig_names2[0], {})
                                    obj_diffs = find_differences(obj1, obj2)
                                    
                                    # Exclude metadata.name from diffs (random suffix handled by normalization)
                                    obj_diffs = [d for d in obj_diffs if d.get('path') != 'metadata.name']
                                    
                                    # Special handling for secret _data field
                                    if rel_type == "secret" and obj_diffs:
                                        data_fields = ['_data', '_binaryData']
                                        data_diffs = [d for d in obj_diffs if any(df in d.get('path', '') for df in data_fields)]
                                        other_diffs = [d for d in obj_diffs if not any(df in d.get('path', '') for df in data_fields)]
                                        
                                        if data_diffs:
                                            key_diffs = []
                                            for d in data_diffs:
                                                path = d.get('path', '')
                                                diff_type = d.get('type', '')
                                                if '.' in path:
                                                    key_name = path.split('.', 1)[1]
                                                    if diff_type == 'missing_in_cluster1':
                                                        key_diffs.append({"path": f"data.{key_name}", "type": "key_missing_in_cluster1", "value1": f"key '{key_name}' yok", "value2": None})
                                                    elif diff_type == 'missing_in_cluster2':
                                                        key_diffs.append({"path": f"data.{key_name}", "type": "key_missing_in_cluster2", "value1": f"key '{key_name}' yok", "value2": None})
                                                    elif diff_type == 'value_different':
                                                        key_diffs.append({"path": f"data.{key_name}", "type": "key_value_different", "value1": f"key '{key_name}' value differs", "value2": None})
                                            obj_diffs = other_diffs + key_diffs
                                    
                                    if obj_diffs or set(orig_names1) != set(orig_names2):
                                        display_name = orig_names1[0]
                                        if set(orig_names1) != set(orig_names2):
                                            display_name = f"{orig_names1[0]} ↔ {orig_names2[0]}"
                                        
                                        type_diffs.append({
                                            "name": display_name,
                                            "normalized_name": norm_name if norm_name != orig_names1[0] else None,
                                            "type": "different" if obj_diffs else "matched_normalized",
                                            "cluster1_name": orig_names1[0],
                                            "cluster2_name": orig_names2[0],
                                            "differences": obj_diffs[:50] if obj_diffs else []
                                        })
                        else:
                            # For service, configmap, secret, pvc: direct name matching
                            all_names = set(map_r1.keys()) | set(map_r2.keys())
                            
                            for obj_name in all_names:
                                if obj_name not in map_r1:
                                    type_diffs.append({
                                        "name": obj_name,
                                        "type": "missing_in_cluster1",
                                        "differences": []
                                    })
                                elif obj_name not in map_r2:
                                    type_diffs.append({
                                        "name": obj_name,
                                        "type": "missing_in_cluster2",
                                        "differences": []
                                    })
                                else:
                                    # Both have it - compare manifests
                                    obj1 = map_r1[obj_name]
                                    obj2 = map_r2[obj_name]
                                    obj_diffs = find_differences(obj1, obj2)
                                    
                                    # Special handling for secret/configmap _data field
                                    # Show which keys differ without exposing values
                                    if rel_type in ["secret", "configmap"] and obj_diffs:
                                        data_fields = ['_data', '_binaryData']
                                        data_diffs = [d for d in obj_diffs if any(df in d.get('path', '') for df in data_fields)]
                                        other_diffs = [d for d in obj_diffs if not any(df in d.get('path', '') for df in data_fields)]
                                        
                                        if data_diffs:
                                            # Parse data diffs to show key-level changes
                                            key_diffs = []
                                            for d in data_diffs:
                                                path = d.get('path', '')
                                                diff_type = d.get('type', '')
                                                
                                                # Extract key name from path like "_data.my-key" or "_binaryData.my-key"
                                                if '.' in path:
                                                    key_name = path.split('.', 1)[1]
                                                    
                                                    if diff_type == 'missing_in_cluster1':
                                                        key_diffs.append({
                                                            "path": f"data.{key_name}",
                                                            "type": "key_missing_in_cluster1",
                                                            "value1": f"key '{key_name}' yok",
                                                            "value2": None
                                                        })
                                                    elif diff_type == 'missing_in_cluster2':
                                                        key_diffs.append({
                                                            "path": f"data.{key_name}",
                                                            "type": "key_missing_in_cluster2",
                                                            "value1": f"key '{key_name}' yok",
                                                            "value2": None
                                                        })
                                                    elif diff_type == 'value_different':
                                                        key_diffs.append({
                                                            "path": f"data.{key_name}",
                                                            "type": "key_value_different",
                                                            "value1": f"key '{key_name}' value differs",
                                                            "value2": None
                                                        })
                                            
                                            obj_diffs = other_diffs + key_diffs
                                    
                                    if obj_diffs:
                                        type_diffs.append({
                                            "name": obj_name,
                                            "type": "different",
                                            "differences": obj_diffs[:50]
                                        })
                    else:
                        # Old format fallback: name-based existence check
                        names1 = set(objs1 if not is_new_format1 else [get_obj_name(o) for o in objs1])
                        names2 = set(objs2 if not is_new_format2 else [get_obj_name(o) for o in objs2])
                        
                        norm_map1 = build_normalized_name_map(names1, rel_type)
                        norm_map2 = build_normalized_name_map(names2, rel_type)
                        
                        norm_names1 = set(norm_map1.keys())
                        norm_names2 = set(norm_map2.keys())
                        
                        for norm_name in norm_names1 - norm_names2:
                            for orig_name in norm_map1[norm_name]:
                                type_diffs.append({
                                    "name": orig_name,
                                    "normalized_name": norm_name if norm_name != orig_name else None,
                                    "type": "missing_in_cluster2",
                                    "differences": []
                                })
                        
                        for norm_name in norm_names2 - norm_names1:
                            for orig_name in norm_map2[norm_name]:
                                type_diffs.append({
                                    "name": orig_name,
                                    "normalized_name": norm_name if norm_name != orig_name else None,
                                    "type": "missing_in_cluster1",
                                    "differences": []
                                })
                        
                        for norm_name in norm_names1 & norm_names2:
                            orig1 = norm_map1[norm_name]
                            orig2 = norm_map2[norm_name]
                            if set(orig1) != set(orig2) and norm_name != orig1[0]:
                                type_diffs.append({
                                    "name": f"{orig1[0]} ↔ {orig2[0]}",
                                    "normalized_name": norm_name,
                                    "type": "matched_normalized",
                                    "cluster1_name": orig1[0],
                                    "cluster2_name": orig2[0],
                                    "differences": []
                                })
                    
                    if type_diffs:
                        related_diffs[rel_type] = type_diffs
            
            total_related_diffs = sum(len(v) for v in related_diffs.values())
            
            # Determine manifest timestamps
            updated_at1 = yaml1_response.get("updated_at") if key in map1 else None
            updated_at2 = yaml2_response.get("updated_at") if key in map2 else None
            
            # Mark if resource was discovered via mini-discovery (not in DB)
            discovered_from_k8s = key not in map1 or key not in map2
            
            results["comparisons"].append({
                "name": r1["name"],
                "kind": r1["kind"],
                "namespace1": r1["namespace"],
                "namespace2": r2["namespace"],
                "has_differences": len(diffs) > 0 or total_related_diffs > 0,
                "difference_count": len(diffs),
                "differences": diffs[:100],  # Limit to 100 diffs per resource
                "related_differences": related_diffs,
                "related_difference_count": total_related_diffs,
                "yaml1": yaml1,
                "yaml2": yaml2,
                "related1": related1,
                "related2": related2,
                "manifest_updated_at1": updated_at1,
                "manifest_updated_at2": updated_at2,
                "discovered_from_k8s": discovered_from_k8s
            })
        except HTTPException as e:
            log_event("compare.resource_error", 
                      name=r1["name"], kind=r1["kind"], error=e.detail)
            results["comparisons"].append({
                "name": r1["name"],
                "kind": r1["kind"],
                "namespace1": r1["namespace"],
                "namespace2": r2["namespace"],
                "error": e.detail
            })
        except Exception as e:
            log_event("compare.resource_error", 
                      name=r1["name"], kind=r1["kind"], error=str(e))
            results["comparisons"].append({
                "name": r1["name"],
                "kind": r1["kind"],
                "namespace1": r1["namespace"],
                "namespace2": r2["namespace"],
                "error": str(e)
            })
    
    log_event("compare.execute.done",
              cluster1=request.cluster1, cluster2=request.cluster2,
              common=len(common_keys), only1=len(only_in_1_keys), only2=len(only_in_2_keys))
    
    return results


# ============================================================================
# SAVED COMPARISON REPORTS
# ============================================================================

class SaveReportRequest(BaseModel):
    """Request to save a comparison report"""
    name: str  # User-provided name for the report
    cluster1: str
    cluster2: str
    cluster1_namespaces: List[str] = []
    cluster2_namespaces: List[str] = []
    cluster1_kinds: List[str] = []
    cluster2_kinds: List[str] = []
    # Summary counts
    common_count: int
    only_in_cluster1_count: int
    only_in_cluster2_count: int
    with_differences_count: int
    total_diffs_count: int
    # Full comparison data
    comparison_data: Dict[str, Any]


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively sanitize an object for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    # Convert any other objects to string (datetime, etc.)
    return str(obj)


@router.post("/compare/reports")
def save_comparison_report(
    request: SaveReportRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Save a comparison report for later review. Admin role required."""
    import json
    from ..models import ComparisonReport, ConfigKV
    from ..routers.auth import _decode_claims
    
    # Admin or Analyst role required for saving reports
    role = _decode_role(token)
    if role not in ["admin", "analyst"]:
        raise HTTPException(status_code=403, detail="Admin or Analyst role required to save reports")
    
    try:
        claims = _decode_claims(token)
        username = claims.get("username", "unknown")
        
        # Check max reports limit
        max_reports_config = db.query(ConfigKV).filter(ConfigKV.key == "max_comparison_reports").first()
        max_reports = int(max_reports_config.value) if max_reports_config and max_reports_config.value else 20
        
        # Count existing reports
        current_count = db.query(ComparisonReport).count()
        
        # If at limit, delete oldest
        if current_count >= max_reports:
            oldest = db.query(ComparisonReport).order_by(ComparisonReport.created_at.asc()).first()
            if oldest:
                db.delete(oldest)
                log_event("compare.report.deleted.oldest", id=oldest.id, name=oldest.name)
        
        # Sanitize comparison_data for JSON storage
        sanitized_data = _sanitize_for_json(request.comparison_data)
        
        # Create new report
        report = ComparisonReport(
            name=request.name,
            created_by=username,
            cluster1=request.cluster1,
            cluster2=request.cluster2,
            cluster1_namespaces=json.dumps(request.cluster1_namespaces) if request.cluster1_namespaces else None,
            cluster2_namespaces=json.dumps(request.cluster2_namespaces) if request.cluster2_namespaces else None,
            cluster1_kinds=json.dumps(request.cluster1_kinds) if request.cluster1_kinds else None,
            cluster2_kinds=json.dumps(request.cluster2_kinds) if request.cluster2_kinds else None,
            common_count=request.common_count,
            only_in_cluster1_count=request.only_in_cluster1_count,
            only_in_cluster2_count=request.only_in_cluster2_count,
            with_differences_count=request.with_differences_count,
            total_diffs_count=request.total_diffs_count,
            comparison_data=sanitized_data
        )
        
        db.add(report)
        db.commit()
        db.refresh(report)
        
        log_event("compare.report.saved", id=report.id, name=report.name, by=username)
        
        return {
            "id": report.id,
            "name": report.name,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "message": "Report saved successfully"
        }
    except Exception as e:
        log_event("compare.report.save_error", error=str(e), name=request.name)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save report: {str(e)}")


@router.get("/compare/reports")
def list_comparison_reports(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """List all saved comparison reports"""
    import json
    from ..models import ComparisonReport
    
    reports = db.query(ComparisonReport).order_by(ComparisonReport.created_at.desc()).all()
    
    return [
        {
            "id": r.id,
            "name": r.name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "created_by": r.created_by,
            "cluster1": r.cluster1,
            "cluster2": r.cluster2,
            "cluster1_namespaces": json.loads(r.cluster1_namespaces) if r.cluster1_namespaces else [],
            "cluster2_namespaces": json.loads(r.cluster2_namespaces) if r.cluster2_namespaces else [],
            "cluster1_kinds": json.loads(r.cluster1_kinds) if r.cluster1_kinds else [],
            "cluster2_kinds": json.loads(r.cluster2_kinds) if r.cluster2_kinds else [],
            "common_count": r.common_count,
            "only_in_cluster1_count": r.only_in_cluster1_count,
            "only_in_cluster2_count": r.only_in_cluster2_count,
            "with_differences_count": r.with_differences_count,
            "total_diffs_count": r.total_diffs_count
        }
        for r in reports
    ]


@router.get("/compare/reports/{report_id}")
def get_comparison_report(
    report_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get a saved comparison report by ID"""
    import json
    from ..models import ComparisonReport
    
    report = db.query(ComparisonReport).filter(ComparisonReport.id == report_id).first()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    return {
        "id": report.id,
        "name": report.name,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "created_by": report.created_by,
        "cluster1": report.cluster1,
        "cluster2": report.cluster2,
        "cluster1_namespaces": json.loads(report.cluster1_namespaces) if report.cluster1_namespaces else [],
        "cluster2_namespaces": json.loads(report.cluster2_namespaces) if report.cluster2_namespaces else [],
        "cluster1_kinds": json.loads(report.cluster1_kinds) if report.cluster1_kinds else [],
        "cluster2_kinds": json.loads(report.cluster2_kinds) if report.cluster2_kinds else [],
        "common_count": report.common_count,
        "only_in_cluster1_count": report.only_in_cluster1_count,
        "only_in_cluster2_count": report.only_in_cluster2_count,
        "with_differences_count": report.with_differences_count,
        "total_diffs_count": report.total_diffs_count,
        "comparison_data": report.comparison_data
    }


@router.delete("/compare/reports/{report_id}")
def delete_comparison_report(
    report_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Delete a saved comparison report.
    - Admin can delete any report
    - Analyst can only delete their own reports
    """
    from ..models import ComparisonReport
    
    # Get role and username from token
    claims = _decode_token_claims(token)
    role = claims.get("role", "")
    username = claims.get("username", "unknown")
    
    # Only admin or analyst can delete reports
    if role not in ["admin", "analyst"]:
        raise HTTPException(status_code=403, detail="Admin or Analyst role required to delete reports")
    
    report = db.query(ComparisonReport).filter(ComparisonReport.id == report_id).first()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Analyst can only delete their own reports
    if role == "analyst" and report.created_by != username:
        raise HTTPException(status_code=403, detail="You can only delete your own reports")
    
    report_name = report.name
    db.delete(report)
    db.commit()
    
    log_event("compare.report.deleted", id=report_id, name=report_name, by=username)
    
    return {"message": f"Report '{report_name}' deleted successfully"}
