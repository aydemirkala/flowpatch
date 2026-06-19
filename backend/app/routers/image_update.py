"""
Image Update Router - API endpoints for the Update Image feature.
Handles product listing, job creation, approval workflow, and execution.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db, SessionLocal
from ..models import ImageUpdateJob, ImageUpdateResult, ImageUpdateSnapshot, Resource, BackendEndpoint, ResourcePlan
from ..schemas import (
    ImageUpdateJobCreate,
    ImageUpdateJobResponse,
    ImageUpdateResultResponse,
    ImageUpdateApproval,
    ImageUpdateTriggerResponse,
    ProductImageInfo,
)
from ..services.image_update import get_image_update_service
from ..logging_utils import log_event

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
router = APIRouter()


def _decode_token(token: str, db: Session = None) -> dict:
    """Decode JWT token and return claims.
    
    Supports both user JWT tokens and backend auth tokens for federation.
    Backend auth tokens return a special claims dict with role='backend'.
    
    Authentication methods (in order):
    1. JWT user token - standard user authentication
    2. BACKEND_AUTH_TOKEN env var - this backend's own registration token
    3. Backend endpoint auth_token - tokens from registered remote backends
    """
    from jose import jwt
    JWT_SECRET = os.getenv("JWT_SECRET", "changeme-secret-PLEASE-SET-ENV")
    JWT_ALG = "HS256"
    
    # Method 1: Try as JWT user token
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        pass
    
    # Method 2: Check if it matches this backend's own auth token (BACKEND_AUTH_TOKEN)
    # This allows the primary backend to call secondary backends using the registration token
    backend_auth_token = os.getenv("BACKEND_AUTH_TOKEN")
    if backend_auth_token and token == backend_auth_token:
        backend_name = os.getenv("BACKEND_NAME", "unknown-backend")
        log_event("image_update.backend_auth.self", backend=backend_name)
        return {"sub": f"backend:{backend_name}", "role": "backend"}
    
    # Method 3: Check if it's an auth_token from a registered remote backend
    # This allows remote backends to call this (primary) backend
    if db:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.enabled == True,
        ).first()
        
        if backend:
            log_event("image_update.backend_auth.remote", backend=backend.name)
            return {"sub": f"backend:{backend.name}", "role": "backend"}
    
    raise HTTPException(status_code=401, detail="Invalid token")


def _require_admin(token: str) -> str:
    """Require admin role, return username."""
    claims = _decode_token(token)
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return claims.get("sub", "unknown")


def _forward_plans_to_remote_backends(
    db: Session,
    product_name: str,
    target_platforms: Optional[List[str]],
    target_namespaces: Optional[List[str]],
    planned_upgrade_at: Optional[str],
    job_note: Optional[str] = None,
) -> None:
    """Forward plan updates to remote backends in federation setup."""
    import os
    import httpx
    import ssl
    
    local_platform = os.getenv("PLATFORM_NAME", "")
    
    # Get all enabled backend endpoints
    backends = db.query(BackendEndpoint).filter(BackendEndpoint.enabled == True).all()
    
    for backend in backends:
        # Skip if this is the local backend
        if backend.platform == local_platform:
            continue
        
        # Skip if target_platforms specified and doesn't include this backend's platform
        if target_platforms and backend.platform not in target_platforms:
            continue
        
        try:
            # Build request to remote backend
            ssl_context = None
            if backend.skip_tls_verify:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            
            url = f"{backend.api_url.rstrip('/')}/api/resources/set-job-plans"
            
            with httpx.Client(verify=not backend.skip_tls_verify, timeout=10.0) as client:
                resp = client.post(
                    url,
                    json={
                        "product_name": product_name,
                        "target_platforms": [backend.platform] if target_platforms else None,
                        "target_namespaces": target_namespaces,
                        "planned_upgrade_at": planned_upgrade_at,
                        "job_note": job_note,
                    },
                    headers={"Authorization": f"Bearer {backend.auth_token}"},
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    log_event(
                        "image_update.plans_forwarded",
                        backend=backend.name,
                        platform=backend.platform,
                        updated=data.get("updated", 0),
                    )
                else:
                    log_event(
                        "image_update.plans_forward_error",
                        backend=backend.name,
                        status=resp.status_code,
                        error=resp.text[:200],
                    )
        except Exception as e:
            log_event(
                "image_update.plans_forward_error",
                backend=backend.name,
                error=str(e),
            )


# ==================== Product Endpoints ====================

@router.get("/products", response_model=List[dict])
async def list_products_with_images(
    platforms: Optional[str] = None,  # Comma-separated platform names for filtering
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[dict]:
    """List all products with their current and latest images.
    
    In federation mode (primary backend), this queries all registered
    remote backends and merges the results to show products across
    all platforms.
    
    Query Parameters:
        platforms: Optional comma-separated list of platform names to filter.
                   If provided, only queries the specified platforms' backends.
                   Example: ?platforms=staging-openshift,dev-openshift
    
    Supports both user JWT tokens and backend auth tokens for federation.
    """
    claims = _decode_token(token, db)  # Verify token (supports backend auth)
    
    service = get_image_update_service()
    
    # Parse platforms filter
    platform_filter = None
    if platforms:
        platform_filter = [p.strip() for p in platforms.split(',') if p.strip()]
        log_event("image_update.products.platform_filter", platforms=platform_filter)
    
    # Check if this is a backend-to-backend request (no federation needed)
    if claims.get("role") == "backend":
        # Remote backend request - return only local products
        log_event("image_update.products.backend_request", 
                  backend=claims.get("sub", "unknown"))
        products = service.get_products_with_images(db)
        # Apply platform filter if specified
        if platform_filter:
            products = [p for p in products if any(plat in platform_filter for plat in p.get("platforms", []))]
        return products
    
    # User request - use federated query to include products from remote backends
    products = await service.get_products_with_images_federated(db, token, platform_filter)
    
    return products


@router.get("/products/{product_name}", response_model=dict)
def get_product_images(
    product_name: str,
    platforms: Optional[str] = Query(None, description="Comma-separated list of platforms to query"),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Get detailed image info for a specific product.
    
    Uses federated query to include resources from remote backends.
    Supports both user JWT tokens and backend auth tokens for federation.
    
    Args:
        platforms: Optional comma-separated list of platform names. If provided,
                  only backends serving those platforms will be queried (faster).
    """
    claims = _decode_token(token, db)
    
    service = get_image_update_service()
    
    # Parse platforms parameter
    platform_list = [p.strip() for p in platforms.split(',')] if platforms else None
    
    # Check if this is a backend-to-backend request
    if claims.get("role") == "backend":
        # Remote backend request - return only local product
        product = service.get_product_details(db, product_name)
    else:
        # User request - use federated query with platform filter
        product = service.get_product_details_federated(db, product_name, token, platforms=platform_list)
    
    if not product:
        raise HTTPException(status_code=404, detail=f"Product not found: {product_name}")
    
    return product


# ==================== Job Management Endpoints ====================

@router.get("/jobs", response_model=List[ImageUpdateJobResponse])
def list_update_jobs(
    status: Optional[str] = None,
    product_name: Optional[str] = None,
    limit: int = 50,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[ImageUpdateJobResponse]:
    """List image update jobs."""
    _decode_token(token)
    
    query = db.query(ImageUpdateJob)
    
    if status:
        query = query.filter(ImageUpdateJob.status == status)
    if product_name:
        query = query.filter(ImageUpdateJob.product_name == product_name)
    
    jobs = query.order_by(ImageUpdateJob.created_at.desc()).limit(limit).all()
    
    result = []
    for job in jobs:
        # Calculate results summary
        results_summary = None
        if job.results:
            results_summary = {
                "total": len(job.results),
                "success": sum(1 for r in job.results if r.status == "success"),
                "failed": sum(1 for r in job.results if r.status == "failed"),
                "skipped": sum(1 for r in job.results if r.status == "skipped"),
                "rolled_back": sum(1 for r in job.results if r.status == "rolled_back"),
            }
        
        result.append(ImageUpdateJobResponse(
            id=job.id,
            product_name=job.product_name,
            source_image=job.source_image,
            target_image=job.target_image,
            image_source=job.image_source,
            target_platforms=json.loads(job.target_platforms) if job.target_platforms else None,
            target_namespaces=json.loads(job.target_namespaces) if job.target_namespaces else None,
            field_edits=json.loads(job.field_edits) if job.field_edits else None,
            helm_managed_ack=job.helm_managed_ack or False,
            scheduled_at=job.scheduled_at,
            status=job.status,
            approval_required=job.approval_required,
            health_check_enabled=job.health_check_enabled,
            health_check_mode=job.health_check_mode,
            approved_by=job.approved_by,
            approved_at=job.approved_at,
            trigger_token=job.trigger_token,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_by=job.created_by,
            created_at=job.created_at,
            notes=job.notes,
            results_summary=results_summary,
            progress_total=job.progress_total,
            progress_current=job.progress_current,
            progress_message=job.progress_message,
            cancel_requested=job.cancel_requested or False,
            cancelled_at=job.cancelled_at,
        ))
    
    return result


_ALLOWED_EDIT_TYPES = {"env", "resource", "command", "args", "label", "annotation", "affinity"}
_AFFINITY_SUBKEYS = {"podantiaffinity", "podaffinity", "nodeaffinity", "all"}


def _validate_field_edits(field_edits):
    """Validate the whitelisted edit list — no free-form patches reach the cluster.
    Returns the (unchanged) list or None; raises HTTPException(400) on anything malformed."""
    if field_edits is None:
        return None
    if not isinstance(field_edits, list):
        raise HTTPException(status_code=400, detail="field_edits must be a list")
    for i, e in enumerate(field_edits):
        if not isinstance(e, dict):
            raise HTTPException(status_code=400, detail=f"field_edits[{i}] must be an object")
        t = (e.get("type") or "").lower()
        op = (e.get("op") or "set").lower()
        if t not in _ALLOWED_EDIT_TYPES:
            raise HTTPException(status_code=400, detail=f"field_edits[{i}]: unsupported type '{t}'")
        if t in ("env", "label", "annotation", "affinity") and op not in ("set", "remove"):
            raise HTTPException(status_code=400, detail=f"field_edits[{i}]: op must be set|remove")
        if t == "env":
            if not e.get("name"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: env requires 'name'")
            if op == "set" and e.get("value") is None:
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: env set requires 'value'")
        elif t == "resource":
            if (e.get("kind") or "").lower() not in ("request", "limit"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: resource.kind must be request|limit")
            if (e.get("name") or "").lower() not in ("cpu", "memory"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: resource.name must be cpu|memory")
            if not e.get("value"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: resource requires 'value'")
        elif t in ("command", "args"):
            if not isinstance(e.get("value"), list):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: {t} 'value' must be a list of strings")
        elif t in ("label", "annotation"):
            if not e.get("key"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: {t} requires 'key'")
            if (e.get("target") or "pod").lower() not in ("pod", "workload"):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: {t}.target must be pod|workload")
            if op == "set" and e.get("value") is None:
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: {t} set requires 'value'")
        elif t == "affinity":
            if (e.get("subkey") or "all").lower() not in _AFFINITY_SUBKEYS:
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: affinity.subkey must be podAntiAffinity|podAffinity|nodeAffinity|all")
            if op == "set" and not isinstance(e.get("value"), dict):
                raise HTTPException(status_code=400, detail=f"field_edits[{i}]: affinity set requires an object 'value'")
    return field_edits


def _helm_managed_targets(db, service, body) -> list:
    """Local target resources that are Helm-managed (helm_status ok/drift) — used to gate
    field-edit jobs behind helm_managed_ack. Remote targets are gated on their own backend."""
    targets = body.target_resource_keys or body.target_resource_ids
    try:
        matches = service.get_matching_resources(
            db, body.product_name, body.target_platforms, body.target_namespaces, targets)
    except Exception:
        return []
    out = []
    for r in matches:
        if (getattr(r, "helm_status", None) or "") in ("ok", "drift"):
            out.append({"platform": r.platform, "namespace": r.namespace, "kind": r.kind,
                        "resource_name": r.resource_name, "release": getattr(r, "helm_release_name", None)})
    return out


@router.post("/jobs", response_model=ImageUpdateJobResponse)
def create_update_job(
    body: ImageUpdateJobCreate,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> ImageUpdateJobResponse:
    """Create a new image update job."""
    username = _require_admin(token)
    
    # Skip slow federated validation - user already selected product from UI
    # Just do a quick local check that product_name is not empty
    if not body.product_name or not body.product_name.strip():
        raise HTTPException(status_code=400, detail="Product name is required")
    
    service = get_image_update_service()

    # Phase 1: validate field edits; require at least an image or an edit; gate Helm-managed.
    field_edits = _validate_field_edits(body.field_edits)
    has_image = bool(body.target_image and body.target_image.strip())
    if not has_image and not field_edits:
        raise HTTPException(status_code=400, detail="Provide target_image and/or field_edits")
    # Phase 1.4: field edits now run on the resource's owning backend (local or remote-
    # forwarded), so remote targets are allowed. The Helm-managed ack gate still applies.
    if field_edits and not body.helm_managed_ack:
        helm_targets = _helm_managed_targets(db, service, body)
        if helm_targets:
            raise HTTPException(status_code=409, detail={
                "error": "helm_managed_ack_required",
                "message": ("Some targets are Helm-managed; a direct patch is reverted by the next "
                            "helm upgrade. Resubmit with helm_managed_ack=true to proceed."),
                "helm_managed": helm_targets,
            })

    # Generate trigger token for Azure DevOps integration
    trigger_token = secrets.token_urlsafe(32)
    
    # Prefer target_resource_keys over deprecated target_resource_ids for cross-backend support
    target_resources_value = None
    if body.target_resource_keys:
        # Store keys as JSON - format: ["platform|namespace|kind|name", ...]
        target_resources_value = json.dumps(body.target_resource_keys)
    elif body.target_resource_ids:
        # Backwards compatibility: convert IDs to keys by looking up resources
        # Note: This only works for local resources
        target_resources_value = json.dumps(body.target_resource_ids)
    
    job = ImageUpdateJob(
        product_name=body.product_name,
        source_image=body.source_image,  # Filter: only update containers matching this image repo
        target_image=body.target_image or "",  # "" for fields-only jobs (column is NOT NULL)
        image_source=body.image_source,
        target_platforms=json.dumps(body.target_platforms) if body.target_platforms else None,
        target_namespaces=json.dumps(body.target_namespaces) if body.target_namespaces else None,
        target_resources=target_resources_value,
        field_edits=json.dumps(field_edits) if field_edits else None,
        helm_managed_ack=body.helm_managed_ack,
        scheduled_at=body.scheduled_at,
        approval_required=body.approval_required,
        health_check_enabled=body.health_check_mode != "off",
        health_check_mode=body.health_check_mode,
        status="pending_approval" if body.approval_required else "approved",
        trigger_token=trigger_token,
        created_by=username,
        notes=body.notes,
    )
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    log_event(
        "image_update.job.created",
        job_id=job.id,
        product=body.product_name,
        target_image=body.target_image,
        created_by=username,
    )
    
    # If scheduled, update ResourcePlan for affected resources
    if body.scheduled_at:
        # Prefer resource keys over deprecated IDs
        target_resources = body.target_resource_keys or body.target_resource_ids
        affected_resources = service.get_matching_resources(
            db,
            body.product_name,
            body.target_platforms,
            body.target_namespaces,
            target_resources,
        )
        
        for resource in affected_resources:
            # Upsert ResourcePlan
            plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == resource.id).first()
            if plan:
                plan.planned_upgrade_at = body.scheduled_at
                if not plan.note:
                    plan.note = f"Job #{job.id}: {body.target_image}"
            else:
                plan = ResourcePlan(
                    resource_id=resource.id,
                    planned_upgrade_at=body.scheduled_at,
                    note=f"Job #{job.id}: {body.target_image}",
                )
                db.add(plan)
        
        db.commit()
        log_event(
            "image_update.job.plans_set",
            job_id=job.id,
            resources_count=len(affected_resources),
        )
        
        # Federation: Forward plan updates to remote backends
        _forward_plans_to_remote_backends(
            db=db,
            product_name=body.product_name,
            target_platforms=body.target_platforms,
            target_namespaces=body.target_namespaces,
            planned_upgrade_at=body.scheduled_at.isoformat() if body.scheduled_at else None,
            job_note=f"Job #{job.id}: {body.target_image}",
        )
    
    return ImageUpdateJobResponse(
        id=job.id,
        product_name=job.product_name,
        source_image=job.source_image,
        target_image=job.target_image,
        image_source=job.image_source,
        target_platforms=json.loads(job.target_platforms) if job.target_platforms else None,
        target_namespaces=json.loads(job.target_namespaces) if job.target_namespaces else None,
        field_edits=json.loads(job.field_edits) if job.field_edits else None,
        helm_managed_ack=job.helm_managed_ack or False,
        scheduled_at=job.scheduled_at,
        status=job.status,
        approval_required=job.approval_required,
        health_check_enabled=job.health_check_enabled,
        health_check_mode=job.health_check_mode,
        approved_by=job.approved_by,
        approved_at=job.approved_at,
        trigger_token=job.trigger_token,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_by=job.created_by,
        created_at=job.created_at,
        notes=job.notes,
        results_summary=None,
        progress_total=job.progress_total,
        progress_current=job.progress_current,
        progress_message=job.progress_message,
        cancel_requested=job.cancel_requested or False,
        cancelled_at=job.cancelled_at,
    )


@router.get("/jobs/{job_id}", response_model=ImageUpdateJobResponse)
def get_job_details(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> ImageUpdateJobResponse:
    """Get details of a specific job."""
    _decode_token(token)
    
    # Force fresh read from database for live progress updates
    # expire_all + commit closes any pending transaction and forces a new read
    db.expire_all()
    db.commit()  # Ensure we start with a clean transaction
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Debug log for live progress troubleshooting
    if job.status == "executing":
        log_event(
            "image_update.job.get_details.progress",
            job_id=job.id,
            status=job.status,
            progress_current=job.progress_current,
            progress_total=job.progress_total,
            progress_message=job.progress_message[:50] if job.progress_message else None,
        )
    
    results_summary = None
    if job.results:
        results_summary = {
            "total": len(job.results),
            "success": sum(1 for r in job.results if r.status == "success"),
            "failed": sum(1 for r in job.results if r.status == "failed"),
            "skipped": sum(1 for r in job.results if r.status == "skipped"),
            "rolled_back": sum(1 for r in job.results if r.status == "rolled_back"),
        }
    
    return ImageUpdateJobResponse(
        id=job.id,
        product_name=job.product_name,
        source_image=job.source_image,
        target_image=job.target_image,
        image_source=job.image_source,
        target_platforms=json.loads(job.target_platforms) if job.target_platforms else None,
        target_namespaces=json.loads(job.target_namespaces) if job.target_namespaces else None,
        field_edits=json.loads(job.field_edits) if job.field_edits else None,
        helm_managed_ack=job.helm_managed_ack or False,
        scheduled_at=job.scheduled_at,
        status=job.status,
        approval_required=job.approval_required,
        health_check_enabled=job.health_check_enabled,
        health_check_mode=job.health_check_mode,
        approved_by=job.approved_by,
        approved_at=job.approved_at,
        trigger_token=job.trigger_token,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_by=job.created_by,
        created_at=job.created_at,
        notes=job.notes,
        results_summary=results_summary,
        progress_total=job.progress_total,
        progress_current=job.progress_current,
        progress_message=job.progress_message,
        cancel_requested=job.cancel_requested or False,
        cancelled_at=job.cancelled_at,
    )


@router.get("/jobs/{job_id}/results", response_model=List[ImageUpdateResultResponse])
def get_job_results(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> List[ImageUpdateResultResponse]:
    """Get results of a job execution with detailed logs and health check info."""
    _decode_token(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    results = db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id
    ).order_by(ImageUpdateResult.executed_at.desc()).all()
    
    # Parse JSON fields for response
    response = []
    for r in results:
        result_dict = {
            "id": r.id,
            "platform": r.platform,
            "namespace": r.namespace,
            "resource_name": r.resource_name,
            "kind": r.kind,
            "container_name": r.container_name,
            "old_image": r.old_image,
            "new_image": r.new_image,
            "status": r.status,
            "error_message": r.error_message,
            "executed_at": r.executed_at,
            "health_check_passed": r.health_check_passed,
            "health_check_message": r.health_check_message,
            "health_checked_at": r.health_checked_at,
            "was_rolled_back": r.was_rolled_back or False,
            "rollback_reason": r.rollback_reason,
            # Parse JSON fields
            "pre_patch_state": json.loads(r.pre_patch_state) if r.pre_patch_state else None,
            "post_patch_state": json.loads(r.post_patch_state) if r.post_patch_state else None,
            "execution_log": json.loads(r.execution_log) if r.execution_log else None,
            # Phase 1: applied field-edits audit + Helm flag (pre_patch_manifest stays internal/encrypted).
            "field_changes": json.loads(r.field_changes) if r.field_changes else None,
            "helm_managed": r.helm_managed,
        }
        response.append(ImageUpdateResultResponse(**result_dict))
    
    return response


@router.get("/jobs/{job_id}/results/export")
def export_job_results(
    job_id: int,
    format: str = "csv",
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """Export job results as CSV or JSON for large result sets."""
    from fastapi.responses import StreamingResponse
    import io
    import csv
    
    _decode_token(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    results = db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id
    ).order_by(ImageUpdateResult.executed_at.asc()).all()
    
    if format == "json":
        result_items = []
        for r in results:
            result_items.append({
                "id": r.id,
                "platform": r.platform,
                "namespace": r.namespace,
                "resource_name": r.resource_name,
                "kind": r.kind,
                "container_name": r.container_name,
                "old_image": r.old_image,
                "new_image": r.new_image,
                "status": r.status,
                "error_message": r.error_message,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                "health_check_passed": r.health_check_passed,
                "health_check_message": r.health_check_message,
                "was_rolled_back": r.was_rolled_back or False,
                "rollback_reason": r.rollback_reason,
                "execution_log": json.loads(r.execution_log) if r.execution_log else None,
            })
        
        data = {
            "job": {
                "id": job.id,
                "product_name": job.product_name,
                "target_image": job.target_image,
                "status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            },
            "results": result_items,
        }
        
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=job_{job_id}_results.json"}
        )
    
    else:  # CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Job info rows
        writer.writerow(["# Job ID", job.id])
        writer.writerow(["# Product", job.product_name])
        writer.writerow(["# Target Image", job.target_image])
        writer.writerow(["# Status", job.status])
        writer.writerow(["# Started At", job.started_at.strftime('%Y-%m-%d %H:%M:%S UTC') if job.started_at else "N/A"])
        writer.writerow(["# Finished At", job.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC') if job.finished_at else "N/A"])
        writer.writerow([])
        
        # Header
        writer.writerow([
            "ID", "Platform", "Namespace", "Resource", "Kind", "Container",
            "Old Image", "New Image", "Manifest Changes", "Status", "Health Check", "Health Message",
            "Was Rolled Back", "Rollback Reason", "Error Message", "Executed At"
        ])

        # Rows
        for r in results:
            _changes_csv = ""
            if r.field_changes:
                try:
                    _changes_csv = "; ".join(
                        f"{c.get('path')}: {c.get('old')} -> {c.get('new')}" for c in json.loads(r.field_changes)[:20])
                except Exception:
                    _changes_csv = ""
            writer.writerow([
                r.id,
                r.platform,
                r.namespace,
                r.resource_name,
                r.kind,
                r.container_name or "",
                r.old_image or "",
                r.new_image,
                _changes_csv,
                r.status,
                "Passed" if r.health_check_passed else ("Failed" if r.health_check_passed is False else "N/A"),
                r.health_check_message or "",
                "Yes" if r.was_rolled_back else "No",
                r.rollback_reason or "",
                r.error_message or "",
                r.executed_at.isoformat() if r.executed_at else "",
            ])
        
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),  # BOM for Excel
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=job_{job_id}_results.csv"}
        )


@router.get("/jobs/{job_id}/results/pdf")
def export_job_results_pdf(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    """Export job results as printable HTML (save as PDF in browser)."""
    from fastapi.responses import HTMLResponse
    
    _decode_token(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    results = db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id
    ).order_by(ImageUpdateResult.executed_at.asc()).all()
    
    # Count results by status
    success_count = sum(1 for r in results if r.status == 'success')
    failed_count = sum(1 for r in results if r.status == 'failed')
    skipped_count = sum(1 for r in results if r.status == 'skipped')
    rolled_back_count = sum(1 for r in results if r.was_rolled_back)
    
    # Generate table rows
    table_rows = ""
    for r in results:
        status_color = {
            'success': '#22c55e',
            'failed': '#ef4444',
            'skipped': '#6b7280',
            'rolled_back': '#f59e0b',
        }.get(r.status, '#6b7280')
        
        if r.was_rolled_back:
            status_color = '#f59e0b'
            status_text = 'ROLLED BACK'
        else:
            status_text = r.status.upper()

        # Changes column: applied manifest edits (Update Product) or the image change. HTML-
        # escaped since field values are arbitrary (avoid injecting into the report).
        import html as _html
        _changes_html = ""
        if r.field_changes:
            try:
                _fc = json.loads(r.field_changes)
                _changes_html = "<br>".join(
                    _html.escape(f"{c.get('path')}: {c.get('old')} -> {c.get('new')}") for c in _fc[:20])
            except Exception:
                _changes_html = ""
        elif r.old_image and r.new_image and r.old_image != r.new_image:
            _changes_html = _html.escape(f"image: {r.old_image} -> {r.new_image}")

        table_rows += f"""
        <tr>
            <td>{r.platform}</td>
            <td>{r.namespace}</td>
            <td>{r.resource_name}</td>
            <td>{r.kind}</td>
            <td>{r.container_name or '-'}</td>
            <td style="max-width:150px;word-break:break-all;font-size:9px">{r.old_image or '-'}</td>
            <td style="max-width:150px;word-break:break-all;font-size:9px">{r.new_image or '-'}</td>
            <td style="max-width:220px;word-break:break-all;font-size:9px">{_changes_html}</td>
            <td style="color:{status_color};font-weight:600">{status_text}</td>
            <td style="max-width:150px;word-break:break-all;font-size:9px;color:#ef4444">{r.error_message or ''}</td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Job #{job.id} Results - {job.product_name}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; font-size: 11px; }}
            h1 {{ color: #1e3a5f; font-size: 18px; margin-bottom: 5px; }}
            .subtitle {{ color: #666; font-size: 12px; margin-bottom: 20px; }}
            .stats {{ display: flex; gap: 20px; margin-bottom: 15px; font-size: 11px; flex-wrap: wrap; }}
            .stat {{ background: #f5f5f5; padding: 8px 12px; border-radius: 4px; }}
            .stat.success {{ border-left: 3px solid #22c55e; }}
            .stat.failed {{ border-left: 3px solid #ef4444; }}
            .stat.skipped {{ border-left: 3px solid #6b7280; }}
            .stat.rollback {{ border-left: 3px solid #f59e0b; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 9px; margin-top: 15px; }}
            th {{ background: #1e3a5f; color: white; padding: 6px 4px; text-align: left; font-weight: 600; }}
            td {{ padding: 5px 4px; border-bottom: 1px solid #ddd; vertical-align: top; }}
            tr:nth-child(even) {{ background: #f9f9f9; }}
            .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-bottom: 15px; }}
            .info-item {{ background: #f5f5f5; padding: 8px; border-radius: 4px; }}
            .info-label {{ color: #666; font-size: 10px; margin-bottom: 2px; }}
            .info-value {{ font-weight: 600; word-break: break-all; }}
            .footer {{ margin-top: 20px; font-size: 10px; color: #666; text-align: center; }}
            @media print {{
                body {{ margin: 10px; }}
                .no-print {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <h1>Image Update Job #{job.id} Results</h1>
        <div class="subtitle">Product: {job.product_name} | Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
        
        <div class="info-grid">
            <div class="info-item">
                <div class="info-label">Target Image</div>
                <div class="info-value" style="font-size:10px">{job.target_image}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Status</div>
                <div class="info-value">{job.status.upper()}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Created By</div>
                <div class="info-value">{job.created_by}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Started At</div>
                <div class="info-value">{job.started_at.strftime('%Y-%m-%d %H:%M:%S UTC') if job.started_at else 'N/A'}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Finished At</div>
                <div class="info-value">{job.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC') if job.finished_at else 'N/A'}</div>
            </div>
        </div>
        
        <div class="stats">
            <div class="stat success"><strong>{success_count}</strong> Success</div>
            <div class="stat failed"><strong>{failed_count}</strong> Failed</div>
            <div class="stat skipped"><strong>{skipped_count}</strong> Skipped</div>
            <div class="stat rollback"><strong>{rolled_back_count}</strong> Rolled Back</div>
            <div class="stat"><strong>{len(results)}</strong> Total</div>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>Platform</th>
                    <th>Namespace</th>
                    <th>Resource</th>
                    <th>Kind</th>
                    <th>Container</th>
                    <th>Old Image</th>
                    <th>New Image</th>
                    <th>Changes</th>
                    <th>Status</th>
                    <th>Error</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
        
        <div class="footer">PatchMgmt - Image Update Job Report</div>
        
        <div class="no-print" style="margin-top:20px;text-align:center">
            <button onclick="window.print()" style="padding:10px 20px;font-size:14px;cursor:pointer;background:#1e3a5f;color:white;border:none;border-radius:4px">
                📄 Print / Save as PDF
            </button>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


# ==================== Approval and Execution Endpoints ====================

@router.post("/jobs/{job_id}/approve")
def approve_job(
    job_id: int,
    body: ImageUpdateApproval,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Approve or reject an image update job."""
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != "pending_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be approved in status: {job.status}"
        )
    
    if body.approved:
        job.status = "approved"
        job.approved_by = username
        job.approved_at = datetime.now(timezone.utc)
        if body.notes:
            job.notes = (job.notes or "") + f"\n[Approval] {body.notes}"
        
        log_event("image_update.job.approved", job_id=job.id, approved_by=username)
    else:
        job.status = "cancelled"
        job.cancelled_at = datetime.now(timezone.utc)
        if body.notes:
            job.notes = (job.notes or "") + f"\n[Rejected] {body.notes}"
        
        log_event("image_update.job.rejected", job_id=job.id, rejected_by=username)
    
    db.commit()
    
    return {
        "ok": True,
        "status": job.status,
        "message": f"Job {'approved' if body.approved else 'rejected'} by {username}",
    }


def _execute_job_worker(job_id: int, username: str):
    """Background worker to execute a job. Uses its own DB session."""
    import logging
    import sys
    
    # Use multiple methods to ensure we see the log
    print(f"[WORKER] Thread started for job {job_id}", flush=True)
    sys.stdout.flush()
    logging.warning(f"[WORKER] Thread started for job {job_id} by {username}")
    log_event("image_update.job.worker.thread_started", job_id=job_id, username=username)
    
    db = None
    try:
        db = SessionLocal()
        print(f"[WORKER] DB connected for job {job_id}", flush=True)
        log_event("image_update.job.worker.db_connected", job_id=job_id)
        
        job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
        if not job:
            log_event("image_update.job.worker.not_found", job_id=job_id)
            return
        
        log_event("image_update.job.worker.start", job_id=job_id, triggered_by=username)
        
        from ..models import ConfigKV
        stuck_row = db.query(ConfigKV).filter(ConfigKV.key == "stuck_detection_seconds").first()
        crash_row = db.query(ConfigKV).filter(ConfigKV.key == "crash_tolerance_seconds").first()
        bs_row = db.query(ConfigKV).filter(ConfigKV.key == "patch_batch_size").first()
        bp_row = db.query(ConfigKV).filter(ConfigKV.key == "patch_batch_pause_seconds").first()
        stuck_det = int(stuck_row.value) if stuck_row else 300
        crash_tol = int(crash_row.value) if crash_row else 120
        batch_size = int(bs_row.value) if bs_row else 10
        batch_pause = int(bp_row.value) if bp_row else 2

        monitor = job.health_check_mode != "off"

        service = get_image_update_service()
        result = service.execute_job(
            db, job,
            monitor_rollout=monitor,
            stuck_detection=stuck_det,
            crash_tolerance=crash_tol,
            patch_batch_size=batch_size,
            patch_batch_pause=batch_pause,
        )

        if not result["success"]:
            log_event("image_update.job.worker.failed", job_id=job_id, error=result.get("error"))
            return
        
        # Clear planned_upgrade_at for successfully updated resources
        if result.get("results", {}).get("success", 0) > 0:
            successful_results = db.query(ImageUpdateResult).filter(
                ImageUpdateResult.job_id == job_id,
                ImageUpdateResult.status == "success",
            ).all()
            
            for res in successful_results:
                resource = db.query(Resource).filter(
                    Resource.namespace == res.namespace,
                    Resource.resource_name == res.resource_name,
                    Resource.kind.ilike(res.kind),
                ).first()
                
                if resource:
                    plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == resource.id).first()
                    if plan and plan.planned_upgrade_at:
                        plan.planned_upgrade_at = None
            
            db.commit()
            log_event("image_update.job.plans_cleared", job_id=job_id)
            
            # Federation: Forward plan clearing to remote backends
            target_platforms = json.loads(job.target_platforms) if job.target_platforms else None
            target_namespaces = json.loads(job.target_namespaces) if job.target_namespaces else None
            _forward_plans_to_remote_backends(
                db=db,
                product_name=job.product_name,
                target_platforms=target_platforms,
                target_namespaces=target_namespaces,
                planned_upgrade_at=None,
                job_note=None,
            )
        
        log_event("image_update.job.worker.complete", job_id=job_id, status=job.status)
    except Exception as e:
        import traceback
        log_event("image_update.job.worker.exception", job_id=job_id, error=str(e), traceback=traceback.format_exc())
        # Try to mark job as failed if we have a db connection
        if db:
            try:
                job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
                if job and job.status == "executing":
                    job.status = "failed"
                    job.finished_at = datetime.now(timezone.utc)
                    job.notes = (job.notes or "") + f"\n[Worker Error: {str(e)}]"
                    db.commit()
            except Exception:
                pass
    finally:
        if db:
            db.close()


@router.post("/jobs/{job_id}/execute")
def execute_job(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Execute an approved image update job asynchronously.
    
    Returns immediately after starting the job. Use GET /jobs/{job_id} to poll for progress.
    Health checks are always enabled. For large jobs (50+ resources), the timeout
    is automatically reduced to 60s per resource for better performance.
    """
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ["approved", "pending_approval"]:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be executed in status: {job.status}"
        )
    
    if job.status == "pending_approval" and job.approval_required:
        raise HTTPException(
            status_code=400,
            detail="Job requires approval before execution"
        )
    
    # Check if scheduled for future
    if job.scheduled_at and job.scheduled_at > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail=f"Job is scheduled for {job.scheduled_at.isoformat()}"
        )
    
    log_event("image_update.job.execute.start", job_id=job.id, triggered_by=username)
    
    # Mark job as executing immediately so frontend can start polling
    job.status = "executing"
    job.started_at = datetime.now(timezone.utc)
    job.progress_current = 0
    job.progress_total = 1
    job.progress_message = "Starting job..."
    db.commit()
    
    # Start execution in background thread
    import logging as _logging
    _logging.warning(f"[EXECUTE] Creating thread for job {job_id}")
    print(f"[EXECUTE] Creating thread for job {job_id}", flush=True)
    
    thread = threading.Thread(
        target=_execute_job_worker,
        args=(job_id, username),
        daemon=True
    )
    thread.start()
    
    _logging.warning(f"[EXECUTE] Thread started for job {job_id}, thread={thread.name}, alive={thread.is_alive()}")
    print(f"[EXECUTE] Thread started for job {job_id}, alive={thread.is_alive()}", flush=True)
    
    log_event("image_update.job.execute.async_started", job_id=job.id)
    
    return {
        "ok": True,
        "job_id": job.id,
        "status": "executing",
        "message": "Job execution started. Poll GET /jobs/{job_id} for progress.",
    }


@router.post("/jobs/{job_id}/dry-run")
def dry_run_job(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Perform a dry run of the job without making any changes.
    
    Checks:
    - Resource existence and accessibility
    - Current image state
    - Backend connectivity for remote platforms
    - Permission validation
    
    Returns a detailed report of what would happen if executed.
    """
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    log_event("image_update.job.dry_run.start", job_id=job.id, triggered_by=username)
    
    try:
        return _dry_run_job_internal(job, db)
    except Exception as e:
        import traceback
        log_event(
            "image_update.job.dry_run.exception",
            job_id=job.id,
            error=str(e),
            traceback=traceback.format_exc()[:1000],
        )
        raise HTTPException(status_code=500, detail=f"Dry run failed: {str(e)}")


def _dry_run_job_internal(job: ImageUpdateJob, db: Session) -> dict:
    """Internal dry run logic - separated for error handling."""
    service = get_image_update_service()
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
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
    
    # Identify remote platforms from resource keys
    remote_resource_keys_by_platform = {}
    
    if target_resources and isinstance(target_resources[0], str) and '|' in target_resources[0]:
        for key in target_resources:
            parts = key.split('|')
            if len(parts) == 4:
                platform, namespace, kind, resource_name = parts
                if platform != local_platform:
                    if platform not in remote_resource_keys_by_platform:
                        remote_resource_keys_by_platform[platform] = []
                    remote_resource_keys_by_platform[platform].append({
                        'platform': platform,
                        'namespace': namespace,
                        'kind': kind,
                        'resource_name': resource_name,
                    })
    
    # Identify remote platforms that need full product dry-run.
    # Covers both: no specific resources at all, and retry scenarios
    # where forwarding failed (those platforms have no resource keys).
    remote_platforms_for_product_check = []
    if target_platforms:
        for platform in target_platforms:
            if platform != local_platform and platform not in remote_resource_keys_by_platform:
                remote_platforms_for_product_check.append(platform)
        
        if remote_platforms_for_product_check:
            log_event(
                "image_update.job.dry_run.remote_platforms",
                job_id=job.id,
                platforms=remote_platforms_for_product_check,
                reason="target_platforms includes non-local platforms without specific resource keys",
            )
    
    # Get matching LOCAL resources
    resources = service.get_matching_resources(
        db,
        job.product_name,
        target_platforms,
        target_namespaces,
        target_resources,
    )
    
    # Parse source image repo + tag for pre-filtering
    from ..services.versioning import parse_image
    target_parsed = parse_image(job.target_image)
    target_repo = target_parsed.repository.lower() if target_parsed else None
    source_repo = None
    source_tag = None
    if job.source_image:
        if ':' in job.source_image.split('/')[-1]:
            raw_repo, source_tag = job.source_image.rsplit(':', 1)
        else:
            raw_repo = job.source_image
        source_parsed = parse_image(raw_repo + ":dummy")
        source_repo = source_parsed.repository.lower() if source_parsed else raw_repo.lower()
    
    # Pre-filter resources by source image:tag (same logic as execute_job)
    if source_repo:
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
                    if cr == source_repo or cr.endswith('/' + source_repo):
                        if source_tag:
                            ct = ci.split(':')[-1] if ':' in ci else ''
                            if ct != source_tag:
                                continue
                        has_match = True
                        break
            elif r.image:
                cr = r.image.split(':')[0].lower()
                if cr == source_repo:
                    if source_tag:
                        ct = r.image.split(':')[-1] if ':' in r.image else ''
                        has_match = ct == source_tag
                    else:
                        has_match = True
            if has_match:
                filtered.append(r)
        log_event("image_update.dry_run.pre_filter",
                 job_id=job.id, source_repo=source_repo, source_tag=source_tag,
                 before=len(resources), after=len(filtered))
        resources = filtered
    
    # Group by platform
    resources_by_platform = {}
    for r in resources:
        if r.platform not in resources_by_platform:
            resources_by_platform[r.platform] = []
        resources_by_platform[r.platform].append(r)
    
    # Build dry run report
    report = {
        "job_id": job.id,
        "product": job.product_name,
        "target_image": job.target_image,
        "source_image": job.source_image,
        # Phase 1: planned field edits + Helm-managed info (so the UI can warn / require ack).
        "field_edits": json.loads(job.field_edits) if job.field_edits else None,
        "helm_managed_ack": job.helm_managed_ack or False,
        "helm_managed_targets": [],
        "summary": {
            "total": 0,
            "ready": 0,
            "warning": 0,
            "error": 0,
        },
        "by_platform": {},
        "details": [],
    }
    
    # Check LOCAL resources
    for platform, platform_resources in resources_by_platform.items():
        if platform not in report["by_platform"]:
            report["by_platform"][platform] = {"ready": 0, "warning": 0, "error": 0}
        
        for r in platform_resources:
            if (getattr(r, "helm_status", None) or "") in ("ok", "drift"):
                report["helm_managed_targets"].append({
                    "platform": r.platform, "namespace": r.namespace, "kind": r.kind,
                    "resource_name": r.resource_name, "release": getattr(r, "helm_release_name", None),
                })
            check_result = _dry_run_check_resource(
                service, r, job.target_image, target_repo, source_repo, db,
                source_tag=source_tag, field_edits=report.get("field_edits"),
                product=job.product_name,
            )

            report["details"].append(check_result)
            report["summary"]["total"] += 1
            
            if check_result["status"] == "ready":
                report["summary"]["ready"] += 1
                report["by_platform"][platform]["ready"] += 1
            elif check_result["status"] == "warning":
                report["summary"]["warning"] += 1
                report["by_platform"][platform]["warning"] += 1
            else:
                report["summary"]["error"] += 1
                report["by_platform"][platform]["error"] += 1
    
    # Check REMOTE resources via federation
    for platform, remote_resources in remote_resource_keys_by_platform.items():
        if platform not in report["by_platform"]:
            report["by_platform"][platform] = {"ready": 0, "warning": 0, "error": 0}
        
        backend = service.get_backend_for_platform(db, platform)
        
        if not backend:
            # No backend found for this platform
            for res_info in remote_resources:
                check_result = {
                    "platform": platform,
                    "namespace": res_info["namespace"],
                    "resource": res_info["resource_name"],
                    "kind": res_info["kind"],
                    "status": "error",
                    "message": f"No backend found for platform: {platform}",
                    "current_image": None,
                    "containers": [],
                }
                report["details"].append(check_result)
                report["summary"]["total"] += 1
                report["summary"]["error"] += 1
                report["by_platform"][platform]["error"] += 1
        else:
            # Forward dry-run check to remote backend
            import aiohttp
            import ssl
            
            skip_tls = backend.get("skip_tls_verify", False)
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def check_remote():
                    # Create SSL context and connector inside async context
                    ssl_context = None
                    if skip_tls:
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                    
                    connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
                    results = []
                    async with aiohttp.ClientSession(connector=connector) as session:
                        for res_info in remote_resources:
                            try:
                                url = f"{backend['api_url'].rstrip('/')}/api/image-updates/dry-run-check"
                                headers = {
                                    "Authorization": f"Bearer {backend['auth_token']}",
                                    "Content-Type": "application/json",
                                }
                                payload = {
                                    "namespace": res_info["namespace"],
                                    "name": res_info["resource_name"],
                                    "kind": res_info["kind"],
                                    "target_image": job.target_image,
                                    "source_image": job.source_image,
                                    # Phase 1.4: let the remote dry-run reflect field edits too.
                                    "field_edits": json.loads(job.field_edits) if job.field_edits else None,
                                    "product": job.product_name,
                                }
                                
                                async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        data["platform"] = platform
                                        results.append(data)
                                    else:
                                        results.append({
                                            "platform": platform,
                                            "namespace": res_info["namespace"],
                                            "resource": res_info["resource_name"],
                                            "kind": res_info["kind"],
                                            "status": "error",
                                            "message": f"Backend returned status {resp.status}",
                                            "current_image": None,
                                            "containers": [],
                                        })
                            except Exception as e:
                                results.append({
                                    "platform": platform,
                                    "namespace": res_info["namespace"],
                                    "resource": res_info["resource_name"],
                                    "kind": res_info["kind"],
                                    "status": "error",
                                    "message": f"Connection error: {str(e)}",
                                    "current_image": None,
                                    "containers": [],
                                })
                    return results
                
                remote_results = loop.run_until_complete(check_remote())
                
                for check_result in remote_results:
                    report["details"].append(check_result)
                    report["summary"]["total"] += 1
                    
                    if check_result["status"] == "ready":
                        report["summary"]["ready"] += 1
                        report["by_platform"][platform]["ready"] += 1
                    elif check_result["status"] == "warning":
                        report["summary"]["warning"] += 1
                        report["by_platform"][platform]["warning"] += 1
                    else:
                        report["summary"]["error"] += 1
                        report["by_platform"][platform]["error"] += 1
            finally:
                loop.close()
    
    # Check REMOTE platforms that need full product dry-run (no specific resources selected)
    log_event(
        "image_update.job.dry_run.checking_remote_platforms",
        job_id=job.id,
        platforms_to_check=remote_platforms_for_product_check,
        count=len(remote_platforms_for_product_check),
    )
    
    for platform in remote_platforms_for_product_check:
        if platform not in report["by_platform"]:
            report["by_platform"][platform] = {"ready": 0, "warning": 0, "error": 0}
        
        backend = service.get_backend_for_platform(db, platform)
        
        log_event(
            "image_update.job.dry_run.backend_lookup",
            job_id=job.id,
            platform=platform,
            backend_found=backend is not None,
            backend_name=backend["name"] if backend else None,
        )
        
        if not backend:
            check_result = {
                "platform": platform,
                "namespace": "-",
                "resource": "-",
                "kind": "-",
                "status": "error",
                "message": f"No backend found for platform: {platform}",
                "current_image": None,
                "containers": [],
            }
            report["details"].append(check_result)
            report["summary"]["total"] += 1
            report["summary"]["error"] += 1
            report["by_platform"][platform]["error"] += 1
        else:
            # Forward full product dry-run to remote backend
            import aiohttp
            import ssl
            
            skip_tls = backend.get("skip_tls_verify", False)
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def check_remote_product():
                    # Create SSL context and connector inside async context
                    ssl_context = None
                    if skip_tls:
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                    
                    connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
                    async with aiohttp.ClientSession(connector=connector) as session:
                        try:
                            url = f"{backend['api_url'].rstrip('/')}/api/image-updates/dry-run-product"
                            headers = {
                                "Authorization": f"Bearer {backend['auth_token']}",
                                "Content-Type": "application/json",
                            }
                            payload = {
                                "product_name": job.product_name,
                                "target_image": job.target_image,
                                "source_image": job.source_image,
                                "target_namespaces": target_namespaces,
                            }
                            
                            log_event(
                                "image_update.job.dry_run.forward_to_backend",
                                job_id=job.id,
                                platform=platform,
                                backend=backend["name"],
                                product=job.product_name,
                            )
                            
                            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    return data.get("results", [])
                                else:
                                    error_text = await resp.text()
                                    log_event(
                                        "image_update.job.dry_run.forward_failed",
                                        job_id=job.id,
                                        platform=platform,
                                        status=resp.status,
                                        error=error_text[:200],
                                    )
                                    return [{
                                        "platform": platform,
                                        "namespace": "-",
                                        "resource": "-",
                                        "kind": "-",
                                        "status": "error",
                                        "message": f"Backend returned status {resp.status}: {error_text[:100]}",
                                        "current_image": None,
                                        "containers": [],
                                    }]
                        except Exception as e:
                            log_event(
                                "image_update.job.dry_run.forward_error",
                                job_id=job.id,
                                platform=platform,
                                error=str(e),
                            )
                            return [{
                                "platform": platform,
                                "namespace": "-",
                                "resource": "-",
                                "kind": "-",
                                "status": "error",
                                "message": f"Connection error: {str(e)}",
                                "current_image": None,
                                "containers": [],
                            }]
                
                remote_results = loop.run_until_complete(check_remote_product())
                
                for check_result in remote_results:
                    # Ensure platform is set
                    check_result["platform"] = platform
                    report["details"].append(check_result)
                    report["summary"]["total"] += 1
                    
                    if check_result["status"] == "ready":
                        report["summary"]["ready"] += 1
                        report["by_platform"][platform]["ready"] += 1
                    elif check_result["status"] == "warning":
                        report["summary"]["warning"] += 1
                        report["by_platform"][platform]["warning"] += 1
                    else:
                        report["summary"]["error"] += 1
                        report["by_platform"][platform]["error"] += 1
            finally:
                loop.close()
    
    log_event(
        "image_update.job.dry_run.complete",
        job_id=job.id,
        total=report["summary"]["total"],
        ready=report["summary"]["ready"],
        warning=report["summary"]["warning"],
        error=report["summary"]["error"],
        platforms_checked=list(report["by_platform"].keys()),
    )
    
    return {
        "ok": True,
        "report": report,
    }


def _dry_run_check_resource(
    service,
    resource,
    target_image: str,
    target_repo: str,
    source_repo: str,
    db: Session,
    source_tag: str = None,
    field_edits: list = None,
    product: str = None,
) -> dict:
    """Check a single resource for dry run - no changes made."""
    result = {
        "platform": resource.platform,
        "namespace": resource.namespace,
        "resource": resource.resource_name,
        "kind": resource.kind,
        "status": "ready",
        "message": "Ready for update",
        "current_image": None,
        "containers": [],
        "warnings": [],
    }
    
    log_event(
        "image_update.dry_run_check.start",
        resource=resource.resource_name,
        namespace=resource.namespace,
        kind=resource.kind,
        target_repo=target_repo,
        source_repo=source_repo,
    )
    
    try:
        # Get current containers from security_info
        containers = []
        if resource.security_info and resource.security_info.get("containers"):
            containers = resource.security_info["containers"]
        
        if not containers:
            result["status"] = "warning"
            result["message"] = "No containers found"
            result["warnings"].append("Resource has no containers defined")
            return result
        
        # Check replicas
        if resource.replicas == 0:
            result["warnings"].append("Resource has 0 replicas (scaled down)")
            result["status"] = "warning"
        
        # Find matching containers
        from ..services.versioning import parse_image
        matching_containers = []
        
        def _repo_matches(cr, sr):
            if not cr or not sr:
                return False
            if cr == sr:
                return True
            return cr.endswith('/' + sr)
        
        for c in containers:
            c_image = c.get("image", "")
            c_parsed = parse_image(c_image)
            c_repo = c_parsed.repository.lower() if c_parsed else None
            
            # Check if this container matches the source image
            matches = False
            if source_repo:
                if c_repo and _repo_matches(c_repo, source_repo):
                    if source_tag:
                        c_tag = c_parsed.tag if c_parsed and c_parsed.tag else ""
                        if not c_tag and ':' in c_image:
                            c_tag = c_image.split(':')[-1]
                        matches = (c_tag == source_tag)
                    else:
                        matches = True
            elif target_repo:
                if c_repo and _repo_matches(c_repo, target_repo):
                    matches = True
            elif field_edits and product:
                # Field-edit job with no image filter: scope to the product-matched
                # container(s) — the edits apply there, not to every container in the pod.
                from ..services.image_update import _image_matches_product
                matches = bool(c_image) and _image_matches_product(c_image, product)
            else:
                matches = True  # No filter, all containers match
            
            if matches:
                container_info = {
                    "name": c.get("name", "unknown"),
                    "current_image": c_image,
                    "target_image": target_image,
                    "will_update": c_image != target_image,
                }
                matching_containers.append(container_info)
                
                if not result["current_image"]:
                    result["current_image"] = c_image
        
        # Field-edit jobs edit the matched container(s) even when the image is unchanged, so
        # "will_update" reflects the edit, not just an image diff (fields-only had no image).
        has_field_edits = bool(field_edits)
        is_image_change = bool(target_image)
        for c in matching_containers:
            c["will_update"] = (c["current_image"] != target_image) if is_image_change else has_field_edits

        result["containers"] = matching_containers

        if not matching_containers:
            result["status"] = "warning"
            result["message"] = "No matching containers found for update"
            result["warnings"].append("No containers match the source image filter")
        elif is_image_change and all(c["current_image"] == target_image for c in matching_containers):
            if has_field_edits:
                result["message"] = f"Image already at target; {len(field_edits)} manifest edit(s) will apply"
            else:
                result["status"] = "warning"
                result["message"] = "Already at target version"
                result["warnings"].append("All containers already have the target image")
        elif has_field_edits and not is_image_change:
            names = ", ".join(sorted({c.get("name", "?") for c in matching_containers}))
            result["message"] = f"{len(field_edits)} manifest edit(s) will apply to container {names}"
        
        # Verify resource exists in Kubernetes (read-only check)
        # This is important: if we can't read the resource, we can't patch it either
        try:
            from kubernetes import client, config
            
            # Ensure K8s config is loaded
            try:
                config.load_incluster_config()
            except Exception:
                try:
                    config.load_kube_config()
                except Exception:
                    pass  # Config may already be loaded
            
            if resource.kind.lower() == "deployment":
                apps_v1 = client.AppsV1Api()
                k8s_resource = apps_v1.read_namespaced_deployment(
                    name=resource.resource_name,
                    namespace=resource.namespace,
                )
            elif resource.kind.lower() == "statefulset":
                apps_v1 = client.AppsV1Api()
                k8s_resource = apps_v1.read_namespaced_stateful_set(
                    name=resource.resource_name,
                    namespace=resource.namespace,
                )
            elif resource.kind.lower() == "daemonset":
                apps_v1 = client.AppsV1Api()
                k8s_resource = apps_v1.read_namespaced_daemon_set(
                    name=resource.resource_name,
                    namespace=resource.namespace,
                )
            else:
                result["warnings"].append(f"Unknown resource kind: {resource.kind}")
        except Exception as k8s_err:
            # K8s check failed - this is an error because execute will also fail
            result["status"] = "error"
            result["message"] = f"Resource not accessible in K8s: {str(k8s_err)[:150]}"
            log_event(
                "image_update.dry_run_check.k8s_error",
                resource=resource.resource_name,
                namespace=resource.namespace,
                error=str(k8s_err)[:200],
            )
        
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"Check failed: {str(e)}"
    
    # Set final message based on status
    if result["status"] == "ready" and result["warnings"]:
        result["status"] = "warning"
        result["message"] = "; ".join(result["warnings"])
    
    log_event(
        "image_update.dry_run_check.complete",
        resource=resource.resource_name,
        status=result["status"],
        message=result["message"][:100] if result["message"] else None,
        containers_count=len(result["containers"]),
        matching_count=len([c for c in result["containers"] if c.get("will_update")]),
    )
    
    return result


class DryRunCheckRequest(BaseModel):
    namespace: str
    name: str
    kind: str
    target_image: Optional[str] = None
    source_image: Optional[str] = None
    field_edits: Optional[list] = None  # Phase 1.4: field-edit dry-run forwarded from primary
    product: Optional[str] = None  # scope field-edit container matching to the product


@router.post("/dry-run-check")
def dry_run_check_single(
    body: DryRunCheckRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Check a single resource for dry run - used by federation.
    
    This endpoint is called by the primary backend to verify resources
    on remote backends without making changes.
    """
    # Verify token - can be either user token or backend auth token
    try:
        _decode_token(token)
    except Exception:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")
    
    service = get_image_update_service()
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    # Find the resource in local database
    resource = db.query(Resource).filter(
        Resource.platform == local_platform,
        Resource.namespace == body.namespace,
        Resource.resource_name == body.name,
        Resource.kind.ilike(body.kind),
    ).first()
    
    if not resource:
        return {
            "namespace": body.namespace,
            "resource": body.name,
            "kind": body.kind,
            "status": "error",
            "message": "Resource not found in database",
            "current_image": None,
            "containers": [],
        }
    
    # Parse target/source for repo matching
    from ..services.versioning import parse_image
    target_parsed = parse_image(body.target_image) if body.target_image else None
    target_repo = target_parsed.repository.lower() if target_parsed else None
    source_repo = None
    source_tag = None
    if body.source_image:
        if ':' in body.source_image.split('/')[-1]:
            raw_repo, source_tag = body.source_image.rsplit(':', 1)
        else:
            raw_repo = body.source_image
        source_parsed = parse_image(raw_repo + ":dummy")
        source_repo = source_parsed.repository.lower() if source_parsed else raw_repo.lower()

    result = _dry_run_check_resource(
        service, resource, body.target_image or "", target_repo, source_repo, db,
        source_tag=source_tag, field_edits=body.field_edits, product=body.product,
    )
    
    return result


class DryRunProductRequest(BaseModel):
    product_name: str
    target_image: str
    source_image: Optional[str] = None
    target_namespaces: Optional[List[str]] = None


@router.post("/dry-run-product")
def dry_run_product(
    body: DryRunProductRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Check all resources for a product for dry run - used by federation.
    
    This endpoint is called by the primary backend to verify all resources
    for a specific product on remote backends without making changes.
    """
    # Verify token - can be either user token or backend auth token
    try:
        _decode_token(token)
    except Exception:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")
    
    service = get_image_update_service()
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    log_event(
        "image_update.dry_run_product.start",
        product=body.product_name,
        platform=local_platform,
        target_namespaces=body.target_namespaces,
    )
    
    # Get matching resources for this product on this backend
    resources = service.get_matching_resources(
        db,
        body.product_name,
        target_platforms=[local_platform],  # Only local resources
        target_namespaces=body.target_namespaces,
        target_resources=None,
    )
    
    log_event(
        "image_update.dry_run_product.resources_found",
        product=body.product_name,
        platform=local_platform,
        count=len(resources),
    )
    
    # Parse target/source for repo matching
    from ..services.versioning import parse_image
    target_parsed = parse_image(body.target_image)
    target_repo = target_parsed.repository.lower() if target_parsed else None
    source_repo = None
    source_tag = None
    if body.source_image:
        if ':' in body.source_image.split('/')[-1]:
            raw_repo, source_tag = body.source_image.rsplit(':', 1)
        else:
            raw_repo = body.source_image
        source_parsed = parse_image(raw_repo + ":dummy")
        source_repo = source_parsed.repository.lower() if source_parsed else raw_repo.lower()
    
    # Pre-filter resources by source image repo + tag (same logic as dry_run_for_job)
    if source_repo:
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
                    if cr == source_repo or cr.endswith('/' + source_repo):
                        if source_tag:
                            ct = ci.split(':')[-1] if ':' in ci else ''
                            if ct != source_tag:
                                continue
                        has_match = True
                        break
            elif r.image:
                cr = r.image.split(':')[0].lower()
                if cr == source_repo:
                    if source_tag:
                        ct = r.image.split(':')[-1] if ':' in r.image else ''
                        has_match = ct == source_tag
                    else:
                        has_match = True
            if has_match:
                filtered.append(r)
        log_event("image_update.dry_run_product.pre_filter",
                 product=body.product_name, source_repo=source_repo, source_tag=source_tag,
                 before=len(resources), after=len(filtered))
        resources = filtered
    
    results = []
    for resource in resources:
        check_result = _dry_run_check_resource(
            service, resource, body.target_image, target_repo, source_repo, db,
            source_tag=source_tag,
        )
        check_result["platform"] = local_platform
        results.append(check_result)
    
    log_event(
        "image_update.dry_run_product.complete",
        product=body.product_name,
        platform=local_platform,
        results_count=len(results),
    )
    
    return {"ok": True, "results": results}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Cancel a pending job."""
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be cancelled in status: {job.status}"
        )
    
    if job.status == "executing":
        from datetime import timedelta
        from ..models import ConfigKV
        idle_minutes_row = db.query(ConfigKV).filter(ConfigKV.key == "stuck_job_timeout_minutes").first()
        idle_minutes = int(idle_minutes_row.value) if idle_minutes_row else 60
        last_activity = job.progress_updated_at or job.started_at
        idle_exceeded = last_activity and last_activity < datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
        
        # Force-cancel if: idle timeout exceeded OR cancellation was already requested (thread may be dead)
        if idle_exceeded or job.cancel_requested:
            reason = f"no progress for >{idle_minutes}min" if idle_exceeded else "cancel_requested flag was set (thread likely dead)"
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.notes = (job.notes or "") + f"\n[Force cancelled by {username}: {reason}]"
            db.commit()
            log_event("image_update.job.force_cancelled", job_id=job.id, cancelled_by=username, reason=reason)
            return {"ok": True, "status": "failed", "message": f"Job force-cancelled ({reason})"}
        
        # First attempt: set cancel_requested flag so running thread can stop gracefully
        job.cancel_requested = True
        db.commit()
        log_event("image_update.job.cancel_requested_via_cancel", job_id=job.id, cancelled_by=username)
        return {
            "ok": True,
            "status": "cancel_requested",
            "message": "Cancellation requested. Click cancel again to force-cancel if the job doesn't stop.",
        }
    
    job.status = "cancelled"
    job.cancelled_at = datetime.now(timezone.utc)
    job.notes = (job.notes or "") + f"\n[Cancelled by {username}]"
    
    # Clear planned_upgrade_at for affected resources if this was a scheduled job
    if job.scheduled_at:
        service = get_image_update_service()
        target_platforms = json.loads(job.target_platforms) if job.target_platforms else None
        target_namespaces = json.loads(job.target_namespaces) if job.target_namespaces else None
        target_resources = json.loads(job.target_resources) if job.target_resources else None
        
        affected_resources = service.get_matching_resources(
            db,
            job.product_name,
            target_platforms,
            target_namespaces,
            target_resources,
        )
        
        for resource in affected_resources:
            plan = db.query(ResourcePlan).filter(ResourcePlan.resource_id == resource.id).first()
            if plan and plan.planned_upgrade_at:
                plan.planned_upgrade_at = None
        
        # Federation: Forward plan clearing to remote backends
        _forward_plans_to_remote_backends(
            db=db,
            product_name=job.product_name,
            target_platforms=target_platforms,
            target_namespaces=target_namespaces,
            planned_upgrade_at=None,  # Clear the plan
            job_note=None,
        )
    
    db.commit()
    
    log_event("image_update.job.cancelled", job_id=job.id, cancelled_by=username)
    
    return {"ok": True, "status": "cancelled"}


@router.post("/jobs/{job_id}/request-cancel")
def request_job_cancel(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Request cancellation of an executing job.
    
    Sets the cancel_requested flag which the job executor checks periodically.
    The job will stop at the next safe point.
    """
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != "executing":
        raise HTTPException(
            status_code=400,
            detail=f"Can only request cancellation for executing jobs (current status: {job.status})"
        )
    
    job.cancel_requested = True
    db.commit()
    
    log_event("image_update.job.cancel_requested", job_id=job.id, requested_by=username)
    
    return {
        "ok": True, 
        "message": "Cancellation requested. Job will stop at the next safe point.",
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
    }


@router.get("/jobs/{job_id}/progress")
def get_job_progress(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Get live progress of a job."""
    _decode_token(token)
    
    # Force fresh read from database for live progress updates
    db.expire_all()
    db.commit()  # Ensure we start with a clean transaction
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "id": job.id,
        "status": job.status,
        "progress_current": job.progress_current or 0,
        "progress_total": job.progress_total or 0,
        "progress_message": job.progress_message or "",
        "cancel_requested": job.cancel_requested,
        "started_at": job.started_at.isoformat() if job.started_at else None,
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Retry a failed or completed job - only retries failed resources.
    
    Works with federation: tracks which platforms had failures and only
    retries on those platforms.
    """
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ["failed", "completed", "completed_with_rollbacks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Only failed or completed jobs can be retried. Current status: {job.status}"
        )
    
    # Get failed results to identify resources that need retry
    failed_results = db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id,
        ImageUpdateResult.status.in_(["failed", "rolled_back"])
    ).all()
    
    if not failed_results:
        raise HTTPException(
            status_code=400,
            detail="No failed resources to retry. All resources were successful."
        )
    
    # Extract unique failed resources grouped by platform (for federation support)
    # Use full resource key format: "platform|namespace|kind|resource_name"
    failed_resource_keys = set()  # Full keys for execute logic
    failed_namespaces = set()
    failed_platforms = set()
    forwarding_failed_platforms = set()  # Platforms where entire forwarding failed
    failed_by_platform = {}  # platform -> list of resource details
    
    for r in failed_results:
        failed_platforms.add(r.platform)
        
        if r.platform not in failed_by_platform:
            failed_by_platform[r.platform] = []
        failed_by_platform[r.platform].append({
            "namespace": r.namespace,
            "resource": r.resource_name,
            "kind": r.kind,
            "error": r.error_message,
        })
        
        if r.resource_name == "(product forwarding failed)":
            # Platform-level forwarding failure (timeout, connection error, etc.)
            # Don't create fake resource keys — retry via full platform forwarding
            forwarding_failed_platforms.add(r.platform)
            continue
        
        resource_key = f"{r.platform}|{r.namespace}|{r.kind}|{r.resource_name}"
        failed_resource_keys.add(resource_key)
        failed_namespaces.add(r.namespace)
    
    # Update job to target only failed resources on their specific platforms
    job.target_resources = json.dumps(list(failed_resource_keys)) if failed_resource_keys else None
    job.target_namespaces = json.dumps(list(failed_namespaces)) if failed_namespaces else None
    job.target_platforms = json.dumps(list(failed_platforms))
    
    # Delete only failed results (keep successful ones)
    db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id,
        ImageUpdateResult.status.in_(["failed", "rolled_back"])
    ).delete(synchronize_session=False)
    
    # Reset job status
    job.status = "approved"
    job.started_at = None
    job.finished_at = None
    total_retry_count = len(failed_resource_keys) + len(forwarding_failed_platforms)
    parts = []
    if failed_resource_keys:
        parts.append(f"{len(failed_resource_keys)} failed resource(s)")
    if forwarding_failed_platforms:
        parts.append(f"{len(forwarding_failed_platforms)} platform forwarding retry(s)")
    retry_desc = " + ".join(parts) if parts else "failed items"
    
    job.notes = (job.notes or "") + f"\n[Retry for {retry_desc} on {len(failed_platforms)} platform(s) requested by {username}]"
    db.commit()
    
    log_event(
        "image_update.job.retry",
        job_id=job.id,
        retried_by=username,
        failed_resource_keys=list(failed_resource_keys),
        forwarding_failed_platforms=list(forwarding_failed_platforms),
        failed_platforms=list(failed_platforms),
        failed_count=total_retry_count,
        by_platform=failed_by_platform,
    )
    
    return {
        "ok": True,
        "status": "approved",
        "message": f"Job reset to retry {retry_desc} on {len(failed_platforms)} platform(s). Execute when ready.",
        "retry_count": total_retry_count,
        "platforms": list(failed_platforms),
    }


def _rollback_job_worker(job_id: int, username: str):
    """Background worker to rollback a job. Uses its own DB session."""
    import logging
    import sys
    
    print(f"[ROLLBACK] Thread started for job {job_id}", flush=True)
    sys.stdout.flush()
    logging.warning(f"[ROLLBACK] Thread started for job {job_id} by {username}")
    log_event("image_update.job.rollback.thread_started", job_id=job_id, username=username)
    
    db = None
    try:
        db = SessionLocal()
        print(f"[ROLLBACK] DB connected for job {job_id}", flush=True)
        log_event("image_update.job.rollback.db_connected", job_id=job_id)
        
        job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
        if not job:
            log_event("image_update.job.rollback.not_found", job_id=job_id)
            return
        
        # Get successful results to rollback
        successful_results = db.query(ImageUpdateResult).filter(
            ImageUpdateResult.job_id == job_id,
            ImageUpdateResult.status == "success",
            ImageUpdateResult.old_image.isnot(None),
        ).all()
        
        total_count = len(successful_results)
        log_event("image_update.job.rollback.start", job_id=job_id, triggered_by=username, count=total_count)
        
        service = get_image_update_service()
        local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")

        # Wait for the rollback's own rollout (no auto-rollback) so we don't report done early.
        from ..models import ConfigKV as _ConfigKV
        _stuck_row = db.query(_ConfigKV).filter(_ConfigKV.key == "stuck_detection_seconds").first()
        rollback_verify_timeout = int(_stuck_row.value) if _stuck_row else 300
        monitor_rollback = job.health_check_mode != "off"

        rollback_results = {
            "total": total_count,
            "success": 0,
            "failed": 0,
            "details": [],
            "by_platform": {},
        }
        
        # Group results by platform for federation
        results_by_platform = {}
        for result in successful_results:
            if result.platform not in results_by_platform:
                results_by_platform[result.platform] = []
            results_by_platform[result.platform].append(result)
        
        current_index = 0
        for platform, platform_results in results_by_platform.items():
            is_local = (platform == local_platform)
            backend = None if is_local else service.get_backend_for_platform(db, platform)
            platform_name = "local" if is_local else platform
            
            rollback_results["by_platform"][platform_name] = {"success": 0, "failed": 0}
            
            for result in platform_results:
                current_index += 1
                success = False
                error = None
                
                # Update progress
                job.progress_current = current_index
                job.progress_message = f"Rolling back {result.resource_name} ({current_index}/{total_count})"
                db.commit()

                # Baseline for the rollback rollout verification (captured pre-patch).
                pre_rollback_state = None
                if is_local and monitor_rollback:
                    pre_rollback_state = service.get_resource_state(result.namespace, result.resource_name, result.kind)

                if is_local:
                    # Local rollback
                    success, _, error = service.patch_resource_image(
                        namespace=result.namespace,
                        name=result.resource_name,
                        kind=result.kind,
                        new_image=result.old_image,
                        container_name=result.container_name,
                    )
                elif backend:
                    # Forward rollback to remote backend
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        success, _, error, _ = loop.run_until_complete(
                            service.forward_patch_to_backend(
                                backend=backend,
                                namespace=result.namespace,
                                name=result.resource_name,
                                kind=result.kind,
                                new_image=result.old_image,
                                container_name=result.container_name,
                            )
                        )
                    finally:
                        loop.close()
                else:
                    error = f"No backend found for platform: {platform}"
                
                # Update result status to show it was rolled back
                result.was_rolled_back = True
                result.rollback_reason = f"Manual rollback by {username}"
                db.commit()
                
                if success:
                    # Wait for the rollback's OWN rollout before declaring success, and record
                    # the rollback's health (overwrites the original patch's stale health). The
                    # job stays "rolling_back" until this returns; no auto-rollback on failure.
                    if is_local and monitor_rollback:
                        rb_ok, rb_msg = service.verify_rollout(
                            result.namespace, result.resource_name, result.kind,
                            pre_rollback_state, timeout=rollback_verify_timeout,
                        )
                        result.health_check_passed = rb_ok
                        result.health_check_message = rb_msg
                        result.health_checked_at = datetime.now(timezone.utc)
                        db.commit()
                    rollback_results["success"] += 1
                    rollback_results["by_platform"][platform_name]["success"] += 1

                    # Update resource table for local resources
                    if is_local:
                        resource = db.query(Resource).filter(
                            Resource.platform == result.platform,
                            Resource.namespace == result.namespace,
                            Resource.resource_name == result.resource_name,
                            Resource.kind.ilike(result.kind),
                        ).first()
                        
                        if resource:
                            service._update_resource_after_patch(
                                db=db,
                                resource=resource,
                                container_name=result.container_name,
                                new_image=result.old_image,
                                job_id=job.id,
                            )
                else:
                    rollback_results["failed"] += 1
                    rollback_results["by_platform"][platform_name]["failed"] += 1
                
                rollback_results["details"].append({
                    "platform": platform,
                    "namespace": result.namespace,
                    "resource": result.resource_name,
                    "kind": result.kind,
                    "container": result.container_name,
                    "reverted_to": result.old_image,
                    "status": "success" if success else "failed",
                    "error": error,
                })
        
        # Update job status
        job.status = "rolled_back"
        job.progress_current = total_count
        job.progress_message = f"Rollback complete: {rollback_results['success']}/{total_count} reverted"
        job.notes = (job.notes or "") + f"\n[Rolled back by {username}] - {rollback_results['success']}/{total_count} reverted across {len(results_by_platform)} platform(s)"
        
        # Update results_summary
        job.results_summary = json.dumps({
            "success": rollback_results["success"],
            "failed": rollback_results["failed"],
            "total": total_count,
            "rolled_back": total_count,
        })
        db.commit()
        
        log_event(
            "image_update.job.rollback.complete",
            job_id=job_id,
            total=total_count,
            success=rollback_results["success"],
            failed=rollback_results["failed"],
            platforms=list(results_by_platform.keys()),
        )
        
    except Exception as e:
        log_event("image_update.job.rollback.error", job_id=job_id, error=str(e))
        import traceback
        traceback.print_exc()
        if db:
            try:
                job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
                if job:
                    job.status = "failed"
                    job.progress_message = f"Rollback error: {str(e)}"
                    db.commit()
            except:
                pass
    finally:
        if db:
            db.close()


@router.post("/jobs/{job_id}/rollback")
def rollback_job(
    job_id: int,
    dry_run: bool = False,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Rollback a completed job - revert all successful patches to their original images.
    
    With dry_run=true, returns the list of resources that would be rolled back without
    executing anything. Use this for confirmation before actual rollback.
    
    Returns immediately after starting the rollback. Use GET /jobs/{job_id} to poll for progress.
    Works with federation: forwards rollback to remote backends for non-local resources.
    """
    username = _require_admin(token)
    
    job = db.query(ImageUpdateJob).filter(ImageUpdateJob.id == job_id).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in ["completed", "failed", "completed_with_rollbacks"]:
        raise HTTPException(
            status_code=400,
            detail=f"Only completed or failed jobs can be rolled back. Current status: {job.status}"
        )
    
    # Get successful results that can be rolled back
    rollback_candidates = db.query(ImageUpdateResult).filter(
        ImageUpdateResult.job_id == job_id,
        ImageUpdateResult.status == "success",
        ImageUpdateResult.old_image.isnot(None),
    ).all()
    
    if not rollback_candidates:
        raise HTTPException(
            status_code=400,
            detail="No successful updates to rollback"
        )
    
    if dry_run:
        items = []
        for r in rollback_candidates:
            items.append({
                "platform": r.platform,
                "namespace": r.namespace,
                "resource_name": r.resource_name,
                "kind": r.kind,
                "container_name": r.container_name,
                "current_image": r.new_image,
                "rollback_to": r.old_image,
            })
        return {
            "ok": True,
            "dry_run": True,
            "job_id": job.id,
            "product_name": job.product_name,
            "total_to_rollback": len(items),
            "items": items,
        }
    
    successful_count = len(rollback_candidates)
    log_event("image_update.job.rollback.requested", job_id=job.id, triggered_by=username, count=successful_count)
    
    # Mark job as rolling_back immediately so frontend can start polling
    job.status = "rolling_back"
    job.progress_current = 0
    job.progress_total = successful_count
    job.progress_message = "Starting rollback..."
    db.commit()
    
    # Start rollback in background thread
    import logging as _logging
    _logging.warning(f"[ROLLBACK] Creating thread for job {job_id}")
    print(f"[ROLLBACK] Creating thread for job {job_id}", flush=True)
    
    thread = threading.Thread(
        target=_rollback_job_worker,
        args=(job_id, username),
        daemon=True
    )
    thread.start()
    
    _logging.warning(f"[ROLLBACK] Thread started for job {job_id}, thread={thread.name}, alive={thread.is_alive()}")
    print(f"[ROLLBACK] Thread started for job {job_id}, alive={thread.is_alive()}", flush=True)
    
    log_event("image_update.job.rollback.async_started", job_id=job_id)
    
    return {
        "ok": True,
        "job_id": job.id,
        "status": "rolling_back",
        "message": "Rollback started. Poll GET /jobs/{job_id} for progress.",
        "total_to_rollback": successful_count,
    }


# ==================== Azure DevOps Pipeline Trigger Endpoint ====================

@router.post("/trigger/{trigger_token}", response_model=ImageUpdateTriggerResponse)
def pipeline_trigger(
    trigger_token: str,
    db: Session = Depends(get_db)
) -> ImageUpdateTriggerResponse:
    """
    Trigger endpoint for Azure DevOps pipeline integration.
    
    This endpoint is called by the pipeline after CD approval.
    No authentication required - the trigger_token serves as authentication.
    """
    job = db.query(ImageUpdateJob).filter(
        ImageUpdateJob.trigger_token == trigger_token
    ).one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Invalid trigger token")
    
    if job.status not in ["approved", "pending_approval"]:
        return ImageUpdateTriggerResponse(
            job_id=job.id,
            status=job.status,
            message=f"Job already in status: {job.status}",
            results=None,
        )
    
    # If pending approval but this is a pipeline trigger, auto-approve
    if job.status == "pending_approval":
        job.status = "approved"
        job.approved_by = "pipeline"
        job.approved_at = datetime.now(timezone.utc)
        job.notes = (job.notes or "") + "\n[Auto-approved via pipeline trigger]"
        db.commit()
    
    log_event("image_update.pipeline.trigger", job_id=job.id)
    
    from ..models import ConfigKV
    stuck_row = db.query(ConfigKV).filter(ConfigKV.key == "stuck_detection_seconds").first()
    crash_row = db.query(ConfigKV).filter(ConfigKV.key == "crash_tolerance_seconds").first()
    bs_row = db.query(ConfigKV).filter(ConfigKV.key == "patch_batch_size").first()
    bp_row = db.query(ConfigKV).filter(ConfigKV.key == "patch_batch_pause_seconds").first()
    stuck_det = int(stuck_row.value) if stuck_row else 300
    crash_tol = int(crash_row.value) if crash_row else 120
    batch_size = int(bs_row.value) if bs_row else 10
    batch_pause = int(bp_row.value) if bp_row else 2

    monitor = job.health_check_mode != "off"

    service = get_image_update_service()
    result = service.execute_job(
        db, job,
        monitor_rollout=monitor,
        stuck_detection=stuck_det,
        crash_tolerance=crash_tol,
        patch_batch_size=batch_size,
        patch_batch_pause=batch_pause,
    )
    
    if not result["success"]:
        return ImageUpdateTriggerResponse(
            job_id=job.id,
            status="failed",
            message=result.get("error", "Execution failed"),
            results=None,
        )
    
    # Format results for pipeline response
    formatted_results = []
    for detail in result.get("results", {}).get("details", []):
        formatted_results.append({
            "platform": detail.get("platform"),
            "namespace": detail.get("namespace"),
            "resource": detail.get("resource"),
            "kind": detail.get("kind"),
            "container": detail.get("container"),
            "old_image": detail.get("old_image"),
            "new_image": detail.get("new_image"),
            "status": detail.get("status"),
            "error": detail.get("error"),
        })
    
    return ImageUpdateTriggerResponse(
        job_id=job.id,
        status=job.status,
        message=f"Completed: {result['results']['success']} success, {result['results']['failed']} failed, {result['results']['skipped']} skipped",
        results=formatted_results,
    )


# ==================== Remote Patch Endpoint (for multi-backend) ====================

class PatchResourceRequest(BaseModel):
    namespace: str
    name: str
    kind: str
    new_image: Optional[str] = None  # optional for fields-only field-edit forwards
    container_name: Optional[str] = None
    source_image: Optional[str] = None  # For filtering containers by repository
    job_id: Optional[int] = None
    # Phase 1.4: structured manifest edits forwarded from the primary; when present the owning
    # backend applies them via apply_resource_changes and stores its own pre-patch snapshot.
    field_edits: Optional[list] = None
    helm_managed_ack: bool = False


class RestoreResourceRequest(BaseModel):
    job_id: int
    namespace: str
    name: str
    kind: str


@router.post("/restore-resource")
def restore_single_resource(
    body: RestoreResourceRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Restore a resource to the pre-patch snapshot THIS backend captured for a field-edit
    job (Phase 1.4 federation rollback). Called by the primary; the snapshot never left this
    backend. Returns {success, error}."""
    try:
        _decode_token(token)
    except Exception:
        be = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token, BackendEndpoint.approved == True,
        ).first()
        if not be:
            raise HTTPException(status_code=401, detail="Invalid token")
    snap = db.query(ImageUpdateSnapshot).filter(
        ImageUpdateSnapshot.job_id == body.job_id,
        ImageUpdateSnapshot.namespace == body.namespace,
        ImageUpdateSnapshot.resource_name == body.name,
        ImageUpdateSnapshot.kind == body.kind,
    ).order_by(ImageUpdateSnapshot.id.desc()).first()
    if not snap or not snap.pre_patch_manifest:
        return {"success": False, "error": "No snapshot found for this job/resource"}
    from ..services.crypto_utils import decrypt_value
    try:
        manifest = json.loads(decrypt_value(snap.pre_patch_manifest))
    except Exception as e:
        return {"success": False, "error": f"snapshot decode error: {e}"}
    service = get_image_update_service()
    ok, err = service.restore_workload(body.namespace, body.name, body.kind, manifest)
    log_event("restore_resource.done", resource=body.name, namespace=body.namespace,
              job_id=body.job_id, success=ok, error=err)
    return {"success": ok, "error": err}


@router.post("/patch-resource")
def patch_single_resource(
    body: PatchResourceRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Patch a single resource with a new image.
    
    This endpoint is called by the primary backend to execute patches
    on resources managed by this (secondary) backend.
    Also updates the local resource table after a successful patch.
    
    If source_image is provided and container_name is not, only containers
    whose current image matches the source_image repository will be patched.
    """
    # Verify token - can be either user token or backend auth token
    try:
        _decode_token(token)
        is_user = True
    except Exception:
        # Check if it's a backend auth token
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")
        is_user = False
    
    service = get_image_update_service()

    # Phase 1.4: field-edit forward — apply the manifest edits on THIS (owning) backend and
    # store our OWN pre-patch snapshot locally for restore-based rollback (never sent to the
    # primary). Returns early; the image-only path below is unchanged.
    if body.field_edits:
        import os as _os
        _plat = _os.getenv("BACKEND_PLATFORM", "default-platform")
        result = service.apply_resource_changes(
            namespace=body.namespace, name=body.name, kind=body.kind,
            new_image=(body.new_image or None), field_edits=body.field_edits,
            container_name=body.container_name, allow_helm_managed=body.helm_managed_ack,
        )
        if result["success"]:
            if result.get("pre_patch_manifest") and body.job_id is not None:
                try:
                    db.add(ImageUpdateSnapshot(
                        job_id=body.job_id, platform=_plat, namespace=body.namespace,
                        resource_name=body.name, kind=body.kind, container_name=body.container_name,
                        pre_patch_manifest=result["pre_patch_manifest"],
                    ))
                    db.commit()
                except Exception as e:
                    log_event("patch_resource.snapshot_store_error", resource=body.name, error=str(e))
            if body.new_image:  # reflect the image change on the local resource row
                try:
                    res_row = db.query(Resource).filter(
                        Resource.platform == _plat, Resource.namespace == body.namespace,
                        Resource.resource_name == body.name, Resource.kind.ilike(body.kind),
                    ).first()
                    if res_row:
                        service._update_resource_after_patch(db, res_row, body.container_name, body.new_image, job_id=body.job_id)
                except Exception:
                    pass
            log_event("patch_resource.field_edits.applied", resource=body.name, namespace=body.namespace,
                      edits=len(body.field_edits), helm_managed=result["helm_managed"])
            return {"success": True, "old_image": result["old_image"], "container": body.container_name,
                    "field_changes": result["field_changes"], "helm_managed": result["helm_managed"]}
        log_event("patch_resource.field_edits.failed", resource=body.name, namespace=body.namespace,
                  error=result["error"])
        return {"success": False, "error": result["error"], "old_image": result.get("old_image"),
                "helm_managed": result.get("helm_managed", False)}

    # If source_image is provided and no specific container, filter by repository
    container_to_patch = body.container_name
    
    if not container_to_patch and body.source_image:
        # Parse source image to get repository for filtering
        from ..services.versioning import parse_image
        source_parsed = parse_image(body.source_image)
        source_repo = None
        if source_parsed:
            # ParsedImage has .registry and .repository attributes
            source_repo = f"{source_parsed.registry}/{source_parsed.repository}".lower()
            if source_repo.startswith("docker.io/"):
                source_repo = source_repo.replace("docker.io/library/", "docker.io/")
        
        if source_repo:
            # Find the resource to get its containers
            import os
            local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
            
            log_event(
                "patch_resource.filtering_start",
                namespace=body.namespace,
                resource=body.name,
                source_image=body.source_image,
                source_repo=source_repo,
                local_platform=local_platform,
            )
            
            resource = db.query(Resource).filter(
                Resource.platform == local_platform,
                Resource.namespace == body.namespace,
                Resource.resource_name == body.name,
                Resource.kind.ilike(body.kind),
            ).first()
            
            if not resource:
                log_event(
                    "patch_resource.resource_not_found",
                    namespace=body.namespace,
                    resource=body.name,
                    platform=local_platform,
                )
            elif not resource.security_info:
                log_event(
                    "patch_resource.no_security_info",
                    namespace=body.namespace,
                    resource=body.name,
                )
            elif not resource.security_info.get("containers"):
                log_event(
                    "patch_resource.no_containers_in_security_info",
                    namespace=body.namespace,
                    resource=body.name,
                    security_info_keys=list(resource.security_info.keys()) if resource.security_info else [],
                )
            else:
                containers_info = []
                for c in resource.security_info["containers"]:
                    c_image = c.get("image", "")
                    c_name = c.get("name", "")
                    c_parsed = parse_image(c_image)
                    if c_parsed:
                        c_repo = f"{c_parsed.registry}/{c_parsed.repository}".lower()
                        if c_repo.startswith("docker.io/"):
                            c_repo = c_repo.replace("docker.io/library/", "docker.io/")
                        
                        containers_info.append({
                            "name": c_name,
                            "image": c_image,
                            "repo": c_repo,
                            "matches": c_repo == source_repo,
                        })
                        
                        if c_repo == source_repo:
                            container_to_patch = c_name
                            log_event(
                                "patch_resource.container_matched",
                                namespace=body.namespace,
                                resource=body.name,
                                container=container_to_patch,
                                source_repo=source_repo,
                                container_repo=c_repo,
                            )
                            break
                
                if not container_to_patch:
                    log_event(
                        "patch_resource.no_container_matched",
                        namespace=body.namespace,
                        resource=body.name,
                        source_repo=source_repo,
                        containers=containers_info,
                    )
    
    # SAFETY CHECK: If source_image was provided but no container matched,
    # do NOT patch anything (would patch all containers which is wrong)
    if body.source_image and not container_to_patch:
        log_event(
            "patch_resource.no_container_match",
            namespace=body.namespace,
            resource=body.name,
            source_image=body.source_image,
            message="No container matched source_image repository, refusing to patch all",
        )
        return {
            "success": False,
            "old_image": None,
            "new_image": body.new_image,
            "error": f"No container found matching source image repository: {body.source_image}. This prevents accidentally patching wrong containers.",
        }
    
    success, old_image, error = service.patch_resource_image(
        namespace=body.namespace,
        name=body.name,
        kind=body.kind,
        new_image=body.new_image,
        container_name=container_to_patch,
    )
    
    # Update the local resource row after a successful patch — in the BACKGROUND.
    # _update_resource_after_patch does slow outbound enrichment (registry latest-version
    # lookups can take 10s+, EOL, etc.). Doing it inline would keep the HTTP response open
    # past the primary's ~30s forward timeout, which then marks the job FAILED even though
    # the patch already succeeded. The patch result (success/old_image) is known now, so
    # return it immediately and enrich asynchronously (the remote's watch/sync also refresh
    # the row). Mirrors the existing daemon-thread workers; uses its own DB session.
    if success:
        import os
        import threading
        from ..db import SessionLocal
        local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
        _ns, _name, _kind = body.namespace, body.name, body.kind
        _new_image, _container, _job_id = body.new_image, container_to_patch, body.job_id

        def _post_patch_enrich() -> None:
            bg_db = SessionLocal()
            try:
                res = bg_db.query(Resource).filter(
                    Resource.platform == local_platform,
                    Resource.namespace == _ns,
                    Resource.resource_name == _name,
                    Resource.kind.ilike(_kind),
                ).first()
                if res:
                    service._update_resource_after_patch(
                        db=bg_db, resource=res, container_name=_container,
                        new_image=_new_image, job_id=_job_id,
                    )
                    bg_db.commit()
                    log_event("patch_resource.db_updated", namespace=_ns, resource=_name,
                              container=_container, job_id=_job_id, background=True)
            except Exception as e:
                log_event("patch_resource.bg_update_error", namespace=_ns, resource=_name, error=str(e))
                try:
                    bg_db.rollback()
                except Exception:
                    pass
            finally:
                bg_db.close()

        threading.Thread(target=_post_patch_enrich, daemon=True).start()

    return {
        "success": success,
        "old_image": old_image,
        "new_image": body.new_image,
        "error": error,
        "container": container_to_patch,  # the container the remote actually patched
    }


# ==================== Health Check Endpoint (for federation) ====================

class HealthCheckRequest(BaseModel):
    namespace: str
    name: str
    kind: str
    pre_patch_state: Optional[dict] = None
    timeout_seconds: int = 300
    crash_tolerance: int = 120


@router.post("/health-check")
def health_check_resource(
    body: HealthCheckRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Smart Watch health check for federation.

    Called by primary backend to verify health on this (secondary) backend.
    Tolerates transient CrashLoopBackOff/ImagePullBackOff for crash_tolerance seconds.
    """
    try:
        _decode_token(token)
    except Exception:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")

    service = get_image_update_service()

    health_passed, post_state, message = service.wait_for_rollout_and_health_check(
        namespace=body.namespace,
        name=body.name,
        kind=body.kind,
        pre_patch_state=body.pre_patch_state,
        timeout_seconds=body.timeout_seconds,
        crash_tolerance=body.crash_tolerance,
    )

    log_event(
        "health_check.completed",
        namespace=body.namespace,
        resource=body.name,
        kind=body.kind,
        health_passed=health_passed,
        message=message,
    )

    return {
        "health_passed": health_passed,
        "post_state": post_state,
        "message": message,
    }


# ==================== Remote Job Execution Endpoint (for federation) ====================

# In-memory task store for async job execution
# Key: task_id, Value: {status, results, started_at, finished_at, error}
import uuid
from typing import Dict, Any

_async_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()

# Cleanup old tasks (older than 1 hour)
def _cleanup_old_tasks():
    with _tasks_lock:
        now = datetime.now(timezone.utc)
        to_delete = []
        for task_id, task in _async_tasks.items():
            if task.get("finished_at"):
                age = (now - task["finished_at"]).total_seconds()
                if age > 3600:  # 1 hour
                    to_delete.append(task_id)
        for task_id in to_delete:
            del _async_tasks[task_id]


class ExecuteForProductRequest(BaseModel):
    """Request to execute image updates for a product on this backend."""
    product_name: str
    target_image: str
    source_image: Optional[str] = None
    target_namespaces: Optional[List[str]] = None
    health_check_enabled: bool = True
    health_check_timeout: int = 300
    crash_tolerance: int = 120
    auto_rollback: bool = True
    job_id: Optional[int] = None
    patch_batch_size: int = 0
    patch_batch_pause: int = 2


def _run_batch_smart_watch(
    task_id: str,
    batch_num: int,
    pending_monitors: list,
    results: dict,
    service,
    db,
    body,
    total_resources: int,
) -> bool:
    """Run concurrent Smart Watch monitoring for a single batch of patched resources.
    Returns True if an ImagePullBackOff/ErrImagePull failure was detected (job should abort)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total_monitoring = len(pending_monitors)
    log_event(
        "image_update.execute_for_product.batch_smart_watch",
        task_id=task_id, batch=batch_num, count=total_monitoring,
    )
    with _tasks_lock:
        completed_so_far = results["success_count"] + results["failed_count"] + results["skipped_count"] + results["rolled_back_count"]
        _async_tasks[task_id]["progress"] = f"Batch {batch_num} Smart Watch: monitoring {total_monitoring} resources..."
        _async_tasks[task_id]["current"] = completed_so_far

    db.expire_on_commit = False

    def _monitor_single(item):
        return service.wait_for_rollout_and_health_check(
            namespace=item["namespace"],
            name=item["resource_name"],
            kind=item["kind"],
            pre_patch_state=item["pre_patch_state"],
            timeout_seconds=body.health_check_timeout,
            crash_tolerance=body.crash_tolerance,
        )

    max_workers = min(total_monitoring, 10)
    ready_count = 0
    rb_count = 0
    monitor_completed = 0
    image_pull_failure = False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_monitor_single, item): item
            for item in pending_monitors
        }

        for future in as_completed(future_map):
            item = future_map[future]
            ns = item["namespace"]
            res_name = item["resource_name"]
            res_kind = item["kind"]

            try:
                health_passed, post_state, health_msg = future.result()
            except Exception as e:
                health_passed = False
                health_msg = f"Monitor error: {e}"

            if health_passed:
                ready_count += 1
                results["success_count"] += 1
                results["details"].append({
                    "namespace": ns,
                    "resource": res_name,
                    "kind": res_kind,
                    "container": item["container_name"],
                    "old_image": item["old_image"],
                    "status": "success",
                    "health_passed": True,
                    "health_message": health_msg,
                })
            else:
                if body.auto_rollback and item["old_image"]:
                    rollback_ok, _, _ = service.patch_resource_image(
                        namespace=ns, name=res_name, kind=res_kind,
                        new_image=item["old_image"],
                        container_name=item["container_name"],
                    )
                    if rollback_ok:
                        try:
                            service._update_resource_after_patch(
                                db, item["resource"], item["container_name"],
                                item["old_image"], job_id=body.job_id)
                            db.commit()
                        except Exception as db_err:
                            log_event("image_update.rollback_db_error",
                                      resource=res_name, error=str(db_err))
                            try:
                                db.rollback()
                            except Exception:
                                pass
                    rb_count += 1
                    results["rolled_back_count"] += 1
                    results["details"].append({
                        "namespace": ns,
                        "resource": res_name,
                        "kind": res_kind,
                        "container": item["container_name"],
                        "old_image": item["old_image"],
                        "status": "rolled_back",
                        "health_passed": False,
                        "health_message": health_msg,
                    })
                    if health_msg and ("ImagePullBackOff" in health_msg or "ErrImagePull" in health_msg):
                        image_pull_failure = True
                else:
                    results["failed_count"] += 1
                    results["details"].append({
                        "namespace": ns,
                        "resource": res_name,
                        "kind": res_kind,
                        "container": item["container_name"],
                        "old_image": item["old_image"],
                        "status": "failed",
                        "health_passed": False,
                        "error": f"Health check failed: {health_msg}",
                        "health_message": health_msg,
                    })

            monitor_completed += 1
            remaining = total_monitoring - monitor_completed
            with _tasks_lock:
                completed_now = results["success_count"] + results["failed_count"] + results["skipped_count"] + results["rolled_back_count"]
                _async_tasks[task_id]["progress"] = f"Batch {batch_num} Smart Watch: {ready_count} ready, {rb_count} rolled back, {remaining} monitoring"
                _async_tasks[task_id]["current"] = completed_now

    log_event(
        "image_update.execute_for_product.batch_smart_watch_complete",
        task_id=task_id, batch=batch_num,
        ready=ready_count, rolled_back=rb_count,
        image_pull_failure=image_pull_failure,
    )
    return image_pull_failure


def _execute_for_product_worker(
    task_id: str,
    body: ExecuteForProductRequest,
    auth_token: str,
):
    """Background worker that patches resources in batches with Smart Watch
    monitoring after each batch completes.
    """
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from ..db import SessionLocal
    from ..services.versioning import parse_image
    
    db = SessionLocal()
    
    try:
        local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
        service = get_image_update_service()
        
        log_event(
            "image_update.execute_for_product.worker_start",
            task_id=task_id,
            product_name=body.product_name,
            local_platform=local_platform,
        )
        
        resources = service.get_matching_resources(
            db,
            body.product_name,
            [local_platform],
            body.target_namespaces,
            None,
        )
        
        log_event(
            "image_update.execute_for_product.resources_found",
            task_id=task_id,
            product_name=body.product_name,
            resource_count=len(resources),
        )
        
        with _tasks_lock:
            _async_tasks[task_id]["progress"] = f"Found {len(resources)} resource(s) to update"
            _async_tasks[task_id]["total_expected"] = len(resources)
        
        if not resources:
            with _tasks_lock:
                _async_tasks[task_id].update({
                    "status": "completed",
                    "finished_at": datetime.now(timezone.utc),
                    "results": {
                        "success": True,
                        "platform": local_platform,
                        "total": 0,
                        "success_count": 0,
                        "failed_count": 0,
                        "skipped_count": 0,
                        "rolled_back_count": 0,
                        "details": [],
                        "message": f"No resources found for product {body.product_name}",
                    }
                })
            return
        
        # Parse source/target images for filtering
        source_repo = None
        source_tag = None
        target_repo = None
        try:
            if body.source_image:
                if ':' in body.source_image.split('/')[-1]:
                    raw_repo, source_tag = body.source_image.rsplit(':', 1)
                else:
                    raw_repo = body.source_image
                source_parsed = parse_image(raw_repo + ":dummy")
                if source_parsed:
                    source_repo = source_parsed.repository.lower()
            target_parsed = parse_image(body.target_image)
            if target_parsed:
                target_repo = target_parsed.repository.lower()
        except Exception:
            pass
        
        def _repo_matches(cr, sr):
            if not cr or not sr:
                return False
            if cr == sr:
                return True
            return cr.endswith('/' + sr)
        
        results = {
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "rolled_back_count": 0,
            "details": [],
        }
        
        # ── Phase 1: Patch all resources with batch pauses ──
        pending_monitors = []
        resource_idx = 0
        
        for r in resources:
            resource_idx += 1
            containers_to_update = []
            
            if r.security_info and r.security_info.get("containers"):
                for c in r.security_info["containers"]:
                    container_image = c.get("image", "")
                    container_repo = None
                    container_tag = None
                    if container_image:
                        try:
                            container_parsed = parse_image(container_image)
                            if container_parsed:
                                container_repo = container_parsed.repository.lower()
                                container_tag = container_parsed.tag
                        except Exception:
                            container_repo = container_image.split(':')[0].lower()
                    
                    if source_repo and container_repo:
                        if not _repo_matches(container_repo, source_repo):
                            continue
                        if source_tag and container_tag and container_tag != source_tag:
                            continue
                    elif target_repo and container_repo:
                        if not _repo_matches(container_repo, target_repo):
                            continue
                    
                    containers_to_update.append({
                        "name": c.get("name"),
                        "current_image": container_image,
                    })
            else:
                single_repo = None
                single_tag = None
                if r.image:
                    try:
                        single_parsed = parse_image(r.image)
                        if single_parsed:
                            single_repo = single_parsed.repository.lower()
                            single_tag = single_parsed.tag
                    except Exception:
                        single_repo = r.image.split(':')[0].lower()
                
                should_include = True
                if source_repo and single_repo:
                    should_include = _repo_matches(single_repo, source_repo)
                    if should_include and source_tag and single_tag and single_tag != source_tag:
                        should_include = False
                elif target_repo and single_repo:
                    should_include = _repo_matches(single_repo, target_repo)
                if should_include:
                    containers_to_update.append({
                        "name": None,
                        "current_image": r.image,
                    })
            
            if not containers_to_update:
                results["total"] += 1
                results["skipped_count"] += 1
                results["details"].append({
                    "namespace": r.namespace,
                    "resource": r.resource_name,
                    "kind": r.kind,
                    "status": "skipped",
                    "message": "No matching containers found",
                })
                continue
            
            for container_info in containers_to_update:
                results["total"] += 1
                container_name = container_info["name"]
                old_image = container_info["current_image"]
                container_display = container_name or "main"
                
                with _tasks_lock:
                    done = results["success_count"] + results["failed_count"] + results["skipped_count"] + results["rolled_back_count"] + len(pending_monitors)
                    _async_tasks[task_id]["progress"] = f"Patching {resource_idx}/{len(resources)}: {r.namespace}/{r.resource_name} ({container_display})"
                    _async_tasks[task_id]["current"] = done
                    _async_tasks[task_id]["total_expected"] = len(resources)
                
                pre_patch_state = None
                if body.health_check_enabled:
                    pre_patch_state = service.get_resource_state(r.namespace, r.resource_name, r.kind)
                
                success, patched_old_image, error = service.patch_resource_image(
                    namespace=r.namespace,
                    name=r.resource_name,
                    kind=r.kind,
                    new_image=body.target_image,
                    container_name=container_name,
                )
                
                if not success:
                    results["failed_count"] += 1
                    results["details"].append({
                        "namespace": r.namespace,
                        "resource": r.resource_name,
                        "kind": r.kind,
                        "container": container_name,
                        "old_image": old_image,
                        "status": "failed",
                        "error": error,
                    })
                    continue
                
                service._update_resource_after_patch(
                    db=db,
                    resource=r,
                    container_name=container_name,
                    new_image=body.target_image,
                    job_id=body.job_id,
                )
                db.commit()
                
                if body.health_check_enabled:
                    pending_monitors.append({
                        "resource": r,
                        "namespace": r.namespace,
                        "resource_name": r.resource_name,
                        "kind": r.kind,
                        "container_name": container_name,
                        "old_image": old_image,
                        "pre_patch_state": pre_patch_state,
                    })
                else:
                    results["success_count"] += 1
                    results["details"].append({
                        "namespace": r.namespace,
                        "resource": r.resource_name,
                        "kind": r.kind,
                        "container": container_name,
                        "old_image": old_image,
                        "status": "success",
                        "health_passed": True,
                        "health_message": "Monitoring disabled",
                    })
            
            # Batch boundary: run Smart Watch for this batch, then pause
            if (body.patch_batch_size > 0
                    and resource_idx % body.patch_batch_size == 0
                    and resource_idx < len(resources)):
                batch_num = resource_idx // body.patch_batch_size

                if body.health_check_enabled and pending_monitors:
                    ipf = _run_batch_smart_watch(
                        task_id, batch_num, pending_monitors, results,
                        service, db, body, len(resources),
                    )
                    pending_monitors.clear()

                    if ipf:
                        error_msg = f"Image pull failed after batch {batch_num}. Verify target image exists and is accessible: {body.target_image}"
                        log_event("image_update.execute_for_product.image_pull_abort",
                                  task_id=task_id, batch=batch_num)
                        with _tasks_lock:
                            _async_tasks[task_id].update({
                                "status": "completed",
                                "progress": error_msg,
                                "results": results,
                                "finished_at": datetime.now(timezone.utc),
                            })
                        return

                if body.patch_batch_pause > 0 and not body.health_check_enabled:
                    with _tasks_lock:
                        _async_tasks[task_id]["progress"] = f"Batch {batch_num} done ({resource_idx}/{len(resources)}). Pausing {body.patch_batch_pause}s..."
                    time.sleep(body.patch_batch_pause)
        
        # Update total_expected to the actual item count
        with _tasks_lock:
            _async_tasks[task_id]["total_expected"] = results["total"]
        
        # Smart Watch remaining resources from last (incomplete) batch
        db.expire_on_commit = False

        if pending_monitors:
            batch_num = ((resource_idx - 1) // body.patch_batch_size) + 1 if body.patch_batch_size > 0 else 0
            ipf = _run_batch_smart_watch(
                task_id, batch_num, pending_monitors, results,
                service, db, body, len(resources),
            )
            if ipf:
                error_msg = f"Image pull failed. Verify target image exists and is accessible: {body.target_image}"
                log_event("image_update.execute_for_product.image_pull_abort",
                          task_id=task_id)
                with _tasks_lock:
                    _async_tasks[task_id].update({
                        "status": "completed",
                        "progress": error_msg,
                        "results": results,
                        "finished_at": datetime.now(timezone.utc),
                    })
                return

        # Mark task as completed
        with _tasks_lock:
            success_emoji = "✓" if results["failed_count"] == 0 and results["rolled_back_count"] == 0 else "⚠️"
            _async_tasks[task_id].update({
                "status": "completed",
                "finished_at": datetime.now(timezone.utc),
                "progress": f"{success_emoji} Done: {results['success_count']} success, {results['failed_count']} failed, {results['rolled_back_count']} rolled back",
                "current": results["total"],
                "total_expected": results["total"],
                "results": {
                    "success": True,
                    "platform": local_platform,
                    **results,
                }
            })
        
        log_event(
            "image_update.execute_for_product.worker_complete",
            task_id=task_id,
            total=results["total"],
            success=results["success_count"],
            failed=results["failed_count"],
        )
        
    except Exception as e:
        log_event(
            "image_update.execute_for_product.worker_error",
            task_id=task_id,
            error=str(e),
        )
        with _tasks_lock:
            _async_tasks[task_id].update({
                "status": "failed",
                "finished_at": datetime.now(timezone.utc),
                "error": str(e),
            })
    finally:
        db.close()


@router.post("/execute-for-product")
def execute_for_product(
    body: ExecuteForProductRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Execute image updates for a product on this backend (async).
    
    This endpoint immediately returns a task_id. The actual execution
    happens in a background thread. Poll /execute-for-product/{task_id}/status
    to get results.
    """
    import os
    
    # Verify token - must be backend auth token (not user token for security)
    try:
        claims = _decode_token(token, db)
        if claims.get("role") != "backend":
            raise HTTPException(status_code=403, detail="Only backend-to-backend calls allowed")
    except Exception:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")
    
    # Cleanup old tasks
    _cleanup_old_tasks()
    
    # Generate task ID
    task_id = str(uuid.uuid4())
    local_platform = os.getenv("BACKEND_PLATFORM", "default-platform")
    
    # Initialize task
    with _tasks_lock:
        _async_tasks[task_id] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "finished_at": None,
            "progress": f"🔍 Finding resources for {body.product_name}...",
            "current": 0,
            "total_expected": 1,  # Will be updated when resources are found
            "results": None,
            "error": None,
            "product_name": body.product_name,
            "primary_job_id": body.job_id,
        }
    
    log_event(
        "image_update.execute_for_product.async_start",
        task_id=task_id,
        product_name=body.product_name,
        target_image=body.target_image,
        local_platform=local_platform,
        primary_job_id=body.job_id,
    )
    
    # Start background thread
    thread = threading.Thread(
        target=_execute_for_product_worker,
        args=(task_id, body, token),
        daemon=True,
    )
    thread.start()
    
    # Return immediately with task_id
    return {
        "async": True,
        "task_id": task_id,
        "status": "running",
        "platform": local_platform,
        "message": "Task started. Poll /execute-for-product/{task_id}/status for results.",
    }


@router.get("/execute-for-product/{task_id}/status")
def get_task_status(
    task_id: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """
    Get the status of an async execute-for-product task.
    
    Returns status ("running", "completed", "failed") and results when complete.
    """
    # Verify token
    try:
        claims = _decode_token(token, db)
        if claims.get("role") != "backend":
            raise HTTPException(status_code=403, detail="Only backend-to-backend calls allowed")
    except Exception:
        backend = db.query(BackendEndpoint).filter(
            BackendEndpoint.auth_token == token,
            BackendEndpoint.approved == True,
        ).first()
        if not backend:
            raise HTTPException(status_code=401, detail="Invalid token")
    
    with _tasks_lock:
        task = _async_tasks.get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    response = {
        "task_id": task_id,
        "status": task["status"],
        "progress": task.get("progress"),
        "current": task.get("current", 0),
        "total_expected": task.get("total_expected", 0),
        "started_at": task["started_at"].isoformat() if task["started_at"] else None,
        "finished_at": task["finished_at"].isoformat() if task["finished_at"] else None,
    }
    
    if task["status"] == "completed":
        response["results"] = task["results"]
    elif task["status"] == "failed":
        response["error"] = task.get("error")
    
    return response


# ==================== Preview Endpoint ====================

class PreviewRequest(BaseModel):
    product_name: str
    source_image: Optional[str] = None  # Filter: only containers matching this image repo
    target_image: str
    target_platforms: Optional[List[str]] = None
    target_namespaces: Optional[List[str]] = None
    target_resource_ids: Optional[List[int]] = None  # DEPRECATED: use target_resource_keys
    target_resource_keys: Optional[List[str]] = None  # Format: "platform|namespace|kind|resource_name"


@router.post("/preview")
def preview_update(
    body: PreviewRequest,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    """Preview which resources would be affected by an update."""
    _decode_token(token)
    
    service = get_image_update_service()
    # Prefer resource keys over deprecated IDs for cross-backend support
    target_resources = body.target_resource_keys or body.target_resource_ids
    resources = service.get_matching_resources(
        db,
        body.product_name,
        body.target_platforms,
        body.target_namespaces,
        target_resources,
    )
    
    # Parse source_image to get repository for matching
    source_repo = None
    target_repo = None
    if body.source_image:
        from ..services.versioning import parse_image
        try:
            source_parsed = parse_image(body.source_image + ":dummy")
            if source_parsed:
                source_repo = source_parsed.repository.lower()
            else:
                source_repo = body.source_image.lower()
        except Exception:
            source_repo = body.source_image.lower().split(':')[0]
    
    try:
        from ..services.versioning import parse_image
        target_parsed = parse_image(body.target_image)
        if target_parsed:
            target_repo = target_parsed.repository.lower()
    except Exception:
        pass
    
    affected = []
    for r in resources:
        containers = []
        if r.security_info and r.security_info.get("containers"):
            for c in r.security_info["containers"]:
                current = c.get("image", "")
                
                # Apply source image filter
                if source_repo:
                    try:
                        from ..services.versioning import parse_image
                        container_parsed = parse_image(current)
                        if container_parsed:
                            container_repo = container_parsed.repository.lower()
                        else:
                            container_repo = current.split(':')[0].lower()
                    except Exception:
                        container_repo = current.split(':')[0].lower()
                    
                    if container_repo != source_repo:
                        continue  # Skip containers that don't match source image
                elif target_repo:
                    # If no source image, match against target image repo
                    try:
                        from ..services.versioning import parse_image
                        container_parsed = parse_image(current)
                        if container_parsed:
                            container_repo = container_parsed.repository.lower()
                        else:
                            container_repo = current.split(':')[0].lower()
                    except Exception:
                        container_repo = current.split(':')[0].lower()
                    
                    if container_repo != target_repo:
                        continue  # Skip containers that don't match target repo
                
                will_change = current != body.target_image
                containers.append({
                    "name": c.get("name"),
                    "current_image": current,
                    "will_change": will_change,
                })
        else:
            will_change = r.image != body.target_image
            containers.append({
                "name": None,
                "current_image": r.image,
                "will_change": will_change,
            })
        
        # Only include resource if it has containers to update
        if containers:
            affected.append({
                "id": r.id,
                "platform": r.platform,
                "namespace": r.namespace,
                "resource_name": r.resource_name,
                "kind": r.kind,
                "containers": containers,
            })
    
    will_change_count = sum(
        1 for a in affected
        for c in a["containers"]
        if c["will_change"]
    )
    
    return {
        "product_name": body.product_name,
        "target_image": body.target_image,
        "total_resources": len(affected),
        "total_containers": sum(len(a["containers"]) for a in affected),
        "will_change": will_change_count,
        "affected": affected,
    }

