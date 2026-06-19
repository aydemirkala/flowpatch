import os
from dataclasses import dataclass, field
from typing import Optional


def _default_allow_origins() -> list[str]:
    value = os.getenv("CORS_ALLOW_ORIGINS")
    if not value:
        return ["*"]
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/patchdb",
    )
    # Default to repository root sources.txt if not provided via env
    sources_file: str = os.getenv(
        "SOURCES_FILE",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sources.txt")),
    )
    kube_incluster: bool = os.getenv("KUBE_INCLUSTER", "true").lower() == "true"
    kubecfg_path: Optional[str] = os.getenv("KUBECONFIG")
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
    allow_origins: list[str] = field(default_factory=_default_allow_origins)
    # Background refresh interval in seconds; 0 disables auto-refresh
    refresh_interval_seconds: int = int(os.getenv("REFRESH_INTERVAL_SECONDS", "86400"))
    # Log directory for JSONL logs
    log_dir: str = os.getenv("LOG_DIR", "/tmp")
    log_max_size_mb: int = int(os.getenv("LOG_MAX_SIZE_MB", "100"))
    log_backup_count: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    # Twistlock (Prisma Cloud) integration
    twistlock_url: Optional[str] = os.getenv("TWISTLOCK_URL")
    twistlock_user: Optional[str] = os.getenv("TWISTLOCK_USER")
    twistlock_user_password: Optional[str] = os.getenv("TWISTLOCK_USER_PASSWORD")
    twistlock_verify: bool = os.getenv("TWISTLOCK_VERIFY", "true").lower() == "true"

    # Deployment-specific registry handling (optional).
    # If your private registry mirrors/proxies Docker Hub under a path prefix, set the
    # registry host here so version lookups resolve against docker.io. Empty = disabled.
    private_registry_host: str = os.getenv("PRIVATE_REGISTRY_HOST", "")
    # Repo path prefixes under that registry that mirror Docker Hub (comma-separated).
    registry_dockerhub_prefixes: list[str] = field(
        default_factory=lambda: [
            p.strip() for p in os.getenv("REGISTRY_DOCKERHUB_PREFIXES", "docker-proxy/").split(",")
            if p.strip()
        ]
    )
    # Extra annotation key prefixes to strip from cached manifests for Compare
    # (vendor-specific), in addition to the built-in standard ones. Comma-separated; empty = none.
    manifest_strip_annotation_prefixes: list[str] = field(
        default_factory=lambda: [
            p.strip() for p in os.getenv("MANIFEST_STRIP_ANNOTATION_PREFIXES", "").split(",")
            if p.strip()
        ]
    )


settings = Settings()
