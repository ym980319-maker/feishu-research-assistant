#!/bin/sh
set -eu

export APP_ENV="${APP_ENV:-production}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"

exec python -m app.server
