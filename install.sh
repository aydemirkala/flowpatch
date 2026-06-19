#!/usr/bin/env bash
#
# Example multi-cluster install script (template).
#
# Deploys the primary backend to one cluster and a remote (federated) backend to
# each additional cluster. Replace the placeholders below with your own cluster
# API URLs, namespaces, and values files. See docs/INSTALLATION.md for details.
#
# Prerequisites: helm 3, kubectl/oc, and one values file per backend under
# charts/patchmgmt/values-examples/ (copy and edit for your environment).

set -euo pipefail

# --- Primary backend ---------------------------------------------------------
# oc login -u <your-username> https://api.example.com:6443
helm upgrade --install patchmgmt ./charts/patchmgmt \
  -f charts/patchmgmt/values-examples/values-primary-backend.yaml \
  -n patchmgmt --create-namespace

# --- Remote (federated) backends --------------------------------------------
# Repeat for each additional cluster; each uses its own values file and points
# primaryBackendUrl at the primary above (configured inside the values file).
#
# oc login -u <your-username> https://api.example.com:6443
# helm upgrade --install patchmgmt ./charts/patchmgmt \
#   -f charts/patchmgmt/values-examples/values-remote-backend.yaml \
#   -n patchmgmt --create-namespace
