# K8s Third-Party Version Tracker

Track third-party / open-source container images across one or many Kubernetes (OpenShift) clusters:
their **version drift**, **end-of-life (EOL)** dates, and **vulnerabilities** — then **patch**,
**compare**, and **visualize** them from a single UI.

A primary backend federates any number of remote backends (one per cluster), each watching its own
cluster in real time, while the frontend shows one merged view.

![Resources](docs/ResourcesList.png)

## Features

- **Version & diff tracking** — current vs. latest image version, with major/minor/patch diff.
- **Vulnerability scanning** — Trivy + Twistlock / Prisma Cloud, surfaced as a per-resource badge.
- **End-of-life (EOL)** — product EOL dates from endoflife.date, with an AI/LLM-derived
  support-status estimate when a product isn't listed.
- **Real-time change detection** — a Kubernetes watch refreshes a resource within seconds of a change,
  complementing the nightly sync.
- **Image patching** — create patch jobs with an approval workflow, rollout health checks, and
  automatic rollback.
- **Cluster compare** — diff the same product's manifests between two clusters.
- **Landscape & dashboard** — product/EOL/security overview and custom charts.
- **Reporting & export** — download a selected cluster's product-list report (CSV / PDF).
- **Multi-cluster federation** — one primary + N remote backends, each with its own database.

## Screenshots

| | |
|---|---|
| **Dashboard** — product / EOL / security overview with custom charts | **Landscape** — drag-and-drop product landscape grouped by category |
| ![Dashboard](docs/Dashboard.png) | ![Landscape](docs/Landscape.png) |
| **Compare** — diff a product's manifests between two clusters | **Image Update** — patch jobs with approval, health checks & rollback |
| ![Compare](docs/Compare.png) | ![Image Update](docs/ImageUpdate.png) |
| **Download** — export a cluster's product-list report as CSV or PDF | **Security** — risk thresholds + Trivy / Twistlock config |
| ![Download](docs/Download.png) | ![Security](docs/Security.png) |
| **EOL & auto-sync** — EOL source and per-backend sync schedule | **Exclusions** — skip registries / namespaces during sync |
| ![EOL & auto-sync](docs/EOLandAutoSyncSetup.png) | ![Exclusions](docs/Exclusion.png) |
| **LLM advice prompts** — customizable security / upgrade prompts | **Security details** — per-resource Trivy + Twistlock CVE breakdown |
| ![LLM prompts](docs/LLMPrompt.png) | ![Security details](docs/SecurityDetails.png) |
| **EOL AI analysis** — LLM support-status estimate when a product has no public EOL | |
| ![EOL AI analysis](docs/EOLAnalysis.png) | |

## Tech stack

FastAPI (Python 3.11) · PostgreSQL · React 18 + TypeScript (Vite) · Helm / OpenShift ·
Trivy + Twistlock.

## Documentation

- **[Architecture](docs/ARCHITECTURE.md)** — how the system works (federation, sync, real-time watch,
  data model, security).
- **[Installation & Configuration](docs/INSTALLATION.md)** — local dev, Helm/OpenShift deployment,
  multi-backend federation, integrations, and the full environment-variable reference.

## Quick start (local)

```bash
# PostgreSQL
docker run -d --name patchdb -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16

# Backend (:8000)
cd backend && pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/patchdb"
export KUBE_INCLUSTER=false JWT_SECRET="$(openssl rand -base64 32)" BACKEND_IS_DEFAULT=true
./run_dev.sh

# Frontend
cd ../frontend && npm install && npm run dev
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for cluster deployment.

## License

Licensed under the [Apache License 2.0](LICENSE).
