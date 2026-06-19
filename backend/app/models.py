from __future__ import annotations

from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("platform", "namespace", "resource_name", "kind", name="uix_resource_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    replicas: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Current replica count (0 = scaled down)
    product_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    current_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latest_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    version_diff: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # major/minor/patch/same/unknown

    last_updated_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[Optional[str]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    security_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    eol_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # LLM-derived support status, used ONLY when there is no public EOL (endoflife.date)
    # data. Advisory/AI-derived (may be outdated). status: supported | eol | unknown;
    # note: a one-line summary. Dedicated columns (not in security_info) so a forced
    # security_info rebuild can't clobber them.
    eol_support_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    eol_support_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Cached manifest YAML for comparison (JSON format, cleaned of runtime fields)
    manifest_yaml: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    manifest_updated_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Related objects for comparison (PDB, HPA, Service, ConfigMap, Secret, Route)
    related_objects: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Last container-spec hash the real-time watch refreshed on. Used to skip
    # re-refreshing unchanged resources after a backend restart (kube_watch seed).
    watch_spec_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Precomputed compact security/display summary for the resources grid (slim list).
    # Holds exactly what the grid needs (distributions, riskFactorCount, advice,
    # trimmed containers) so the list query never detoasts the large security_info blob.
    sec_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # --- Helm release status (Phase 1A) ---
    # Drift of the live manifest vs what Helm last rendered for this resource, computed
    # during refresh (see kube.get_helm_status). Dedicated columns (not inside
    # security_info) so a forced refresh rebuilding that blob can't clobber them.
    helm_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # ok | drift | none | unknown
    helm_release_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    helm_release_namespace: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Release revision drift was last computed against — perf-dedupe key so we don't
    # gunzip/parse the whole-chart release Secret on every refresh.
    helm_release_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Per-field drift diff [{path, helm, live}] (Secret-object values redacted). Lazy-loaded
    # on click via resource-detail; kept OUT of the slim grid SELECT to avoid detoast.
    helm_drift_detail: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    history: Mapped[list[ResourceHistory]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    plan: Mapped[Optional["ResourcePlan"]] = relationship(
        back_populates="resource", uselist=False, cascade="all, delete-orphan"
    )


class ResourceHistory(Base):
    __tablename__ = "resource_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), nullable=False, index=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)

    image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latest_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    version_diff: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    checked_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    security_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    resource: Mapped[Resource] = relationship(back_populates="history")


class ResourcePlan(Base):
    __tablename__ = "resource_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), unique=True, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    planned_upgrade_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)

    resource: Mapped[Resource] = relationship(back_populates="plan")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # 'admin' | 'read-only'


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    password_must_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    theme: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="dark")
    font_size: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="medium")
    ldap_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LdapConfig(Base):
    """LDAP/AD integration configuration"""
    __tablename__ = "ldap_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Server settings
    server_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # ldaps://ldaps.example.com
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=636)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # TLS/SSL settings
    ca_cert: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # PEM-encoded CA certificate for self-signed certs
    skip_cert_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Skip certificate verification
    # Bind credentials
    bind_dn: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # CN=service_account,OU=...
    bind_password: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Encrypted
    # Search settings
    base_dn: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # DC=example,DC=com
    user_search_filter: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # (&(objectClass=user)(sAMAccountName={username}))
    user_search_base: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # OU=Users,DC=example,DC=com
    # Attribute mappings
    username_attribute: Mapped[str] = mapped_column(String(64), nullable=False, default="sAMAccountName")
    email_attribute: Mapped[str] = mapped_column(String(64), nullable=False, default="mail")
    display_name_attribute: Mapped[str] = mapped_column(String(64), nullable=False, default="displayName")
    # Group settings
    group_search_base: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    group_search_filter: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    admin_group_dn: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Group DN for admin role
    analyst_group_dn: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Group DN for analyst role
    readonly_group_dn: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Group DN for read-only role
    # Timestamps
    updated_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfigKV(Base):
    __tablename__ = "config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backend_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)  # Which backend ran this sync
    platform: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)  # Which platform (for display)
    started_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    finished_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # username or 'auto'
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # 'running', 'success', 'error'
    resources_refreshed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    errors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Duration in seconds


class UserChart(Base):
    __tablename__ = "user_charts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chart_name: Mapped[str] = mapped_column(String(256), nullable=False)
    group_by: Mapped[str] = mapped_column(String(64), nullable=False)  # product_name, namespace, etc.
    count_by: Mapped[str] = mapped_column(String(64), nullable=False)  # count, eol_issues, security_risks, etc.
    filter_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # DEPRECATED: use filters
    filter_value: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)  # DEPRECATED: use filters
    filters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array: [{"column":"platform","value":"prod"}]
    chart_type: Mapped[str] = mapped_column(String(16), nullable=False, default="bar", server_default="bar")  # 'bar' or 'pie'
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BackendEndpoint(Base):
    __tablename__ = "backend_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)  # "prod-api", "staging-api"
    platform: Mapped[str] = mapped_column(String(64), nullable=False)  # "prod-openshift", "staging-openshift"
    api_url: Mapped[str] = mapped_column(String(512), nullable=False)  # "https://api.example.com"
    auth_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)  # Pre-generated auth token
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # True for local backend
    backend_type: Mapped[str] = mapped_column(String(16), nullable=False, default="secondary")  # "primary" or "secondary"
    skip_tls_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Skip TLS verification
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Backend connected and verified
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Username who created this
    approved_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)  # When backend first connected
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Optional notes about this backend
    auto_sync_schedule: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Cron expression or seconds
    force_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Force sync mode
    cleanup_force_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Bypass cleanup safety guard (10% delete cap)
    use_dynamic_discovery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Dynamic resource discovery
    latest_version_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)  # "local", "primary", or None (use global)
    twistlock_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)  # "local", "primary", or None (use global)
    trivy_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)  # "local", "primary", or None (use global)
    eol_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)  # "local", "primary", or None (use global)
    llm_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)  # "local", "primary", or None (use global)
    ldap_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="primary")  # "local" or "primary" (use primary's LDAP)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)  # Last heartbeat


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Optional display name
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComparisonReport(Base):
    """Saved comparison reports for later review"""
    __tablename__ = "comparison_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)  # User-given name for the report
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Username who created it
    
    # Comparison parameters
    cluster1: Mapped[str] = mapped_column(String(128), nullable=False)
    cluster2: Mapped[str] = mapped_column(String(128), nullable=False)
    cluster1_namespaces: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    cluster2_namespaces: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    cluster1_kinds: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    cluster2_kinds: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    
    # Summary counts
    common_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    only_in_cluster1_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    only_in_cluster2_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    with_differences_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_diffs_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    
    # Full comparison data (JSON) - includes comparisons, only_in_cluster1, only_in_cluster2
    comparison_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class ProductList(Base):
    """List of all products from endoflife.date API"""
    __tablename__ = "product_list_table"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ManagedProduct(Base):
    """Products that the backend is responsible for"""
    __tablename__ = "managed_product_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backend_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # Which backend manages this
    product_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    image_pattern: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # e.g. "apache/apisix" — overrides product_name for image matching
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # True if manually added
    added_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # Username who added it
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Unique constraint: one product per backend
    __table_args__ = (
        UniqueConstraint('backend_name', 'product_name', name='uq_backend_product'),
    )


class ImageUpdateJob(Base):
    """Image update jobs for patching resources"""
    __tablename__ = "image_update_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    source_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Filter: only update containers matching this image repo
    target_image: Mapped[str] = mapped_column(String(512), nullable=False)  # The new image to patch to
    image_source: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")  # 'latest', 'custom'
    
    # Target selection (JSON arrays)
    target_platforms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: ["platform1", "platform2"]
    target_namespaces: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: ["ns1", "ns2"] or null for all
    target_resources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: specific resource IDs or null for all
    # Phase 1 (structured field editing): typed/whitelisted manifest edits applied in the
    # same read-modify-write as the image. JSON array of edit objects (see ImageUpdateJobCreate);
    # null/empty = image-only job. TEXT-as-JSON to match this table's convention.
    field_edits: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Scheduling
    scheduled_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)  # null = immediate
    
    # Approval workflow
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_approval")
    # Status values: pending_approval, approved, executing, completed, failed, cancelled
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Phase 1: extra, explicit acknowledgment required when any target is Helm-managed
    # (a direct patch is reverted by the next helm upgrade). Separate from approval_required;
    # without it the executor refuses Helm-managed targets.
    helm_managed_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    health_check_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    health_check_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="smart_watch", server_default="smart_watch")
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Azure DevOps integration
    trigger_token: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True, index=True)
    pipeline_callback_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Execution tracking
    started_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Live progress tracking
    progress_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Total resources
    progress_current: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Current index
    progress_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Current action
    progress_updated_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Results relationship
    results: Mapped[list["ImageUpdateResult"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ImageUpdateResult(Base):
    """Results of individual resource updates within a job"""
    __tablename__ = "image_update_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("image_update_jobs.id"), nullable=False, index=True)
    
    # Resource identification
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    container_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    
    # Before/after state
    old_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    new_image: Mapped[str] = mapped_column(String(512), nullable=False)
    
    # Pre-patch state (JSON: replicas, ready_replicas, pod_statuses, etc.)
    pre_patch_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Post-patch health check state (JSON)
    post_patch_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Health check result
    health_check_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    health_check_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Auto-rollback tracking
    was_rolled_back: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Result status
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # 'success', 'failed', 'skipped', 'rolled_back'
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Detailed execution log (JSON array of log entries)
    execution_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Phase 1 (structured field editing) — all TEXT-as-JSON to match this table:
    #  pre_patch_manifest: sanitized spec+metadata snapshot captured JUST BEFORE patching;
    #    the rollback source of truth (restore re-applies this exact manifest).
    #  field_changes: applied-edits summary [{path, old, new}] for audit/UI only.
    #  helm_managed: true when the target carries meta.helm.sh/release-* (a direct patch
    #    is reverted by the next helm upgrade — surfaced as a warning, not a block).
    pre_patch_manifest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    field_changes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    helm_managed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Timestamps
    executed_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    health_checked_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    job: Mapped[ImageUpdateJob] = relationship(back_populates="results")


class ImageUpdateSnapshot(Base):
    """Pre-patch manifest snapshot captured by the backend that OWNS the resource, for
    field-edit (Update Product) patches — so restore-based rollback stays local to that
    backend and snapshots never cross the federation wire. The primary stores LOCAL
    snapshots on ImageUpdateResult.pre_patch_manifest; a remote backend stores its OWN here,
    keyed by the originating (primary) job id + resource identity. No FK to image_update_jobs
    (that row lives on the primary, not on the owning remote)."""
    __tablename__ = "image_update_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # originating primary job id
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    container_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Encrypted JSON (crypto_utils Fernet), same as ImageUpdateResult.pre_patch_manifest.
    pre_patch_manifest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageCache(Base):
    """Cache for EOL and Twistlock data to avoid duplicate API calls for same image."""
    __tablename__ = "image_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)  # Full image name with tag
    
    # EOL data cache
    eol_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: {eol, support, releaseDate, ...}
    eol_fetched_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    eol_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # Last error message if failed
    
    # Twistlock data cache
    twistlock_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: {vulnerabilities, vulnerabilityDistribution, ...}
    twistlock_fetched_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    twistlock_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Trivy data cache
    trivy_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: {vulnerabilities, vulnerabilityDistribution, ...}
    trivy_fetched_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    trivy_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Latest version data cache (for federation)
    latest_version_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: {latest_version, error}
    latest_version_fetched_at: Mapped[Optional[str]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())