"""
Exclusion list management for registries and namespaces.
Allows admin to configure which registries and namespaces to skip during sync.

Supports both simple patterns and regex:
- Simple prefix: "openshift-" matches anything starting with "openshift-"
- Exact match: "default" matches only "default"
- Regex: "^openshift-.*" (starts with ^) for advanced patterns
"""
from typing import List
import re
from sqlalchemy.orm import Session
from ..models import ConfigKV
from ..logging_utils import log_event


def get_excluded_registries(db: Session) -> List[str]:
    """
    Get list of excluded image registries from config.
    Returns a list of registry prefixes to skip (e.g., ['image-registry.openshift-image-registry.svc']).
    """
    config = db.query(ConfigKV).filter(ConfigKV.key == "excluded_registries").first()
    if not config or not config.value:
        # Default exclusions
        return ["image-registry.openshift-image-registry.svc"]
    
    # Split by comma or newline
    registries = [r.strip() for r in config.value.replace('\n', ',').split(',') if r.strip()]
    return registries


def get_excluded_namespaces(db: Session) -> List[str]:
    """
    Get list of excluded namespace prefixes from config.
    Returns a list of namespace prefixes to skip (e.g., ['openshift-', 'kube-']).
    """
    config = db.query(ConfigKV).filter(ConfigKV.key == "excluded_namespaces").first()
    if not config or not config.value:
        # Default exclusions
        return [
            "openshift-",
            "kube-",
            "kubernetes-",
            "portworx-",
            "ibm-",
            "default"
        ]
    
    # Split by comma or newline
    namespaces = [n.strip() for n in config.value.replace('\n', ',').split(',') if n.strip()]
    return namespaces


def should_skip_namespace(namespace: str, db: Session) -> bool:
    """
    Check if a namespace should be skipped based on exclusion list.
    
    Supports three pattern types:
    1. Regex (starts with ^): "^openshift-.*" matches using regex
    2. Prefix (ends with - or _): "openshift-" matches "openshift-console"
    3. Exact: "default" matches only "default"
    
    Args:
        namespace: The namespace to check
        db: Database session to fetch config
    
    Returns:
        True if namespace should be skipped, False otherwise
    """
    if not namespace:
        return False
    
    excluded = get_excluded_namespaces(db)
    for pattern in excluded:
        if not pattern:
            continue
        
        # Regex pattern (starts with ^)
        if pattern.startswith('^'):
            try:
                if re.match(pattern, namespace):
                    return True
            except re.error as e:
                log_event("exclusions.regex_error", pattern=pattern, error=str(e))
                continue
        
        # Prefix match (ends with - or _)
        elif pattern.endswith('-') or pattern.endswith('_'):
            if namespace.startswith(pattern):
                return True
        
        # Exact match
        else:
            if namespace == pattern:
                return True
    
    return False


def should_skip_image(image: str, db: Session) -> bool:
    """
    Check if an image should be skipped based on registry exclusion list.
    
    Supports three pattern types:
    1. Regex (starts with ^): "^image-registry\\..*\\.svc" matches using regex
    2. Substring: "docker.io/internal" matches if contained in image
    3. Prefix (ends with /): "myregistry.local/" matches images from that registry
    
    Args:
        image: The full image string (e.g., "registry.io/repo/image:tag")
        db: Database session to fetch config
    
    Returns:
        True if image should be skipped, False otherwise
    """
    if not image:
        return False
    
    excluded = get_excluded_registries(db)
    for pattern in excluded:
        if not pattern:
            continue
        
        # Regex pattern (starts with ^)
        if pattern.startswith('^'):
            try:
                if re.search(pattern, image):
                    return True
            except re.error as e:
                log_event("exclusions.regex_error", pattern=pattern, error=str(e))
                continue
        
        # Substring match (default behavior)
        else:
            if pattern in image:
                return True
    
    return False


def set_excluded_registries(db: Session, registries: List[str]) -> None:
    """
    Save excluded registries to config.
    
    Args:
        db: Database session
        registries: List of registry prefixes to exclude
    """
    value = '\n'.join([r.strip() for r in registries if r.strip()])
    
    config = db.query(ConfigKV).filter(ConfigKV.key == "excluded_registries").first()
    if config:
        config.value = value
    else:
        config = ConfigKV(key="excluded_registries", value=value)
        db.add(config)
    
    db.commit()
    log_event("exclusions.registries.updated", count=len(registries))


def set_excluded_namespaces(db: Session, namespaces: List[str]) -> None:
    """
    Save excluded namespaces to config.
    
    Args:
        db: Database session
        namespaces: List of namespace prefixes to exclude
    """
    value = '\n'.join([n.strip() for n in namespaces if n.strip()])
    
    config = db.query(ConfigKV).filter(ConfigKV.key == "excluded_namespaces").first()
    if config:
        config.value = value
    else:
        config = ConfigKV(key="excluded_namespaces", value=value)
        db.add(config)
    
    db.commit()
    log_event("exclusions.namespaces.updated", count=len(namespaces))

