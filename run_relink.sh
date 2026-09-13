#!/usr/bin/env bash
# Starts the Relink Studio backend in either local-only or LAN mode.
#
# Usage:
#   ./run_relink.sh --local   # 127.0.0.1 only, no LAN access (default behavior)
#   ./run_relink.sh --lan     # 0.0.0.0, reachable from your private network,
#                             # authentication required by default
#
# Anything you've already exported yourself (RELINK_HOST, RELINK_PORT,
# RELINK_REQUIRE_AUTH, RELINK_CORS_ORIGINS, RELINK_DATA_DIR,
# RELINK_UPLOAD_DIR, ...) is left alone -- this script only sets
# RELINK_MODE from the flag you pass. Every setting and its default per
# mode is documented in relink-api/app/config.py.
#
# LAN mode is for your own private network only (home, office, VPN).
# Never forward this port through your router to the public internet --
# Relink has no built-in TLS or public-internet hardening.
set -euo pipefail

usage() {
  echo "Usage: $0 --local | --lan" >&2
  exit 1
}

[ "$#" -eq 1 ] || usage
case "$1" in
  --local) export RELINK_MODE=local ;;
  --lan)   export RELINK_MODE=lan ;;
  *) usage ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$SCRIPT_DIR/relink-api"

if [ ! -d "$API_DIR" ]; then
  echo "Could not find relink-api/ next to this script (looked in $API_DIR)." >&2
  exit 1
fi

cd "$API_DIR"

if [ ! -d venv ]; then
  echo "Creating virtualenv (first run only)..."
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet -r requirements.txt

if [ "$RELINK_MODE" = "lan" ]; then
  echo "Starting Relink in LAN mode -- reachable from other devices on your"
  echo "private network. Do NOT forward this port through your router to"
  echo "the public internet."
fi

exec python run.py

