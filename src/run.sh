#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=".venv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install -q fastapi uvicorn pydantic aiofiles websockets

echo ""
echo "  RICAIP Digital Twin"
echo "  http://localhost:8765"
echo ""

PYTHONPATH=src/tracking "$VENV/bin/uvicorn" \
  src.tracking.server:app \
  --host 0.0.0.0 \
  --port 8765 \
  --reload \
  --reload-dir src/tracking
