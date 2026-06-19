"""
Dynamic source generation based on managed products and Kubernetes discovery
"""
import os
from typing import List, Dict, Set
from sqlalchemy.orm import Session

from ..models import ManagedProduct
from ..logging_utils import log_event
from .kube_client import KubeClient
from .versioning import parse_image


def generate_dynamic_sources(db: Session, backend_name: str = None, platform_name: str = None) -> List[Dict[str, str]]:
    """
    Dynamically generate sources by discovering resources from Kubernetes
    and matching them against managed products for this specific backend.
    
    Args:
        db: Database session
        backend_name: Name of the backend (defaults to BACKEND_NAME env var)
        platform_name: Platform name (defaults to BACKEND_PLATFORM env var)
    
    Returns list of source entries in the same format as sources.txt:
    [
        {
            "platform": "prod-openshift",
            "namespace": "my-namespace", 
            "kind": "deployment",
            "resource_name": "my-app",
            "product_name": "redis"
        },
        ...
    ]
    """
    
    # Get backend and platform names from environment if not provided
    if not backend_name:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
    if not platform_name:
        platform_name = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    log_event("dynamic_sources.start", backend=backend_name, platform=platform_name)
    
    # Get managed products with match patterns (federation-aware)
    product_patterns = _get_product_match_patterns(db)
    
    if not product_patterns:
        log_event("dynamic_sources.no_managed_products", backend=backend_name)
        return []
    log_event("dynamic_sources.managed_products", backend=backend_name, count=len(product_patterns))
    
    # Discover all resources from Kubernetes
    kube = KubeClient()
    discovered_sources = []
    processed_keys: Set[tuple] = set()  # Track unique (namespace, kind, name)
    
    try:
        # Get all namespaces
        namespaces = kube.list_namespaces()
        log_event("dynamic_sources.namespaces", count=len(namespaces))
        
        # Import exclusion helper
        from .exclusions import should_skip_namespace, should_skip_image
        
        for namespace in namespaces:
            # Skip excluded namespaces
            if should_skip_namespace(namespace, db):
                log_event("dynamic_sources.skip.excluded_namespace", namespace=namespace)
                continue
            
            # Check each resource type
            for kind in ['deployment', 'statefulset', 'daemonset']:
                try:
                    resources = kube.list_resources(namespace, kind)
                    
                    for resource_name, containers in resources.items():
                        # Create unique key
                        key = (namespace, kind, resource_name)
                        if key in processed_keys:
                            continue
                        processed_keys.add(key)
                        
                        # Get images from containers
                        images = []
                        if isinstance(containers, list):
                            images = containers
                        elif isinstance(containers, dict):
                            images = list(containers.values())
                        
                        # Match images against managed product patterns
                        # Longest-pattern-wins to distinguish overlapping patterns
                        matched_product = None
                        for image in images:
                            if not image:
                                continue
                            
                            if should_skip_image(image, db):
                                continue
                            
                            parsed = parse_image(image)
                            if not parsed:
                                continue
                            
                            repo_lower = parsed.repository.lower()
                            
                            best_name = None
                            best_len = 0
                            for pname, patterns in product_patterns.items():
                                for pat in patterns:
                                    if pat in repo_lower and len(pat) > best_len:
                                        best_name = pname
                                        best_len = len(pat)
                            
                            if best_name:
                                matched_product = best_name
                                break
                        
                        # If we found a match, add to sources
                        if matched_product:
                            source = {
                                "platform": platform_name,
                                "namespace": namespace,
                                "kind": kind,
                                "resource_name": resource_name,
                                "product_name": matched_product
                            }
                            discovered_sources.append(source)
                            log_event(
                                "dynamic_sources.matched",
                                namespace=namespace,
                                kind=kind,
                                resource=resource_name,
                                product=matched_product
                            )
                
                except Exception as e:
                    log_event("dynamic_sources.kind_error", namespace=namespace, kind=kind, error=str(e))
                    continue
        
        log_event("dynamic_sources.success", discovered=len(discovered_sources))
        return discovered_sources
        
    except Exception as e:
        log_event("dynamic_sources.error", error=str(e))
        return []


def _get_all_managed_product_names(db: Session) -> Set[str]:
    """Get all managed product names (lowercased) from DB or federation cache."""
    return set(_get_product_match_patterns(db).keys())


def _parse_patterns(raw: str, fallback: str) -> List[str]:
    """Split comma-separated patterns, falling back to [fallback] if empty."""
    if not raw:
        return [fallback]
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts if parts else [fallback]


def _get_product_match_patterns(db: Session) -> Dict[str, List[str]]:
    """Return {product_name: [pattern1, pattern2, ...]} for all managed products.

    If a product has image_pattern (comma-separated), those are used;
    otherwise fall back to [product_name].
    """
    backend_type = os.getenv("BACKEND_TYPE", "primary")
    is_federation_secondary = backend_type == "secondary" and os.getenv("PRIMARY_BACKEND_URL", "")

    if is_federation_secondary:
        from .config_cache import get_config_cache
        cache = get_config_cache()
        products = cache.get_managed_products()
        result: Dict[str, List[str]] = {}
        for p in products:
            name = (p.get("product_name") or "").lower()
            if not name:
                continue
            raw = (p.get("image_pattern") or "").strip().lower()
            result[name] = _parse_patterns(raw, name)
        return result
    else:
        managed_products = db.query(ManagedProduct).all()
        result = {}
        for p in managed_products:
            name = p.product_name.lower()
            raw = (p.image_pattern or "").strip().lower()
            result[name] = _parse_patterns(raw, name)
        return result


def discover_single_product(
    db: Session, 
    product_name: str, 
    platform_name: str = None,
    target_namespace: str = None
) -> List[Dict[str, str]]:
    """
    Discover resources for a single product only.
    Much faster than full discovery - only checks for one product.
    Skips resources that have a longer managed product match (longest-match-wins).
    
    Args:
        db: Database session
        product_name: The product to discover (e.g., "redis", "fluent-bit")
        platform_name: Platform name (defaults to BACKEND_PLATFORM env var)
        target_namespace: If provided, only scan this namespace (memory efficient)
    
    Returns list of discovered sources matching this product.
    """
    if not platform_name:
        platform_name = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    product_lower = product_name.lower().strip()
    if not product_lower:
        return []
    
    # Resolve the match patterns for this product (comma-separated image_pattern or product_name)
    all_patterns = _get_product_match_patterns(db)
    my_patterns = all_patterns.get(product_lower, [product_lower])
    my_max_len = max(len(p) for p in my_patterns)
    
    # Collect patterns from other products that are longer than our longest
    longer_patterns: Set[str] = set()
    for pn, pats in all_patterns.items():
        if pn != product_lower:
            for pat in pats:
                if len(pat) > my_max_len:
                    longer_patterns.add(pat)
    
    log_event("discover_product.start", product=product_name, platform=platform_name, 
              target_namespace=target_namespace or "all")
    
    kube = KubeClient()
    discovered_sources = []
    processed_keys: Set[tuple] = set()
    
    try:
        if target_namespace:
            namespaces = [target_namespace]
        else:
            namespaces = kube.list_namespaces()
        
        from .exclusions import should_skip_namespace, should_skip_image
        
        for namespace in namespaces:
            if should_skip_namespace(namespace, db):
                continue
            
            for kind in ['deployment', 'statefulset', 'daemonset']:
                try:
                    resources = kube.list_resources(namespace, kind)
                    
                    for resource_name, containers in resources.items():
                        key = (namespace, kind, resource_name)
                        if key in processed_keys:
                            continue
                        processed_keys.add(key)
                        
                        images = []
                        if isinstance(containers, list):
                            images = containers
                        elif isinstance(containers, dict):
                            images = list(containers.values())
                        
                        matched = False
                        for image in images:
                            if not image:
                                continue
                            if should_skip_image(image, db):
                                continue
                            
                            parsed = parse_image(image)
                            if not parsed:
                                continue
                            
                            repo_lower = parsed.repository.lower()
                            if any(mp in repo_lower for mp in my_patterns):
                                # Skip if a longer pattern from another product also matches
                                has_longer = any(lp in repo_lower for lp in longer_patterns)
                                if has_longer:
                                    continue
                                matched = True
                                break
                        
                        if matched:
                            source = {
                                "platform": platform_name,
                                "namespace": namespace,
                                "kind": kind,
                                "resource_name": resource_name,
                                "product_name": product_name
                            }
                            discovered_sources.append(source)
                            log_event(
                                "discover_product.matched",
                                namespace=namespace,
                                kind=kind,
                                resource=resource_name,
                                product=product_name
                            )
                
                except Exception as e:
                    continue
        
        log_event("discover_product.success", product=product_name, discovered=len(discovered_sources))
        return discovered_sources
        
    except Exception as e:
        log_event("discover_product.error", product=product_name, error=str(e))
        return []


def get_combined_sources(db: Session, backend_name: str = None, platform_name: str = None) -> List[Dict[str, str]]:
    """
    Get sources from both dynamic discovery and static sources.txt (if exists).
    Dynamic sources take precedence.
    
    Args:
        db: Database session
        backend_name: Name of the backend (defaults to BACKEND_NAME env var)
        platform_name: Platform name (defaults to BACKEND_PLATFORM env var)
    
    Returns combined list of source entries.
    """
    from ..services.sources_reader import read_sources
    
    # Get backend name from environment if not provided
    if not backend_name:
        backend_name = os.getenv("BACKEND_NAME", "default-backend")
    
    # Get dynamic sources for this backend
    dynamic = generate_dynamic_sources(db, backend_name, platform_name)
    
    # Try to read static sources.txt as fallback
    static = []
    try:
        static_items = read_sources()
        static = [
            {
                "platform": item.platform,
                "namespace": item.namespace,
                "kind": item.kind,
                "resource_name": item.resource_name,
                "product_name": item.product_name or ""
            }
            for item in static_items
        ]
    except Exception as e:
        log_event("dynamic_sources.static_read_error", error=str(e))
    
    # Combine: dynamic + static (remove duplicates, dynamic wins)
    seen_keys: Set[tuple] = set()
    combined = []
    
    # Add dynamic first
    for source in dynamic:
        key = (source["platform"], source["namespace"], source["kind"], source["resource_name"])
        if key not in seen_keys:
            seen_keys.add(key)
            combined.append(source)
    
    # Add static if not already present
    for source in static:
        key = (source["platform"], source["namespace"], source["kind"], source["resource_name"])
        if key not in seen_keys:
            seen_keys.add(key)
            combined.append(source)
    
    log_event("dynamic_sources.combined", dynamic=len(dynamic), static=len(static), total=len(combined))
    return combined

