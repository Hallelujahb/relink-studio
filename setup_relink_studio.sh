#!/usr/bin/env bash
# Relink Studio setup: backend venv + frontend scaffold/deps/sync, then (by
# default) starts both servers. Safe to re-run any time.
#
#   ./setup_relink_studio.sh              # set up, verify, start backend + frontend
#   ./setup_relink_studio.sh --no-start   # set up and verify only
#
# What it guarantees (each step fails loudly, nothing is assumed):
#   - backend venv exists and `create_app()` actually imports and boots
#   - relink-studio/ is scaffolded if missing (Vite + React)
#   - frontend deps the app really imports are installed (papaparse, xlsx)
#   - Tailwind is wired in (the UI is written with Tailwind utility classes;
#     a stock Vite template renders it unstyled)
#   - relink-studio/src/App.jsx is always overwritten from relink_studio.jsx
#     (old copy backed up to App.jsx.bak if it differed)
#   - `npm run build` succeeds before anything is started
set -euo pipefail

START=1
[ "${1:-}" = "--no-start" ] && START=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

API_DIR="$SCRIPT_DIR/relink-api"
FRONTEND_DIR="$SCRIPT_DIR/relink-studio"
JSX_SOURCE="$SCRIPT_DIR/relink_studio.jsx"

# ------------------------------- Backend ------------------------------- #
echo "== Backend setup =="
[ -d "$API_DIR" ] || { echo "ERROR: $API_DIR not found next to this script." >&2; exit 1; }
cd "$API_DIR"

if [ -d venv ] && ! venv/bin/python -c "" 2>/dev/null; then
  echo "Existing venv is broken -- rebuilding."
  rm -rf venv
fi
[ -d venv ] || { echo "Creating virtualenv..."; python3 -m venv venv; }

source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
echo "Verifying the backend actually imports and boots..."
RELINK_DATA_DIR="$(mktemp -d)" RELINK_UPLOAD_DIR="$(mktemp -d)" \
  python -c "from app import create_app; create_app(); print('backend import OK')"
deactivate
cd "$SCRIPT_DIR"

# ------------------------------- Frontend ------------------------------ #
echo "== Frontend setup =="
[ -f "$JSX_SOURCE" ] || { echo "ERROR: $JSX_SOURCE not found." >&2; exit 1; }

if [ ! -d "$FRONTEND_DIR" ]; then
  echo "Scaffolding relink-studio/ (Vite + React)..."
  npm create vite@latest relink-studio -- --template react --no-interactive </dev/null
fi

cd "$FRONTEND_DIR"
npm install
npm install papaparse xlsx tailwindcss @tailwindcss/vite

if ! grep -q "tailwindcss" vite.config.js 2>/dev/null; then
  cat > vite.config.js <<'VITE'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
})
VITE
  echo "Wrote vite.config.js with the Tailwind plugin."
fi
if ! grep -q "tailwindcss" src/index.css 2>/dev/null; then
  printf '@import "tailwindcss";\nhtml, body, #root { height: 100%%; margin: 0; }\n' > src/index.css
  echo "Wrote src/index.css (Tailwind import + full-height root)."
fi

mkdir -p src
if [ -f src/App.jsx ] && ! cmp -s "$JSX_SOURCE" src/App.jsx; then
  cp src/App.jsx src/App.jsx.bak
  echo "Existing App.jsx differed from relink_studio.jsx -- backed up to App.jsx.bak"
fi
cp "$JSX_SOURCE" src/App.jsx
rm -f src/App.css
echo "Synced relink_studio.jsx -> relink-studio/src/App.jsx"

echo "Verifying the frontend builds..."
npm run build
cd "$SCRIPT_DIR"

echo ""
echo "Setup complete and verified."
if [ "$START" = "0" ]; then
  echo "Start with:"
  echo "  ./run_relink.sh --local            # backend  -> http://127.0.0.1:5000"
  echo "  cd relink-studio && npm run dev    # frontend -> http://localhost:5173"
  exit 0
fi

echo "Starting backend (http://127.0.0.1:5000) and frontend (http://localhost:5173). Ctrl-C stops both."
./run_relink.sh --local &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM
cd "$FRONTEND_DIR"
npm run dev
