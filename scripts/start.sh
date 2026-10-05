#!/usr/bin/env bash
# One-command launcher: installs deps if needed, builds the UI, starts the platform.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python3 -c "import evalplatform" 2>/dev/null; then
  echo "▶ Installing backend…"
  python3 -m pip install -q -e ".[modal]"
fi
if [ ! -d frontend/node_modules ]; then
  echo "▶ Installing frontend…"
  (cd frontend && npm install --silent)
fi
if [ ! -d frontend/dist ] || [ -n "$(find frontend/src -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "▶ Building UI…"
  (cd frontend && npm run build --silent)
fi
echo "▶ Open http://localhost:${EVAL_PORT:-8000}"
exec python3 -m evalplatform "$@"
