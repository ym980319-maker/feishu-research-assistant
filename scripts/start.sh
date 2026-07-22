#!/bin/sh
set -eu

export APP_ENV="${APP_ENV:-production}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"

echo "[startup] checking production environment"
python -m app.deployment.check
echo "[startup] environment ready; starting Research Assistant on ${HOST}:${PORT}"
exec python -m app.server
