#!/usr/bin/env bash
# Relink Studio: one command to install, build, start and open.
#
#   ./relink.sh          run on this machine only (127.0.0.1)
#   ./relink.sh --lan    share on your private network (login required)
#   ./relink.sh --setup  install and build, then stop
#
# Re-running is cheap. Dependencies and the frontend build are only redone
# when requirements.txt, package.json or the frontend source changed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="$ROOT/relink-api"
WEB="$ROOT/relink-studio"
JSX="$ROOT/relink_studio.jsx"

mode=local
start=1
for arg in "$@"; do
  case "$arg" in
    --lan) mode=lan ;;
    --local) mode=local ;;
    --setup) start=0 ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 1 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

command -v python3 >/dev/null || die "python3 is not installed. Install Python 3.10 or newer."
command -v node >/dev/null || die "node is not installed. Install Node 20 or newer."
command -v npm >/dev/null || die "npm is not installed. It normally ships with Node."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 or newer is required (found $(python3 -V 2>&1))."
python3 -c 'import venv, ensurepip' 2>/dev/null \
  || die "python3-venv is missing. On Debian or Ubuntu: sudo apt install python3-venv"
[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] \
  || die "Node 20 or newer is required (found $(node -v))."
[ -d "$API" ] || die "relink-api/ not found next to this script."
[ -f "$WEB/package.json" ] || die "relink-studio/package.json not found next to this script."
[ -f "$JSX" ] || die "relink_studio.jsx not found next to this script."

step "Backend"
cd "$API"
if [ -d venv ] && ! venv/bin/python -c "" 2>/dev/null; then
  echo "Existing virtualenv is broken, rebuilding it."
  rm -rf venv
fi
[ -d venv ] || python3 -m venv venv
stamp="venv/.requirements.stamp"
if [ ! -f "$stamp" ] || [ requirements.txt -nt "$stamp" ]; then
  venv/bin/python -m pip install --quiet --upgrade pip
  venv/bin/python -m pip install --quiet -r requirements.txt
  tmp_data="$(mktemp -d)"; tmp_up="$(mktemp -d)"
  RELINK_DATA_DIR="$tmp_data" RELINK_UPLOAD_DIR="$tmp_up" \
    venv/bin/python -c "from app import create_app; create_app()" \
    || { rm -rf "$tmp_data" "$tmp_up"; die "the backend installed but failed to start."; }
  rm -rf "$tmp_data" "$tmp_up"
  touch "$stamp"
else
  echo "Dependencies already installed."
fi

step "Frontend"
cd "$WEB"
mkdir -p src
cmp -s "$JSX" src/App.jsx || cp "$JSX" src/App.jsx
if [ ! -d node_modules ] || [ package.json -nt node_modules ]; then
  npm install --no-audit --no-fund
  touch node_modules
fi
if [ ! -f dist/index.html ] || [ -n "$(find src package.json vite.config.js index.html -newer dist/index.html -print -quit)" ]; then
  npm run build
else
  echo "Build is up to date."
fi
cd "$ROOT"

if [ "$start" = "0" ]; then
  echo
  echo "Setup finished. Run ./relink.sh to start."
  exit 0
fi

export RELINK_MODE="$mode"
export RELINK_FRONTEND_DIST="$WEB/dist"
export RELINK_DEBUG=0   # the debug reloader would leave a child process behind on Ctrl-C
port="${RELINK_PORT:-5000}"
probe_host="127.0.0.1"
case "${RELINK_HOST:-}" in ""|0.0.0.0) ;; *) probe_host="$RELINK_HOST" ;; esac

if python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
then
  die "port $port is already in use. Stop whatever is using it, or set RELINK_PORT to another port."
fi

step "Starting"
"$API/venv/bin/python" "$API/run.py" >"$ROOT/relink.log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT INT TERM

ready=0
for _ in $(seq 1 60); do
  kill -0 "$pid" 2>/dev/null || break
  if python3 -c "import urllib.request; urllib.request.urlopen('http://$probe_host:$port/health', timeout=1)" 2>/dev/null; then
    ready=1; break
  fi
  sleep 0.5
done
if [ "$ready" != "1" ]; then
  echo "The backend did not come up. Last lines of relink.log:" >&2
  tail -n 20 "$ROOT/relink.log" >&2
  exit 1
fi

echo
echo "Relink Studio is running."
echo
echo "  Open:  http://$probe_host:$port"
if [ "$mode" = "lan" ]; then
  ip="$(python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print(s.getsockname()[0])" 2>/dev/null || true)"
  [ -n "$ip" ] && echo "  LAN:   http://$ip:$port"
  echo
  echo "LAN mode requires a login. The first account has to be created from this machine:"
  echo "  curl -X POST http://127.0.0.1:$port/api/auth/register -H 'Content-Type: application/json' \\"
  echo "       -d '{\"username\":\"you\",\"password\":\"at-least-8-chars\",\"role\":\"lead\"}'"
  echo "Only use LAN mode on a network you trust, and never forward the port to the internet."
fi
echo
echo "Logs: relink.log    Stop: Ctrl-C"
wait "$pid"
