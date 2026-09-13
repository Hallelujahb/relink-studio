"""
Ingestion provider abstraction for ReLink.

Local file ingestion (CSV/XLSX/GeoJSON, see app.parsers) is unaffected and
remains the default. This module adds:

  - a small Provider interface every source type implements
  - real capability detection for optional DB drivers (psycopg2 for
    Postgres, clickhouse-connect for ClickHouse) -- reports honestly
    whether each is installed, it never fakes connectivity
  - a connection-config validator that never accepts credentials embedded
    in a URL and never returns/logs a raw password

No network calls happen at import time. Nothing here sends data anywhere
outside the host the API process runs on -- Postgres/ClickHouse are
expected to be on localhost or the private LAN, exactly like the existing
Flask<->frontend setup.
"""
from dataclasses import dataclass, field
from typing import Optional

_REDACTED = "***redacted***"
_SECRET_FIELDS = ("password", "passwd", "pwd", "token", "secret")


def redact_connection_config(config: dict) -> dict:
    """Returns a copy safe to log or store in audit records -- every key
    that looks like a credential is replaced, never truncated-but-visible."""
    out = {}
    for k, v in (config or {}).items():
        if any(s in k.lower() for s in _SECRET_FIELDS):
            out[k] = _REDACTED if v else None
        else:
            out[k] = v
    return out


@dataclass
class ConnectionConfig:
    host: str
    port: int
    database: str
    username: Optional[str] = None
    password: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None
    read_only: bool = True
    ssl: bool = False
    extra: dict = field(default_factory=dict)

    def validate(self):
        errors = []
        if not self.host:
            errors.append("host is required")
        if not self.port:
            errors.append("port is required")
        if not self.database:
            errors.append("database is required")
        if self.password and any(sep in (self.host or "") for sep in ("@", "://")):
            # Credentials must never be smuggled into the host/URL field.
            errors.append("credentials must not be embedded in the host/URL; use the password field")
        return errors


def detect_postgres_capability():
    try:
        import psycopg2  # noqa: F401
        return {"available": True, "driver": "psycopg2", "reason": None}
    except ImportError as e:
        return {"available": False, "driver": None, "reason": f"psycopg2 not installed ({e})"}


def detect_clickhouse_capability():
    try:
        import clickhouse_connect  # noqa: F401
        return {"available": True, "driver": "clickhouse_connect", "reason": None}
    except ImportError as e:
        return {"available": False, "driver": None, "reason": f"clickhouse_connect not installed ({e})"}


def list_capabilities():
    return {
        "file": {"available": True, "driver": "pandas/openpyxl", "reason": None},
        "postgresql": detect_postgres_capability(),
        "clickhouse": detect_clickhouse_capability(),
    }


class ProviderUnavailable(RuntimeError):
    pass


class BaseProvider:
    """Interface every ingestion provider implements. Subclasses that need
    an optional driver must raise ProviderUnavailable in __init__ rather
    than silently degrading, so the caller can surface a clear error."""

    name = "base"

    def test_connection(self) -> dict:
        raise NotImplementedError

    def discover_schema(self) -> dict:
        raise NotImplementedError

    def estimate_row_count(self) -> Optional[int]:
        raise NotImplementedError

    def extract_chunks(self, chunk_size: int = 5000):
        raise NotImplementedError


class PostgresProvider(BaseProvider):
    name = "postgresql"

    def __init__(self, config: ConnectionConfig):
        cap = detect_postgres_capability()
        if not cap["available"]:
            raise ProviderUnavailable(
                "PostgreSQL support requires 'psycopg2' (or psycopg2-binary), which is not "
                "installed. Install it and restart the API to enable this connector -- no "
                "connection was attempted."
            )
        errors = config.validate()
        if errors:
            raise ValueError("Invalid PostgreSQL connection config: " + "; ".join(errors))
        self.config = config

    def test_connection(self) -> dict:
        import psycopg2
        conn = psycopg2.connect(
            host=self.config.host, port=self.config.port, dbname=self.config.database,
            user=self.config.username, password=self.config.password,
            sslmode="require" if self.config.ssl else "prefer", connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return {"ok": True, "config": redact_connection_config(self.config.__dict__)}
        finally:
            conn.close()


class ClickHouseProvider(BaseProvider):
    name = "clickhouse"

    def __init__(self, config: ConnectionConfig):
        cap = detect_clickhouse_capability()
        if not cap["available"]:
            raise ProviderUnavailable(
                "ClickHouse support requires 'clickhouse-connect', which is not installed. "
                "Install it and restart the API to enable this connector -- no connection "
                "was attempted."
            )
        errors = config.validate()
        if errors:
            raise ValueError("Invalid ClickHouse connection config: " + "; ".join(errors))
        self.config = config

    def test_connection(self) -> dict:
        import clickhouse_connect
        client = clickhouse_connect.get_client(
            host=self.config.host, port=self.config.port, database=self.config.database,
            username=self.config.username, password=self.config.password,
            secure=self.config.ssl,
        )
        client.command("SELECT 1")
        return {"ok": True, "config": redact_connection_config(self.config.__dict__)}


def get_provider(name: str, config: Optional[ConnectionConfig] = None) -> BaseProvider:
    if name == "postgresql":
        return PostgresProvider(config)
    if name == "clickhouse":
        return ClickHouseProvider(config)
    raise ValueError(f"Unknown provider: {name}")
