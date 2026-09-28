#!/usr/bin/env bash
# Relink Studio setup — backend venv + frontend, kept in sync with the
# real source every time this is run. Safe to re-run any time.
#
# FIX: setup previously scaffolded a stock Vite template for
# relink-studio/ and nothing ever synced relink_studio.jsx into it -- so
# every fresh clone, and every re-run of the old script, silently kept
# serving the old/wrong UI no matter what was in relink_studio.jsx. This
# version always overwrites relink-studio/src/App.jsx from
# relink_studio.jsx, so the two can never drift apart again.
#
# Also: the backend venv is now sanity-checked after install (an actual
# `from app import create_app; create_app()` import, not just "pip install
# didn't error") so a broken backend fails LOUDLY here with a real
# traceback, instead of surfacing later as a silent "it doesn't work".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

API_DIR="$SCRIPT_DIR/relink-api"
FRONTEND_DIR="$SCRIPT_DIR/relink-studio"
JSX_SOURCE="$SCRIPT_DIR/relink_studio.jsx"

# --------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------- #
echo "== Backend setup =="
if [ ! -d "$API_DIR" ]; then
  echo "ERROR: $API_DIR not found next to this script." >&2
  exit 1
fi
cd "$API_DIR"

if [ -d venv ] && ! venv/bin/python -c "" 2>/dev/null; then
  echo "Existing venv is broken (python binary missing/invalid) -- rebuilding."
  rm -rf venv
fi
if [ ! -d venv ]; then
  echo "Creating virtualenv..."
  python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
echo "Verifying the backend actually imports and boots..."
RELINK_DATA_DIR="$(mktemp -d)" RELINK_UPLOAD_DIR="$(mktemp -d)" \
  python -c "from app import create_app; create_app(); print('backend import OK')"
deactivate
cd "$SCRIPT_DIR"

# --------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------- #
echo "== Frontend setup =="
if [ ! -f "$JSX_SOURCE" ]; then
  echo "ERROR: $JSX_SOURCE not found -- nothing to sync into the frontend." >&2
  exit 1
fi
if [ ! -d "$FRONTEND_DIR" ]; then
  echo "ERROR: $FRONTEND_DIR not found. Scaffold it first:" >&2
  echo "  npm create vite@latest relink-studio -- --template react" >&2
  exit 1
fi

mkdir -p "$FRONTEND_DIR/src"
if [ -f "$FRONTEND_DIR/src/App.jsx" ] && ! cmp -s "$JSX_SOURCE" "$FRONTEND_DIR/src/App.jsx"; then
  cp "$FRONTEND_DIR/src/App.jsx" "$FRONTEND_DIR/src/App.jsx.bak"
  echo "Existing App.jsx differed from relink_studio.jsx -- backed up to App.jsx.bak"
fi
cp "$JSX_SOURCE" "$FRONTEND_DIR/src/App.jsx"
echo "Synced relink_studio.jsx -> relink-studio/src/App.jsx"

cd "$FRONTEND_DIR"
npm install
cd "$SCRIPT_DIR"

echo ""
echo "Setup complete and verified. Run:"
echo "  ./run_relink.sh --local            # backend  -> http://127.0.0.1:5000"
echo "  cd relink-studio && npm run dev    # frontend -> http://localhost:5173"
