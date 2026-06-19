from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class HistoryItem(BaseModel):
    version: Optional[str] = None
    checked_at: Optional[datetime] = None
    image: Optional[str] = None
    latest_version: Optional[str] = None
    version_diff: Optional[str] = None
    container_name: Optional[str] = None
    source: Optional[str] = None
    job_id: Optional[int] = None


class ResourceOut(BaseModel):
    id: int
    platform: str
    namespace: str
    resource_name: str
    kind: str
    replicas: Optional[int] = None  # Current replica count (0 = scaled down/no pods)
    image: Optional[str] = None
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    version_diff: Optional[str] = None
    vulnerability_count: Optional[int] = None
    vulnerabilities: Optional[list[str]] = None
    advice: Optional[str] = None
    note: Optional[str] = None
    planned_upgrade_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    security_info: Optional[dict] = None
    containers: Optional[list[dict]] = None
    container_name: Optional[str] = None
    update_history: Optional[List[HistoryItem]] = None
    product_name: Optional[str] = None
    eol_date: Optional[str] = None
    # LLM-derived support status, shown only when there is no public EOL date.
    eol_support_status: Optional[str] = None
    eol_support_note: Optional[str] = None
    # Helm release status (Phase 1A): ok | drift | none | unknown. drift detail is lazy.
    helm_status: Optional[str] = None
    helm_release_name: Optional[str] = None

    class Config:
        from_attributes = True


class RefreshResponse(BaseModel):
    refreshed: int
    errors: int
    deleted: int


class PlanUpdate(BaseModel):
    note: Optional[str] = None
    planned_upgrade_at: Optional[str] = None
    apply_to_all: Optional[bool] = False
    match_image: Optional[str] = None


# Image Update schemas
class ProductImageInfo(BaseModel):
    """Information about a product's images across platforms"""
    product_name: str
    platforms: List[str]
    namespaces: List[str]
    resources: List[dict]  # [{platform, namespace, resource_name, kind, current_image, latest_image}]
    current_images: List[str]  # Unique current images
    latest_images: List[str]  # Unique latest images available


class ImageUpdateJobCreate(BaseModel):
    """Request to create an image update job"""
    product_name: str
    source_image: Optional[str] = None  # Filter to only update containers matching this image repo
    target_image: Optional[str] = None  # Optional when field_edits is non-empty (fields-only job)
    image_source: str = "custom"  # 'latest' or 'custom'
    target_platforms: Optional[List[str]] = None  # null = all platforms
    target_namespaces: Optional[List[str]] = None  # null = all namespaces in selected platforms
    target_resource_ids: Optional[List[int]] = None  # DEPRECATED: use target_resource_keys for cross-backend support
    target_resource_keys: Optional[List[str]] = None  # Format: "platform|namespace|kind|resource_name"
    # Phase 1: structured, whitelisted manifest edits (env/resource/command/args/label/annotation).
    field_edits: Optional[List[dict]] = None
    # Required true to apply to Helm-managed targets (a direct patch is reverted by the next
    # helm upgrade); enforced for field-edit jobs at create and again by the executor.
    helm_managed_ack: bool = False
    scheduled_at: Optional[datetime] = None  # null = immediate (after approval)
    approval_required: bool = True
    health_check_enabled: bool = True
    health_check_mode: str = "smart_watch"  # "off" or "smart_watch"
    notes: Optional[str] = None


class ImageUpdateJobResponse(BaseModel):
    """Response for an image update job"""
    id: int
    product_name: str
    source_image: Optional[str] = None
    target_image: Optional[str] = None
    image_source: str
    target_platforms: Optional[List[str]] = None
    target_namespaces: Optional[List[str]] = None
    field_edits: Optional[List[dict]] = None
    helm_managed_ack: bool = False
    scheduled_at: Optional[datetime] = None
    status: str
    approval_required: bool
    health_check_enabled: bool = True
    health_check_mode: str = "smart_watch"
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    trigger_token: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_by: str
    created_at: datetime
    notes: Optional[str] = None
    results_summary: Optional[dict] = None  # {total, success, failed, skipped}
    # Live progress
    progress_total: Optional[int] = None
    progress_current: Optional[int] = None
    progress_message: Optional[str] = None
    cancel_requested: bool = False
    cancelled_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ImageUpdateResultResponse(BaseModel):
    """Response for an individual update result"""
    id: int
    platform: str
    namespace: str
    resource_name: str
    kind: str
    container_name: Optional[str] = None
    old_image: Optional[str] = None
    new_image: str
    status: str
    error_message: Optional[str] = None
    executed_at: datetime
    
    # Health check and state info
    pre_patch_state: Optional[dict] = None  # {replicas, ready_replicas, pod_statuses}
    post_patch_state: Optional[dict] = None
    health_check_passed: Optional[bool] = None
    health_check_message: Optional[str] = None
    health_checked_at: Optional[datetime] = None
    
    # Rollback info
    was_rolled_back: bool = False
    rollback_reason: Optional[str] = None

    # Phase 1: applied field-edits audit + Helm-managed flag (pre_patch_manifest is the
    # encrypted rollback snapshot and is intentionally NOT exposed in the API).
    field_changes: Optional[List[dict]] = None
    helm_managed: Optional[bool] = None

    # Detailed logs
    execution_log: Optional[List[dict]] = None  # [{timestamp, level, message}]

    class Config:
        from_attributes = True


class ImageUpdateApproval(BaseModel):
    """Approval action for a job"""
    approved: bool
    notes: Optional[str] = None


class ImageUpdateTriggerResponse(BaseModel):
    """Response for pipeline trigger endpoint"""
    job_id: int
    status: str
    message: str
    results: Optional[List[dict]] = None