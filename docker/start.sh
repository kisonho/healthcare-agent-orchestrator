#!/usr/bin/env bash
set -euo pipefail

pip install -r /app/src/requirements.txt

# Ensure src directory is discoverable
export PYTHONPATH="/app/src:${PYTHONPATH:-}"

exec python -m uvicorn src.app:app --host 0.0.0.0 --port 8000
