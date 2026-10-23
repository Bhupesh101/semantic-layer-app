#!/usr/bin/env bash
# Run backend (http://127.0.0.1:8000) and frontend dev server (http://localhost:5173) together.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/node/bin:$PATH"

if [ ! -x "$ROOT/backend/.venv/bin/uvicorn" ]; then
  python3 -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements-dev.txt"
fi
[ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && npm install --no-audit --no-fund)

(cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd "$ROOT/frontend" && npm run dev
