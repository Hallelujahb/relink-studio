"""
Centralized runtime configuration for Relink Studio's backend.

Two modes:
  - "local" (default): binds to 127.0.0.1 only. Nothing on your network
    can reach it. Matches the tool's original behavior exactly.
  - "lan": binds to 0.0.0.0 so other devices on your own private network
    can reach it, requires authentication by default, and never runs
    with Flask's debug mode on (regardless of RELINK_DEBUG).

Every setting can still be overridden explicitly via its own environment
variable -- an explicit value always wins over the mode's default, in
either mode. This module only computes and validates configuration; it
doesn't touch the filesystem or create the Flask app.

LAN mode is for a trusted private network (home, office LAN, VPN) only.
Relink Studio has no built-in TLS, rate limiting, or public-internet
hardening -- never forward its port through a router or expose it
directly to the public internet.
"""
import os
import socket


class ConfigError(ValueError):
    """Raised for invalid Relink configuration. The message is meant to
    be read directly by whoever set the bad environment variable."""


VALID_MODES = ("local", "lan")
DEFAULT_PORT = 5000
DEFAULT_CORS_ORIGINS = "http://localhost:5173"


def _present(env, name):
    return name in env and env[name] != ""


def _parse_bool(value, name):
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"Invalid value for {name}: {value!r}. Use one of: 1/0, true/false, yes/no, on/off."
    )


class RelinkConfig:
    def __init__(self, mode, host, port, require_auth, cors_origins, debug, data_dir, upload_dir):
        self.mode = mode
        self.host = host
        self.port = port
        self.require_auth = require_auth
        self.cors_origins = cors_origins
        self.debug = debug
        self.data_dir = data_dir
        self.upload_dir = upload_dir

    def local_url(self):
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "127.0.0.1") else self.host
        return f"http://{display_host}:{self.port}"

    def lan_url(self):
        """Best-effort LAN-reachable URL. Only meaningful in LAN mode;
        returns None if not in LAN mode or no LAN-facing IP could be
        detected (e.g. offline)."""
        if self.mode != "lan":
            return None
        ip = _detect_lan_ip()
        return f"http://{ip}:{self.port}" if ip else None

    def print_banner(self, stream=None):
        import sys
        stream = stream or sys.stdout
        lines = [
            "Relink Studio API",
            f"  mode:            {self.mode}",
            f"  local URL:       {self.local_url()}",
        ]
        lan_url = self.lan_url()
        if lan_url:
            lines.append(f"  LAN URL:         {lan_url}")
        elif self.mode == "lan":
            lines.append("  LAN URL:         (could not detect a LAN IP -- check your network connection)")
        lines += [
            f"  port:            {self.port}",
            f"  auth required:   {self.require_auth}",
            f"  data dir:        {self.data_dir}",
            f"  upload dir:      {self.upload_dir}",
        ]
        if self.mode == "lan":
            lines.append("  WARNING: bound to 0.0.0.0 -- reachable from your whole LAN.")
            lines.append("  Do NOT forward this port through your router to the public internet.")
            if not self.require_auth:
                lines.append("  WARNING: authentication is DISABLED in LAN mode (explicitly overridden).")
            if self.host == "127.0.0.1":
                lines.append("  NOTE: RELINK_HOST=127.0.0.1 overrides LAN mode's usual 0.0.0.0 -- other devices won't reach this.")
        for line in lines:
            print(line, file=stream)


def _detect_lan_ip():
    """Best-effort: asks the OS which local interface it would use to
    reach the outside world, without actually sending any data (UDP
    connect() just resolves a route, it doesn't transmit anything).
    Returns None if nothing usable is found (e.g. no network at all)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def load_config(env=None):
    """Reads environment variables (or the given mapping, for tests) and
    returns a RelinkConfig. Raises ConfigError on anything invalid --
    callers should let that propagate rather than silently guessing."""
    env = os.environ if env is None else env

    raw_mode = env.get("RELINK_MODE", "local").strip().lower()
    if raw_mode not in VALID_MODES:
        raise ConfigError(f"Invalid RELINK_MODE: {raw_mode!r}. Must be 'local' or 'lan'.")
    mode = raw_mode

    if _present(env, "RELINK_HOST"):
        host = env["RELINK_HOST"].strip()
        if not host:
            raise ConfigError("RELINK_HOST is set but empty.")
    else:
        host = "127.0.0.1" if mode == "local" else "0.0.0.0"

    if _present(env, "RELINK_PORT"):
        raw_port = env["RELINK_PORT"].strip()
        try:
            port = int(raw_port)
        except ValueError:
            raise ConfigError(f"Invalid RELINK_PORT: {raw_port!r}. Must be an integer.")
        if not (1 <= port <= 65535):
            raise ConfigError(f"Invalid RELINK_PORT: {port}. Must be between 1 and 65535.")
    else:
        port = DEFAULT_PORT

    if _present(env, "RELINK_REQUIRE_AUTH"):
        require_auth = _parse_bool(env["RELINK_REQUIRE_AUTH"], "RELINK_REQUIRE_AUTH")
    else:
        # LAN mode is reachable by other devices on the network, so it
        # requires authentication unless someone explicitly turns it off.
        # Local mode (127.0.0.1-only) keeps the original off-by-default
        # behavior so the existing single-machine flow doesn't change.
        require_auth = mode == "lan"

    raw_cors = env.get("RELINK_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    cors_origins = [o.strip() for o in raw_cors.split(",") if o.strip()]
    if not cors_origins:
        raise ConfigError("RELINK_CORS_ORIGINS is set but resolves to zero origins.")

    # LAN mode never runs with Flask's debug mode (interactive debugger +
    # reloader), no matter what RELINK_DEBUG says -- debug mode is a
    # remote-code-execution risk on anything reachable beyond localhost.
    if mode == "lan":
        debug = False
    else:
        debug = _parse_bool(env.get("RELINK_DEBUG", "0"), "RELINK_DEBUG")

    data_dir = env.get("RELINK_DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "data")
    upload_dir = env.get("RELINK_UPLOAD_DIR") or os.path.join(os.path.dirname(__file__), "..", "uploads")

    return RelinkConfig(
        mode=mode,
        host=host,
        port=port,
        require_auth=require_auth,
        cors_origins=cors_origins,
        debug=debug,
        data_dir=os.path.abspath(data_dir),
        upload_dir=os.path.abspath(upload_dir),
    )

