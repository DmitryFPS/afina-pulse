#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export AFINA_HOST="${AFINA_HOST:-0.0.0.0}"
export AFINA_PORT="${AFINA_PORT:-8091}"
echo "Starting Afina Watch on http://127.0.0.1:${AFINA_PORT}"
exec python3 scripts/ui_server.py
