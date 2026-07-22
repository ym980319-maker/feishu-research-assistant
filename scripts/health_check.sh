#!/bin/sh
set -eu

HEALTH_HOST="${HEALTH_HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
export HEALTH_URL="http://${HEALTH_HOST}:${PORT}/health"

python - <<'PY'
import json
import os
import sys
import urllib.request

url = os.environ["HEALTH_URL"]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception:
    sys.exit(1)

sys.exit(0 if payload == {"status": "ok"} else 1)
PY
