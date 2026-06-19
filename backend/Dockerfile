# ---- Builder: install Python deps + Trivy CLI (build toolchain stays here) ----
# Fully-qualified registry path: Podman/Buildah (TFS pipeline) does not auto-prepend
# docker.io for short image names the way Docker does.
FROM docker.io/library/python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Build toolchain is only needed to compile any sdist-only wheels; it is never
# shipped to the runtime image. Dropping it from the final image is what
# eliminates the build-essential CVEs (perl, perl-modules-5.40, libperl5.40,
# linux-libc-dev, curl, ...) that the previous single-stage build carried.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    libpq-dev \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv so it can be copied wholesale into the runtime stage.
RUN python -m venv /opt/venv

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Install trivy CLI for vulnerability scanning (client-server mode)
# Pinned to v0.71.0 (keep in sync with the Trivy server image in values.yaml).
# Note: avoid v0.69.4-v0.69.6 — those releases were compromised; v0.71.0 is clear.
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin v0.71.0

# ---- Runtime: minimal image, no compilers / no perl toolchain / no curl ----
FROM docker.io/library/python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TRIVY_CACHE_DIR=/trivy-cache

WORKDIR /app

# Patch base-image OS packages (e.g. libgnutls30t64, perl-base) to the latest
# Debian security release. Nothing new is installed here, only upgraded.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Bring in the prebuilt virtualenv and the trivy binary from the builder stage.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /usr/local/bin/trivy /usr/local/bin/trivy

COPY backend/app /app/app

# Create non-root user and adjust permissions
RUN groupadd -g 10001 app && \
    useradd -u 10001 -g app -s /usr/sbin/nologin -m app && \
    chown -R app:app /app && \
    mkdir -p /trivy-cache && chown app:app /trivy-cache

# Drop privileges
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
