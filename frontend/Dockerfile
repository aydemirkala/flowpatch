# Build stage
# Fully-qualified registry path: Podman/Buildah (TFS pipeline) does not auto-prepend
# docker.io for short image names the way Docker does.
FROM docker.io/library/node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./
RUN npm ci || npm install
COPY frontend .
RUN npm run build

# Serve stage
FROM docker.io/nginxinc/nginx-unprivileged:1.29-alpine

# Fix CVE-2025-48174: Upgrade libavif and other system packages
USER root
RUN apk upgrade --no-cache && \
    apk add --no-cache libavif>=1.3.0 || \
    apk upgrade --no-cache libavif
# Fix CVE-2026-31789 (CRITICAL) / CVE-2026-45447 / CVE-2026-2838x (HIGH):
# ensure patched OpenSSL (libcrypto3/libssl3). Fails the build loudly if a
# secure version is unavailable, instead of silently shipping vulnerable libs.
RUN apk add --no-cache "libcrypto3>=3.5.7" "libssl3>=3.5.7"
# CVE-2026-6732 (HIGH, libxml2 XSD-validation DoS): pin the patched libxml2.
# It is pulled in by nginx's xslt/njs modules so it cannot be removed; pin it
# explicitly (rather than rely on the apk upgrade above, which the CI builder
# does not reliably apply) so the build fails loudly if the fix is unavailable.
RUN apk add --no-cache "libxml2>=2.13.9-r1"
# CVE-2026-45186 (HIGH, expat XML parser — pulled in via fontconfig): pin the
# patched expat. Same rationale as libxml2 above; the CI builder's apk upgrade
# does not reliably apply it, so pin explicitly (fails loudly if unavailable).
RUN apk add --no-cache "expat>=2.8.1-r0"
# CVE-2026-5773 / CVE-2026-6276 (HIGH, libcurl): the nginx base image ships
# curl + libcurl, but this static-serving frontend never uses them and nothing
# else depends on them. Alpine has no fixed curl yet (latest is 8.19.0-r0), so
# remove the package outright — this clears both CVEs and also drops the
# now-orphaned libidn2/libunistring, shrinking the attack surface.
RUN apk del curl libcurl
USER nginx

COPY --from=build /app/dist /usr/share/nginx/html
# Provide configs suitable for running unprivileged and SPA routing
COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY frontend/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080

