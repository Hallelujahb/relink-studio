"""
Runtime configuration for Relink Studio's backend.

Two modes:
  local (default)  binds to 127.0.0.1, no login, Host header checked.
  lan              binds to 0.0.0.0, login required by default, debug never on.

Any setting can be overridden with its own environment variable, and an
explicit value always beats the mode default. This module only reads and
validates the environment. It does not touch the filesystem.

LAN mode is for a trusted private network. There is no TLS or hardening for
the public internet, so never forward the port to it.
"""
import os
import socket


class ConfigError(ValueError):
    """Invalid configuration. The message is written to be read by whoever set the variable."""


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
    raise ConfigError(f"Invalid value for {name}: {value!r}. Use one of: 1/0, true/false, yes/no, on/off.")


class RelinkConfig:
    def __init__(self, mode, host, port, require_auth, cors_origins, debug, data_dir, upload_dir, allowed_hosts=None):
        self.mode = mode
        self.host = host
        self.port = port
        self.require_auth = require_auth
        self.cors_origins = cors_origins
        self.debug = debug
        self.data_dir = data_dir
        self.upload_dir = upload_dir
        self.allowed_hosts = allowed_hosts or []

    def local_url(self):
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "127.0.0.1") else self.host
        return f"http://{display_host}:{self.port}"

    def lan_url(self):
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
            lines.append("  LAN URL:         (could not detect a LAN IP, check your network connection)")
        lines += [
            f"  port:            {self.port}",
            f"  auth required:   {self.require_auth}",
            f"  data dir:        {self.data_dir}",
            f"  upload dir:      {self.upload_dir}",
        ]
        if self.mode == "lan":
            lines.append("  WARNING: bound to 0.0.0.0, reachable from your whole LAN.")
            lines.append("  Do NOT forward this port through your router to the public internet.")
            if not self.require_auth:
                lines.append("  WARNING: authentication is DISABLED in LAN mode (explicitly overridden).")
            if self.host == "127.0.0.1":
                lines.append("  NOTE: RELINK_HOST=127.0.0.1 overrides LAN mode's usual 0.0.0.0, other devices won't reach this.")
        for line in lines:
            print(line, file=stream)


def _detect_lan_ip():
    """Asks the OS which interface it would use to reach the outside. Nothing is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def load_config(env=None):
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
        require_auth = mode == "lan"

    raw_cors = env.get("RELINK_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    cors_origins = [o.strip() for o in raw_cors.split(",") if o.strip()]
    if not cors_origins:
        raise ConfigError("RELINK_CORS_ORIGINS is set but resolves to zero origins.")

    # Flask's debugger is remote code execution on anything beyond localhost.
    if mode == "lan":
        debug = False
    else:
        debug = _parse_bool(env.get("RELINK_DEBUG", "0"), "RELINK_DEBUG")

    allowed_hosts = [h.strip().lower() for h in env.get("RELINK_ALLOWED_HOSTS", "").split(",") if h.strip()]

    data_dir = env.get("RELINK_DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "data")
    upload_dir = env.get("RELINK_UPLOAD_DIR") or os.path.join(os.path.dirname(__file__), "..", "uploads")

    return RelinkConfig(
        mode=mode, host=host, port=port, require_auth=require_auth, cors_origins=cors_origins,
        debug=debug, data_dir=os.path.abspath(data_dir), upload_dir=os.path.abspath(upload_dir),
        allowed_hosts=allowed_hosts,
    )
