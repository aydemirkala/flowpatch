# Installation & Configuration

How to run the **K8s Third-Party Version Tracker** locally and deploy it to Kubernetes/OpenShift,
including multi-cluster federation and optional integrations.

> See [ARCHITECTURE.md](ARCHITECTURE.md) for how the system is designed.

---

## 1. Prerequisites

- Kubernetes or OpenShift cluster(s) with admin rights to create RBAC.
- `kubectl` (or `oc`) and **Helm 3**.
- A **PostgreSQL** database per backend (the chart can deploy one, or use an external instance).
- Container registry to host the backend/frontend images.
- (Optional) Trivy, Twistlock/Prisma Cloud, SMTP server, LDAP — for the matching integrations.

---

## 2. Build & publish the container images

The chart does not ship images — build them from this repo and push to **your** registry, then point
the chart at them (section 3).

```bash
# from the repo root; replace registry.example.com/yourorg with YOUR registry/namespace
docker build -t registry.example.com/yourorg/patchmgmt-api:v1 -f backend/Dockerfile .
docker build -t registry.example.com/yourorg/patchmgmt-frontend:v1 -f frontend/Dockerfile .
docker push registry.example.com/yourorg/patchmgmt-api:v1
docker push registry.example.com/yourorg/patchmgmt-frontend:v1
```

---

## 3. Configure for your environment — what to change

Don't edit `values.yaml` in place. **Copy an example and edit your copy** (keep secrets out of git):

```bash
cp charts/patchmgmt/values-examples/values-primary-backend.yaml my-primary.yaml
# edit my-primary.yaml per the table below, then deploy with -f my-primary.yaml (section 5)
```

All placeholders below use `example.com` / `yourorg` / `CHANGE_ME` and must be replaced. Secrets
(JWT, DB password, tokens, Twistlock/SMTP passwords) are rendered into Kubernetes Secrets by the chart
from these values — so set them in your (un-committed) values file; you don't create Secrets by hand.

### Required (every deployment)

| What | Where (file → key) | Set to |
|---|---|---|
| API image | your values → `api.image` | `registry.example.com/yourorg/patchmgmt-api:v1` → **your** image (section 2) |
| Frontend image | your values → `frontend.image` | **your** frontend image |
| Namespace | your values → top-level `namespace` (and `-n` on the helm command) | your target namespace |
| JWT secret | your values → `api.env.jwtSecret` | output of `openssl rand -base64 32` (min 32 chars) |
| Database | **external:** `api.env.databaseUrl` (`…://user:CHANGE_ME@host:5432/db`) — **or built-in:** `postgresql.enabled: true` + `postgresql.auth.password` | your DB DSN / password |
| Backend identity | `api.env.backendName`, `api.env.backendPlatform` | a unique name + a cluster label shown in the UI |
| Backend URL | `api.env.backendUrl` | this backend's externally reachable URL (your Route/Ingress host) |
| Route | `route.enabled` (+ `route.tls`) / your Ingress | enable + your host/TLS |

### Remote (secondary) backends only — federation

| What | Where → key | Set to |
|---|---|---|
| Type | `api.env.backendType` / `api.env.backendIsDefault` | `secondary` / `"false"` |
| Primary URL | `api.env.primaryBackendUrl` | the primary backend's reachable URL |
| Registration token | `api.env.registrationToken` | the token generated in the primary's Admin UI (section 7) |

### Optional integrations (set only what you use)

| Integration | Where → key |
|---|---|
| Trivy (CVE scan) | `trivy.enabled: true` (+ `trivy.image`) or `api.env.trivyUrl` |
| Twistlock/Prisma | `api.env.twistlockUrl` / `twistlockUser` / `twistlockUserPassword` |
| SMTP reports | `api.env.smtpServer` / `smtpPort` / `smtpFromEmail` / `smtpUsername` / `smtpPassword` / `smtpSchedule` |
| EOL source | `api.env.eolApiUrl` (default `https://endoflife.date`) |
| Private registry mirror | `api.env.privateRegistryHost` (+ `registryDockerhubPrefixes`) — see §8 |
| Strip vendor annotations | `api.env.manifestStripAnnotationPrefixes` (e.g. `yourcorp.com/`) |
| Fluentbit → Elasticsearch | `api.fluentbit.enabled` + `…config.conf` (`Host`, `HTTP_User`, `HTTP_Passwd`) |
| LLM advice (OpenAI-compatible) | configured at runtime in the **Admin UI** (base URL, model, api key, prompts) |
| LDAP | configured at runtime in the **Admin UI** (not values) |

> The example install script [install.sh](../install.sh) is a template — replace `<your-username>`,
> the cluster API URLs (`api.example.com`), and namespaces before using it.

The full list of every backend env var ↔ Helm key is in section **11**.

---

## 4. Local development

```bash
# 1) PostgreSQL (any local instance works)
docker run -d --name patchdb -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16

# 2) Backend (FastAPI on :8000)
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/patchdb"
export KUBE_INCLUSTER=false          # use your local kubeconfig
export JWT_SECRET="$(openssl rand -base64 32)"
export BACKEND_IS_DEFAULT=true       # this dev backend is the primary
./run_dev.sh                         # uvicorn app.main:app --reload --port 8000

# 3) Frontend (Vite dev server)
cd ../frontend
npm install
npm run dev
```

The backend exposes `GET /healthz`. The schema is created/migrated automatically on startup
(`ensure_schema`), so no manual migration step is needed.

---

## 5. Deploy a single (primary) backend with Helm

After building images (§2) and editing your values copy (§3):

```bash
helm upgrade --install patchmgmt ./charts/patchmgmt \
  -f my-primary.yaml \
  -n <your-namespace> --create-namespace
```

(The bundled `charts/patchmgmt/values-examples/values-primary-backend.yaml` is a ready starting point
to copy.) **Verify:** the pod becomes Ready and `GET /healthz` returns ok.

**First-run admin.** There are no default credentials. On first open the UI shows a setup screen to
create the initial admin user (backed by `GET /api/auth/setup-state` → `POST /api/auth/setup-admin`,
which only works while no admin exists). Create it, then log in and add more users/backends from the
Admin page.

---

## 6. OpenShift

Helm works on OpenShift directly (see above). Plain manifests are also provided in
[deploy/openshift/](../deploy/openshift/):

- `deployment.yaml` — api + frontend (set your image refs).
- `rbac.yaml` — ServiceAccount + (Cluster)Role. Grants `get`/`list`/**`watch`** on
  `deployments`/`statefulsets`/`daemonsets` (the `watch` verb is required for real-time detection).
- `route.yaml` — external Route (set your host).

---

## 7. Multi-backend / federation

Each additional cluster runs its own backend with its own database, federated to the primary.

1. **Create the backend entry on the primary (Admin UI).** Add a backend (name, platform, API URL).
   The UI generates a unique **registration token**. Copy it.
2. **Deploy the remote backend.** Copy `values-examples/values-remote-backend.yaml`, edit the §3
   "Required" + "Remote" items, then:

   ```bash
   helm upgrade --install patchmgmt ./charts/patchmgmt \
     -f my-remote.yaml \
     -n <your-namespace> --create-namespace
   ```

3. **Self-registration.** On startup the remote calls the primary's `POST /api/federation/register`
   with its token and is approved automatically (logs: `federation.self_register.success`). It then
   appears in the Admin UI and the frontend includes it in resource queries and the sync modal.

**Notes**
- The frontend only talks to the **primary**; remote data is fetched through the primary's
  `/api/proxy/{backend_name}/...` reverse-proxy.
- Per-backend `*_mode` flags (in the Admin UI) decide whether latest-version/Twistlock/Trivy/EOL/LLM/
  LDAP are resolved locally or delegated to the primary.

---

## 8. Optional integrations

Configurable via `api.env.*` (or the Admin UI for the shared ones on the primary).

- **Trivy** (container CVE scanning). Enable the bundled server in values (`trivy.enabled: true`,
  image pinned to `ghcr.io/aquasecurity/trivy:0.71.0`) or point `api.env.trivyUrl` at an external Trivy
  server. The backend image bundles a matching Trivy CLI (v0.71.0). Keep client and server versions
  aligned.
- **Twistlock / Prisma Cloud.** Set `twistlockUrl`, `twistlockUser`, `twistlockUserPassword`
  (password → Secret). `twistlockVerify: false` to skip TLS verification.
- **SMTP / email reports.** Set `smtpServer`, `smtpPort`, `smtpFromEmail`, `smtpUsername`,
  `smtpPassword`, `smtpUseTls`, and `smtpSchedule` (cron) for scheduled reports. Recipients are managed
  in the Admin UI.
- **EOL data.** `eolApiUrl` (default `https://endoflife.date`).
- **LLM advice (optional, disabled by default).** Generates natural-language security & upgrade advice
  via any **OpenAI-compatible** API. Configure it in the **Admin UI** (not values): enable it and set
  `base_url`, `model`, `api_key` (stored encrypted), `max_tokens`, `temperature`. The system prompts
  ship with English defaults and can be edited in the Admin UI (`llm_prompt_security` /
  `llm_prompt_upgrade`); secondaries inherit config + prompts from the primary (or set `llm_mode`).
- **Private registry that mirrors Docker Hub.** If your registry proxies Docker Hub under a path
  prefix, set `privateRegistryHost` (e.g. `harbor.yourcorp.com`) and `registryDockerhubPrefixes`
  (e.g. `docker-proxy/`) so version lookups resolve against docker.io. Empty host = disabled.
- **Strip vendor annotations in Compare.** `manifestStripAnnotationPrefixes` (e.g. `yourcorp.com/`)
  removes your vendor-specific annotation keys from cached manifests so cluster diffs stay clean.
- **LDAP.** Configured on the primary (Admin UI); remotes can delegate auth via `ldap_mode`.
- **Skip-TLS (dev only).** `primaryBackendVerifyTls: false`, `twistlockVerify: false`, and per-backend
  `skip_tls_verify` let you run against self-signed certs. **Do not use in production.**

---

## 9. Real-time watch configuration

| Env / value | Default | Meaning |
|---|---|---|
| `ENABLE_KUBE_WATCH` / `api.env.enableKubeWatch` | `true` | Turn the real-time Kubernetes watch on/off. |
| `KUBE_WATCH_NAMESPACE` / `api.env.kubeWatchNamespace` | `""` (all) | Limit the watch to one namespace. |
| `KUBE_WATCH_WORKERS` | `2` | Worker pool size that processes watch-triggered refreshes. |

The watch only covers managed products in the backend's own cluster; the nightly cron remains the
completeness backstop. Requires the `watch` RBAC verb (already in the chart's `rbac.yaml`).

---

## 10. Upgrades

Schema changes are applied automatically and idempotently on startup (`ensure_schema`) — new columns
such as `sec_summary` and `watch_spec_hash` are added in place, **without data loss**. To upgrade:

```bash
helm upgrade patchmgmt ./charts/patchmgmt -f my-primary.yaml -n <your-namespace>
```

Roll out the primary and remotes the same way (order does not matter; each migrates its own DB). A
backend restart never re-scans everything — the watch seeds its dedup state from the DB.

---

## 11. Backend environment variable reference

Backend env vars (read by [config.py](../backend/app/config.py) and
[api-deployment.yaml](../charts/patchmgmt/templates/api-deployment.yaml)) and their Helm value keys.
Values keys are **camelCase** under `api.env.*`; env vars are **UPPER_SNAKE_CASE**.

| Env var | Helm value (`api.env.`) | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | `databaseUrl` | — | PostgreSQL DSN (`postgresql+psycopg2://…`). **Required.** |
| `JWT_SECRET` | `jwtSecret` | — | JWT signing secret (≥32 chars). **Required.** |
| `KUBE_INCLUSTER` | `kubeInCluster` | `true` | Use in-cluster K8s API (`false` → use `KUBECONFIG`). |
| `KUBECONFIG` | — | — | Kubeconfig path when not in-cluster. |
| `SOURCES_FILE` | `sourcesFile` | `/app/sources.txt` | Static source list (when not using dynamic discovery). |
| `REFRESH_INTERVAL_SECONDS` | `refreshIntervalSeconds` | `86400` | Auto-sync interval (0 disables; cron expr also supported). |
| `LOG_DIR` | `logDir` | `/tmp` | JSONL log directory. |
| `ENABLE_KUBE_WATCH` | `enableKubeWatch` | `false`* | Real-time Kubernetes watch. (*chart default `true`.) |
| `KUBE_WATCH_NAMESPACE` | `kubeWatchNamespace` | `""` | Restrict watch to one namespace. |
| `KUBE_WATCH_WORKERS` | — | `2` | Watch refresh worker pool size. |
| `BACKEND_NAME` | `backendName` | `default-backend` | Unique backend identifier. |
| `BACKEND_PLATFORM` | `backendPlatform` | `default-platform` | Cluster/platform label shown in the UI. |
| `BACKEND_URL` | `backendUrl` | `http://localhost:8000` | This backend's reachable API URL. |
| `BACKEND_IS_DEFAULT` | `backendIsDefault` | `true` | `true` → auto-approved primary. |
| `BACKEND_TYPE` | `backendType` | `primary` | `primary` or `secondary`. |
| `BACKEND_AUTH_TOKEN` | `backendAuthToken` / `registrationToken` | — | Federation token (remote backends). |
| `PRIMARY_BACKEND_URL` | `primaryBackendUrl` | — | Primary's URL (required for secondary). |
| `PRIMARY_BACKEND_VERIFY_TLS` | `primaryBackendVerifyTls` | `false` | Verify TLS when calling the primary. |
| `BACKEND_SKIP_TLS_VERIFY` | `skipTlsVerify` | `false` | Skip TLS when the primary proxies to this backend. |
| `TWISTLOCK_URL` | `twistlockUrl` | — | Twistlock/Prisma base URL. |
| `TWISTLOCK_USER` | `twistlockUser` | — | Twistlock user. |
| `TWISTLOCK_USER_PASSWORD` | `twistlockUserPassword` | — | Twistlock password (→ Secret). |
| `TWISTLOCK_VERIFY` | `twistlockVerify` | `true` | Verify TLS when calling Twistlock. |
| `TRIVY_URL` | `trivyUrl` / bundled | — | Trivy server URL. |
| `EOL_API_URL` | `eolApiUrl` | `https://endoflife.date` | EOL data source. |
| `SMTP_SERVER` / `SMTP_PORT` / `SMTP_FROM_EMAIL` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_USE_TLS` / `SMTP_SCHEDULE` | `smtp*` | — | Email reports (password → Secret; `smtpSchedule` is a cron expr). |
| `SECURITY_RED_*` / `SECURITY_ORANGE_*` / `SECURITY_GREEN_*` | `security*` | see values | Security badge thresholds. |
| `LATEST_VERSION_CHECK_MODE` / `LATEST_VERSION_CACHE_HOURS` | `latestVersion*` | `local` / `12` | Latest-version resolution + cache. |
| `PRIVATE_REGISTRY_HOST` | `privateRegistryHost` | `""` | Private registry whose Docker-Hub proxy paths resolve to docker.io (empty = off). |
| `REGISTRY_DOCKERHUB_PREFIXES` | `registryDockerhubPrefixes` | `docker-proxy/` | Comma-separated repo prefixes under that registry that mirror Docker Hub. |
| `MANIFEST_STRIP_ANNOTATION_PREFIXES` | `manifestStripAnnotationPrefixes` | `""` | Extra annotation key prefixes to strip from cached manifests in Compare. |
| `CORS_ALLOW_ORIGINS` | — | `*` | Allowed CORS origins (comma-separated). |

---

## 12. Troubleshooting

- **Remote backend not showing up.** Check the remote's logs for `federation.self_register.*`; verify
  `registrationToken` matches the Admin UI, and `primaryBackendUrl` is reachable. With self-signed
  certs set `primaryBackendVerifyTls: false`.
- **Grid lists nothing / proxy 502.** Confirm the backend is `enabled` + `approved` in the Admin UI and
  its `backendUrl` is reachable from the primary.
- **No real-time updates.** Ensure `ENABLE_KUBE_WATCH=true` and the ServiceAccount has the `watch` verb;
  look for `kube_watch.started` / `kube_watch.event` in logs.
- **Sync logs flooded?** Watch refreshes are intentionally excluded from the Sync Logs page; only
  cron/manual syncs appear there.
- **Slow first load on a huge cluster.** The grid reads the precomputed `sec_summary`; it is backfilled
  once on startup — give the first boot a moment to populate it.
