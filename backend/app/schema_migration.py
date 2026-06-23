from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .logging_utils import log_event


def ensure_schema(engine: Engine) -> None:
    """Idempotent lightweight migrations for additive changes.

    This is a safe place to add simple ALTER TABLE statements that may be
    required after model changes without introducing Alembic.
    """
    try:
        with engine.begin() as conn:
            # users.password_must_change (boolean NOT NULL DEFAULT false)
            q = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'password_must_change'
                """
            )
            exists = conn.execute(q).scalar() is not None
            if not exists:
                conn.execute(text("ALTER TABLE users ADD COLUMN password_must_change boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="users", column="password_must_change")
            # resources.product_name
            q2 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'product_name'
                """
            )
            if conn.execute(q2).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN product_name varchar(128)"))
                log_event("schema.migrate.add_column", table="resources", column="product_name")
            # resources.eol_date
            q3 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'eol_date'
                """
            )
            if conn.execute(q3).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN eol_date varchar(32)"))
                log_event("schema.migrate.add_column", table="resources", column="eol_date")
            # backend_endpoints.skip_tls_verify
            q4 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'skip_tls_verify'
                """
            )
            if conn.execute(q4).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN skip_tls_verify boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="skip_tls_verify")
            # backend_endpoints.approved (approval system)
            q5 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'approved'
                """
            )
            if conn.execute(q5).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN approved boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="approved")
            # backend_endpoints.approved_by
            q6 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'approved_by'
                """
            )
            if conn.execute(q6).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN approved_by varchar(128)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="approved_by")
            # backend_endpoints.approved_at
            q7 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'approved_at'
                """
            )
            if conn.execute(q7).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN approved_at timestamp with time zone"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="approved_at")
            # backend_endpoints.last_seen_at
            q8 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'last_seen_at'
                """
            )
            if conn.execute(q8).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN last_seen_at timestamp with time zone"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="last_seen_at")
            # backend_endpoints.auth_token
            q9 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'auth_token'
                """
            )
            if conn.execute(q9).scalar() is None:
                # Add column, generate tokens for existing rows
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN auth_token varchar(128)"))
                # Generate unique tokens for existing backends
                import secrets as sec
                from sqlalchemy import update
                existing_backends = conn.execute(text("SELECT id FROM backend_endpoints WHERE auth_token IS NULL"))
                for row in existing_backends:
                    token = sec.token_urlsafe(32)
                    conn.execute(text(f"UPDATE backend_endpoints SET auth_token = '{token}' WHERE id = {row[0]}"))
                # Make it NOT NULL after populating
                conn.execute(text("ALTER TABLE backend_endpoints ALTER COLUMN auth_token SET NOT NULL"))
                # Add unique constraint
                try:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_backend_endpoints_auth_token ON backend_endpoints(auth_token)"))
                except:
                    pass
                log_event("schema.migrate.add_column", table="backend_endpoints", column="auth_token")
            # backend_endpoints.description
            q10 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'description'
                """
            )
            if conn.execute(q10).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN description text"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="description")
            # backend_endpoints.auto_sync_schedule
            q11 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'auto_sync_schedule'
                """
            )
            if conn.execute(q11).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN auto_sync_schedule varchar(128)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="auto_sync_schedule")
            # backend_endpoints.force_sync_enabled
            q12 = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'force_sync_enabled'
                """
            )
            if conn.execute(q12).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN force_sync_enabled boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="force_sync_enabled")

            # backend_endpoints.cleanup_force_enabled
            q12b = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'cleanup_force_enabled'
                """
            )
            if conn.execute(q12b).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN cleanup_force_enabled boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="cleanup_force_enabled")

            # managed_product_list.backend_name
            q13 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'managed_product_list' AND column_name = 'backend_name'
                """
            )
            if conn.execute(q13).scalar() is None:
                conn.execute(text("ALTER TABLE managed_product_list ADD COLUMN backend_name varchar(128)"))
                # Set default backend for existing records
                conn.execute(text("UPDATE managed_product_list SET backend_name = 'default-backend' WHERE backend_name IS NULL"))
                conn.execute(text("ALTER TABLE managed_product_list ALTER COLUMN backend_name SET NOT NULL"))
                # Drop old unique constraint if exists
                conn.execute(text("ALTER TABLE managed_product_list DROP CONSTRAINT IF EXISTS managed_product_list_product_name_key"))
                # Add new composite unique constraint
                conn.execute(text("ALTER TABLE managed_product_list ADD CONSTRAINT uq_backend_product UNIQUE (backend_name, product_name)"))
                log_event("schema.migrate.add_column", table="managed_product_list", column="backend_name")
            
            # backend_endpoints.use_dynamic_discovery
            q14 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'use_dynamic_discovery'
                """
            )
            if conn.execute(q14).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN use_dynamic_discovery boolean NOT NULL DEFAULT false"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="use_dynamic_discovery")
            
            # backend_endpoints.backend_type
            q15 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'backend_type'
                """
            )
            if conn.execute(q15).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN backend_type varchar(16) NOT NULL DEFAULT 'secondary'"))
                # Set is_default backends to 'primary'
                conn.execute(text("UPDATE backend_endpoints SET backend_type = 'primary' WHERE is_default = true"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="backend_type")
            
            # backend_endpoints.latest_version_mode (per-backend latest version check mode)
            q_lvm = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'latest_version_mode'
                """
            )
            if conn.execute(q_lvm).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN latest_version_mode varchar(16)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="latest_version_mode")
            
            # backend_endpoints.twistlock_mode (per-backend Twistlock query mode)
            q_twm = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'twistlock_mode'
                """
            )
            if conn.execute(q_twm).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN twistlock_mode varchar(16)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="twistlock_mode")
            
            # backend_endpoints.trivy_mode (Trivy scan mode per backend)
            q_trvm = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'trivy_mode'
                """
            )
            if conn.execute(q_trvm).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN trivy_mode varchar(16)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="trivy_mode")
            
            # backend_endpoints.eol_mode (EOL check mode per backend)
            q_eolm = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'eol_mode'
                """
            )
            if conn.execute(q_eolm).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN eol_mode varchar(16)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="eol_mode")
            
            # sync_logs.backend_name
            q16 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'sync_logs' AND column_name = 'backend_name'
                """
            )
            if conn.execute(q16).scalar() is None:
                conn.execute(text("ALTER TABLE sync_logs ADD COLUMN backend_name varchar(128)"))
                log_event("schema.migrate.add_column", table="sync_logs", column="backend_name")
            
            # sync_logs.platform
            q17 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'sync_logs' AND column_name = 'platform'
                """
            )
            if conn.execute(q17).scalar() is None:
                conn.execute(text("ALTER TABLE sync_logs ADD COLUMN platform varchar(128)"))
                log_event("schema.migrate.add_column", table="sync_logs", column="platform")
            
            # sync_logs.duration_seconds
            q18 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'sync_logs' AND column_name = 'duration_seconds'
                """
            )
            if conn.execute(q18).scalar() is None:
                conn.execute(text("ALTER TABLE sync_logs ADD COLUMN duration_seconds integer"))
                log_event("schema.migrate.add_column", table="sync_logs", column="duration_seconds")
            
            # user_charts.filter_by
            q19 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'user_charts' AND column_name = 'filter_by'
                """
            )
            if conn.execute(q19).scalar() is None:
                conn.execute(text("ALTER TABLE user_charts ADD COLUMN filter_by varchar(64)"))
                log_event("schema.migrate.add_column", table="user_charts", column="filter_by")
            
            # user_charts.filter_value
            q20 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'user_charts' AND column_name = 'filter_value'
                """
            )
            if conn.execute(q20).scalar() is None:
                conn.execute(text("ALTER TABLE user_charts ADD COLUMN filter_value varchar(256)"))
                log_event("schema.migrate.add_column", table="user_charts", column="filter_value")
            
            # user_charts.chart_type
            q21 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'user_charts' AND column_name = 'chart_type'
                """
            )
            if conn.execute(q21).scalar() is None:
                conn.execute(text("ALTER TABLE user_charts ADD COLUMN chart_type varchar(16) NOT NULL DEFAULT 'bar'"))
                log_event("schema.migrate.add_column", table="user_charts", column="chart_type")
            
            # resources.replicas
            q22 = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'replicas'
                """
            )
            if conn.execute(q22).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN replicas integer"))
                log_event("schema.migrate.add_column", table="resources", column="replicas")
            
            # ENFORCE: Secondary backends MUST use dynamic discovery (federation requirement)
            # This ensures existing backends are corrected on every startup
            result = conn.execute(text("""
                UPDATE backend_endpoints 
                SET use_dynamic_discovery = TRUE 
                WHERE backend_type = 'secondary' AND use_dynamic_discovery = FALSE
                RETURNING name
            """))
            updated_backends = result.fetchall()
            if updated_backends:
                backend_names = [row[0] for row in updated_backends]
                log_event("schema.migrate.enforce_dynamic_discovery", 
                         backends=backend_names, 
                         reason="Secondary backends must fetch managed products from primary")
            
            # Create image_update_jobs table if not exists
            jobs_exists = conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'image_update_jobs'"
            )).scalar()
            
            if not jobs_exists:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS image_update_jobs (
                        id SERIAL PRIMARY KEY,
                        product_name VARCHAR(256) NOT NULL,
                        target_image VARCHAR(512) NOT NULL,
                        image_source VARCHAR(32) NOT NULL DEFAULT 'custom',
                        target_platforms TEXT,
                        target_namespaces TEXT,
                        target_resources TEXT,
                        scheduled_at TIMESTAMP WITH TIME ZONE,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending_approval',
                        approval_required BOOLEAN NOT NULL DEFAULT TRUE,
                        approved_by VARCHAR(128),
                        approved_at TIMESTAMP WITH TIME ZONE,
                        trigger_token VARCHAR(128) UNIQUE,
                        pipeline_callback_url VARCHAR(512),
                        started_at TIMESTAMP WITH TIME ZONE,
                        finished_at TIMESTAMP WITH TIME ZONE,
                        created_by VARCHAR(128) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        notes TEXT
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_product_name ON image_update_jobs (product_name)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_status ON image_update_jobs (status)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_jobs_trigger_token ON image_update_jobs (trigger_token)"))
                log_event("schema.migrate.create_table", table="image_update_jobs")
            
            # Create image_update_results table if not exists
            results_exists = conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'image_update_results'"
            )).scalar()
            
            if not results_exists:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS image_update_results (
                        id SERIAL PRIMARY KEY,
                        job_id INTEGER NOT NULL REFERENCES image_update_jobs(id) ON DELETE CASCADE,
                        platform VARCHAR(64) NOT NULL,
                        namespace VARCHAR(128) NOT NULL,
                        resource_name VARCHAR(256) NOT NULL,
                        kind VARCHAR(64) NOT NULL,
                        container_name VARCHAR(128),
                        old_image VARCHAR(512),
                        new_image VARCHAR(512) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        error_message TEXT,
                        executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_image_update_results_job_id ON image_update_results (job_id)"))
                log_event("schema.migrate.create_table", table="image_update_results")
            
            # resource_history.container_name (for per-container history tracking)
            q_hist_container = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'resource_history' AND column_name = 'container_name'
                """
            )
            if conn.execute(q_hist_container).scalar() is None:
                conn.execute(text("ALTER TABLE resource_history ADD COLUMN container_name varchar(256)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_resource_history_container_name ON resource_history (container_name)"))
                log_event("schema.migrate.add_column", table="resource_history", column="container_name")
            
            # resource_history.source (sync or job)
            q_hist_source = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resource_history' AND column_name = 'source'
            """)
            if conn.execute(q_hist_source).scalar() is None:
                conn.execute(text("ALTER TABLE resource_history ADD COLUMN source varchar(32)"))
                log_event("schema.migrate.add_column", table="resource_history", column="source")

            # resource_history.job_id (link to image_update_jobs)
            q_hist_job = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resource_history' AND column_name = 'job_id'
            """)
            if conn.execute(q_hist_job).scalar() is None:
                conn.execute(text("ALTER TABLE resource_history ADD COLUMN job_id integer"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_resource_history_job_id ON resource_history (job_id)"))
                log_event("schema.migrate.add_column", table="resource_history", column="job_id")

            # resources.watch_spec_hash (real-time watch dedup across restarts)
            q_watch_hash = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'watch_spec_hash'
            """)
            if conn.execute(q_watch_hash).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN watch_spec_hash varchar(64)"))
                log_event("schema.migrate.add_column", table="resources", column="watch_spec_hash")

            # resources.sec_summary (precomputed compact summary for the fast slim listing)
            q_sec_summary = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'sec_summary'
            """)
            if conn.execute(q_sec_summary).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN sec_summary jsonb"))
                log_event("schema.migrate.add_column", table="resources", column="sec_summary")

            # resources Helm-status columns (Phase 1A: drift of live vs Helm-rendered manifest)
            q_helm_status = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'helm_status'
            """)
            if conn.execute(q_helm_status).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN helm_status varchar(16)"))
                # Backfill 'unknown' (= not yet computed) so the grid doesn't show a
                # misleading 'None' before the first refresh determines Helm ownership.
                conn.execute(text("UPDATE resources SET helm_status = 'unknown' WHERE helm_status IS NULL"))
                log_event("schema.migrate.add_column", table="resources", column="helm_status")

            q_helm_release_name = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'helm_release_name'
            """)
            if conn.execute(q_helm_release_name).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN helm_release_name varchar(256)"))
                log_event("schema.migrate.add_column", table="resources", column="helm_release_name")

            q_helm_release_ns = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'helm_release_namespace'
            """)
            if conn.execute(q_helm_release_ns).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN helm_release_namespace varchar(128)"))
                log_event("schema.migrate.add_column", table="resources", column="helm_release_namespace")

            q_helm_revision = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'helm_release_revision'
            """)
            if conn.execute(q_helm_revision).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN helm_release_revision integer"))
                log_event("schema.migrate.add_column", table="resources", column="helm_release_revision")

            q_helm_drift = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'helm_drift_detail'
            """)
            if conn.execute(q_helm_drift).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN helm_drift_detail jsonb"))
                log_event("schema.migrate.add_column", table="resources", column="helm_drift_detail")

            # resources LLM-derived support status (shown when no public EOL data exists)
            q_eol_support_status = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'eol_support_status'
            """)
            if conn.execute(q_eol_support_status).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN eol_support_status varchar(16)"))
                log_event("schema.migrate.add_column", table="resources", column="eol_support_status")

            q_eol_support_note = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'eol_support_note'
            """)
            if conn.execute(q_eol_support_note).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN eol_support_note varchar(512)"))
                log_event("schema.migrate.add_column", table="resources", column="eol_support_note")

            # resources.eol_support_at: timestamp of the LLM EOL status, for TTL expiry.
            q_eol_support_at = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'eol_support_at'
            """)
            if conn.execute(q_eol_support_at).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN eol_support_at timestamptz"))
                log_event("schema.migrate.add_column", table="resources", column="eol_support_at")

            # Composite index for the per-resource history count (slim listing) and the
            # on-demand history fetch — both filter by resource_id and order by checked_at.
            q_hist_composite = text("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'resource_history' AND indexname = 'ix_resource_history_resource_checked'
            """)
            if conn.execute(q_hist_composite).scalar() is None:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_resource_history_resource_checked "
                    "ON resource_history (resource_id, checked_at DESC)"
                ))
                log_event("schema.migrate.create_index", table="resource_history",
                          index="ix_resource_history_resource_checked")

            # image_update_jobs.source_image (filter to only update containers matching this image repo)
            q_source_image = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'source_image'
                """
            )
            if conn.execute(q_source_image).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN source_image varchar(512)"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="source_image")
            
            # Live progress tracking fields for image_update_jobs
            progress_columns = [
                ("progress_total", "INTEGER"),
                ("progress_current", "INTEGER"),
                ("progress_message", "VARCHAR(512)"),
                ("cancel_requested", "BOOLEAN DEFAULT FALSE"),
            ]
            
            for col_name, col_type in progress_columns:
                q_col = text(f"""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'image_update_jobs' AND column_name = '{col_name}'
                """)
                if conn.execute(q_col).scalar() is None:
                    conn.execute(text(f"ALTER TABLE image_update_jobs ADD COLUMN {col_name} {col_type}"))
                    log_event("schema.migrate.add_column", table="image_update_jobs", column=col_name)
            
            # Health check fields for image_update_results
            health_check_columns = [
                ("pre_patch_state", "TEXT"),
                ("post_patch_state", "TEXT"),
                ("health_check_passed", "BOOLEAN"),
                ("health_check_message", "TEXT"),
                ("health_checked_at", "TIMESTAMP WITH TIME ZONE"),
                ("was_rolled_back", "BOOLEAN DEFAULT FALSE"),
                ("rollback_reason", "TEXT"),
                ("execution_log", "TEXT"),
            ]
            
            for col_name, col_type in health_check_columns:
                q_col = text(f"""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'image_update_results' AND column_name = '{col_name}'
                """)
                if conn.execute(q_col).scalar() is None:
                    conn.execute(text(f"ALTER TABLE image_update_results ADD COLUMN {col_name} {col_type}"))
                    log_event("schema.migrate.add_column", table="image_update_results", column=col_name)

            # Phase 1 (structured field editing): job-level edit list (TEXT-as-JSON).
            q_field_edits = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'field_edits'
            """)
            if conn.execute(q_field_edits).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN field_edits TEXT"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="field_edits")

            # Phase 1: extra Helm-managed acknowledgment flag (separate from approval).
            q_helm_ack = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'helm_managed_ack'
            """)
            if conn.execute(q_helm_ack).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN helm_managed_ack BOOLEAN DEFAULT FALSE"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="helm_managed_ack")

            # Phase 1 per-result columns: rollback snapshot + applied-edits audit + helm flag.
            for col_name, col_type in [
                ("pre_patch_manifest", "TEXT"),
                ("field_changes", "TEXT"),
                ("helm_managed", "BOOLEAN"),
            ]:
                q_col = text(f"""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'image_update_results' AND column_name = '{col_name}'
                """)
                if conn.execute(q_col).scalar() is None:
                    conn.execute(text(f"ALTER TABLE image_update_results ADD COLUMN {col_name} {col_type}"))
                    log_event("schema.migrate.add_column", table="image_update_results", column=col_name)

            # Phase 1.4: per-backend pre-patch snapshot store for field-edit rollback. Each
            # backend keeps its OWN resources' snapshots locally (never crosses the wire);
            # keyed by the originating primary job id + resource identity.
            snap_exists = conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'image_update_snapshots'"
            )).scalar()
            if not snap_exists:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS image_update_snapshots (
                        id SERIAL PRIMARY KEY,
                        job_id INTEGER NOT NULL,
                        platform VARCHAR(64) NOT NULL,
                        namespace VARCHAR(128) NOT NULL,
                        resource_name VARCHAR(256) NOT NULL,
                        kind VARCHAR(64) NOT NULL,
                        container_name VARCHAR(128),
                        pre_patch_manifest TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_image_update_snapshots_job "
                    "ON image_update_snapshots (job_id)"
                ))
                log_event("schema.migrate.create_table", table="image_update_snapshots")

            # Create image_cache table if not exists (for EOL and Twistlock caching)
            cache_exists = conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'image_cache'"
            )).scalar()
            
            if not cache_exists:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS image_cache (
                        id SERIAL PRIMARY KEY,
                        image VARCHAR(512) NOT NULL UNIQUE,
                        eol_data TEXT,
                        eol_fetched_at TIMESTAMP WITH TIME ZONE,
                        eol_error VARCHAR(512),
                        twistlock_data TEXT,
                        twistlock_fetched_at TIMESTAMP WITH TIME ZONE,
                        twistlock_error VARCHAR(512),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_image_cache_image ON image_cache (image)"))
                log_event("schema.migrate.create_table", table="image_cache")
            
            # Add latest_version columns to image_cache if they don't exist
            for col_name, col_type in [
                ("latest_version_data", "TEXT"),
                ("latest_version_fetched_at", "TIMESTAMP WITH TIME ZONE"),
            ]:
                q_col = text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'image_cache' AND column_name = :col
                """)
                if conn.execute(q_col, {"col": col_name}).scalar() is None:
                    conn.execute(text(f"ALTER TABLE image_cache ADD COLUMN {col_name} {col_type}"))
                    log_event("schema.migrate.add_column", table="image_cache", column=col_name)
            
            # Add trivy columns to image_cache if they don't exist
            for col_name, col_type in [
                ("trivy_data", "TEXT"),
                ("trivy_fetched_at", "TIMESTAMP WITH TIME ZONE"),
                ("trivy_error", "VARCHAR(512)"),
            ]:
                q_col = text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'image_cache' AND column_name = :col
                """)
                if conn.execute(q_col, {"col": col_name}).scalar() is None:
                    conn.execute(text(f"ALTER TABLE image_cache ADD COLUMN {col_name} {col_type}"))
                    log_event("schema.migrate.add_column", table="image_cache", column=col_name)
            
            # resources.manifest_yaml (cached YAML for comparison)
            q_manifest = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'manifest_yaml'
                """
            )
            if conn.execute(q_manifest).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN manifest_yaml JSONB"))
                log_event("schema.migrate.add_column", table="resources", column="manifest_yaml")
            
            # resources.manifest_updated_at
            q_manifest_at = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'manifest_updated_at'
                """
            )
            if conn.execute(q_manifest_at).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN manifest_updated_at TIMESTAMP WITH TIME ZONE"))
                log_event("schema.migrate.add_column", table="resources", column="manifest_updated_at")
            
            # resources.related_objects (for PDB, HPA, Service, ConfigMap, Secret, Route)
            q_related = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'resources' AND column_name = 'related_objects'
                """
            )
            if conn.execute(q_related).scalar() is None:
                conn.execute(text("ALTER TABLE resources ADD COLUMN related_objects JSONB"))
                log_event("schema.migrate.add_column", table="resources", column="related_objects")
            
            # comparison_reports table for saved comparison reports
            q_comp_reports = text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'comparison_reports'
                """
            )
            if conn.execute(q_comp_reports).scalar() is None:
                conn.execute(text("""
                    CREATE TABLE comparison_reports (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(256) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        created_by VARCHAR(128),
                        cluster1 VARCHAR(128) NOT NULL,
                        cluster2 VARCHAR(128) NOT NULL,
                        cluster1_namespaces TEXT,
                        cluster2_namespaces TEXT,
                        cluster1_kinds TEXT,
                        cluster2_kinds TEXT,
                        common_count INTEGER NOT NULL DEFAULT 0,
                        only_in_cluster1_count INTEGER NOT NULL DEFAULT 0,
                        only_in_cluster2_count INTEGER NOT NULL DEFAULT 0,
                        with_differences_count INTEGER NOT NULL DEFAULT 0,
                        total_diffs_count INTEGER NOT NULL DEFAULT 0,
                        comparison_data JSONB
                    )
                """))
                conn.execute(text("CREATE INDEX idx_comparison_reports_created_at ON comparison_reports(created_at)"))
                log_event("schema.migrate.create_table", table="comparison_reports")
            
            # users.theme (user's preferred theme)
            q_user_theme = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'theme'
                """
            )
            if conn.execute(q_user_theme).scalar() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN theme VARCHAR(32) DEFAULT 'dark'"))
                log_event("schema.migrate.add_column", table="users", column="theme")
            
            # users.ldap_user (whether user is from LDAP)
            q_ldap_user = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'ldap_user'
                """
            )
            if conn.execute(q_ldap_user).scalar() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN ldap_user BOOLEAN NOT NULL DEFAULT FALSE"))
                log_event("schema.migrate.add_column", table="users", column="ldap_user")
            
            # Create ldap_config table if not exists
            q_ldap_config = text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'ldap_config'
                """
            )
            if conn.execute(q_ldap_config).scalar() is None:
                conn.execute(text("""
                    CREATE TABLE ldap_config (
                        id SERIAL PRIMARY KEY,
                        enabled BOOLEAN NOT NULL DEFAULT FALSE,
                        server_url VARCHAR(512),
                        port INTEGER NOT NULL DEFAULT 636,
                        use_ssl BOOLEAN NOT NULL DEFAULT TRUE,
                        ca_cert TEXT,
                        skip_cert_verify BOOLEAN NOT NULL DEFAULT FALSE,
                        bind_dn VARCHAR(512),
                        bind_password VARCHAR(512),
                        base_dn VARCHAR(512),
                        user_search_filter VARCHAR(512),
                        user_search_base VARCHAR(512),
                        username_attribute VARCHAR(64) NOT NULL DEFAULT 'sAMAccountName',
                        email_attribute VARCHAR(64) NOT NULL DEFAULT 'mail',
                        display_name_attribute VARCHAR(64) NOT NULL DEFAULT 'displayName',
                        group_search_base VARCHAR(512),
                        group_search_filter VARCHAR(512),
                        admin_group_dn VARCHAR(512),
                        analyst_group_dn VARCHAR(512),
                        readonly_group_dn VARCHAR(512),
                        updated_at TIMESTAMP WITH TIME ZONE
                    )
                """))
                log_event("schema.migrate.create_table", table="ldap_config")
            
            # Add ca_cert column to ldap_config if not exists
            q_ldap_ca = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ldap_config' AND column_name = 'ca_cert'
                """
            )
            if conn.execute(q_ldap_ca).scalar() is None:
                conn.execute(text("ALTER TABLE ldap_config ADD COLUMN ca_cert TEXT"))
                log_event("schema.migrate.add_column", table="ldap_config", column="ca_cert")
            
            # Add skip_cert_verify column to ldap_config if not exists
            q_ldap_skip = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ldap_config' AND column_name = 'skip_cert_verify'
                """
            )
            if conn.execute(q_ldap_skip).scalar() is None:
                conn.execute(text("ALTER TABLE ldap_config ADD COLUMN skip_cert_verify BOOLEAN NOT NULL DEFAULT FALSE"))
                log_event("schema.migrate.add_column", table="ldap_config", column="skip_cert_verify")
            
            # backend_endpoints.ldap_mode (LDAP auth mode: 'primary' or 'local')
            q_ldap_mode = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'ldap_mode'
                """
            )
            if conn.execute(q_ldap_mode).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN ldap_mode VARCHAR(16) DEFAULT 'primary'"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="ldap_mode")

            # backend_endpoints.llm_mode
            q_llm_mode = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'backend_endpoints' AND column_name = 'llm_mode'
                """
            )
            if conn.execute(q_llm_mode).scalar() is None:
                conn.execute(text("ALTER TABLE backend_endpoints ADD COLUMN llm_mode VARCHAR(16)"))
                log_event("schema.migrate.add_column", table="backend_endpoints", column="llm_mode")

            # users.font_size
            q_font = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'font_size'
                """
            )
            if conn.execute(q_font).scalar() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN font_size VARCHAR(16) DEFAULT 'medium'"))
                log_event("schema.migrate.add_column", table="users", column="font_size")

            # image_update_jobs.health_check_enabled
            q_hc = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'health_check_enabled'
                """
            )
            if conn.execute(q_hc).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN health_check_enabled BOOLEAN NOT NULL DEFAULT true"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="health_check_enabled")

            # image_update_jobs.health_check_mode
            q_hcm = text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'health_check_mode'
                """
            )
            if conn.execute(q_hcm).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN health_check_mode VARCHAR(20) NOT NULL DEFAULT 'smart_watch'"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="health_check_mode")

            # Migrate security_info from json to jsonb for faster extraction
            for tbl in ("resources", "resource_history"):
                q_type = text(
                    f"""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = '{tbl}' AND column_name = 'security_info'
                    """
                )
                dtype = conn.execute(q_type).scalar()
                if dtype and dtype.lower() == "json":
                    conn.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN security_info TYPE jsonb USING security_info::jsonb"))
                    log_event("schema.migrate.json_to_jsonb", table=tbl, column="security_info")

            # image_update_jobs.progress_updated_at
            q_pua = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'progress_updated_at'
                """
            )
            if conn.execute(q_pua).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN progress_updated_at TIMESTAMPTZ"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="progress_updated_at")

            # image_update_jobs.cancelled_at
            q_cancelled_at = text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'image_update_jobs' AND column_name = 'cancelled_at'
            """)
            if conn.execute(q_cancelled_at).scalar() is None:
                conn.execute(text("ALTER TABLE image_update_jobs ADD COLUMN cancelled_at TIMESTAMPTZ"))
                log_event("schema.migrate.add_column", table="image_update_jobs", column="cancelled_at")

            # managed_product_list.image_pattern (custom image matching override)
            q_img_pat = text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'managed_product_list' AND column_name = 'image_pattern'
                """
            )
            if conn.execute(q_img_pat).scalar() is None:
                conn.execute(text("ALTER TABLE managed_product_list ADD COLUMN image_pattern varchar(512)"))
                log_event("schema.migrate.add_column", table="managed_product_list", column="image_pattern")

    except Exception as e:
        # Do not crash startup; log for visibility
        log_event("schema.migrate.error", error=str(e))


