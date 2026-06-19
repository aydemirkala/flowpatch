#!/bin/sh
set -euo pipefail

export PYTHONPATH=$(dirname "$0")/app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
