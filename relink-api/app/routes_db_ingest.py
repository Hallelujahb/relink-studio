"""
Direct database import: turns a Postgres, ClickHouse or MySQL table into a
registered file, so it can be a project's source or target like an upload.

POST /api/db/test-connection   check credentials and reachability only
POST /api/db/schema            column list and estimated row count for a table
POST /api/db/ingest            pull the table (default cap 100,000 rows)

Body: provider, host, port, database, table, optional schema, username,
password, ssl, and row_limit for /ingest (null means no cap).

The caller chooses the host and port, so the server will connect anywhere it
can reach. With logins on these routes need the lead role. Connection details
live only in the request and are never stored, logged or audited. A missing
driver returns 501. ClickHouse and MySQL have not been run against a live
server, Postgres is the tested path.
"""
import json
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from .auth import require_lead_when_auth
from .connectors import ConnectionConfig, get_provider, ProviderUnavailable, redact_connection_config
from .db import get_conn
from .parsers import save_parsed_payload

bp = Blueprint("api_db_ingest", __name__)

DEFAULT_ROW_LIMIT = 100_000
_VALID_PROVIDERS = ("postgresql", "clickhouse", "mysql")


def _uid():
    return uuid.uuid4().hex[:12]


def _now():
    return datetime.utcnow().isoformat()


def _err(message, status=400):
    return jsonify({"error": message}), status


def _config_from_body(body):
    """Builds (provider_name, ConnectionConfig) from the request body, or
    raises ValueError with a message safe to return directly to the caller."""
    provider = body.get("provider")
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"'provider' must be one of {_VALID_PROVIDERS}.")
    for field in ("host", "port", "database"):
        if not body.get(field):
            raise ValueError(f"'{field}' is required.")
    try:
        port = int(body["port"])
    except (TypeError, ValueError):
        raise ValueError("'port' must be an integer.")
    cfg = ConnectionConfig(
        host=body["host"], port=port, database=body["database"],
        username=body.get("username"), password=body.get("password"),
        schema=body.get("schema"), table=body.get("table"),
        ssl=bool(body.get("ssl", False)),
    )
    errors = cfg.validate()
    if errors:
        raise ValueError("Invalid connection config: " + "; ".join(errors))
    return provider, cfg


@bp.route("/db/test-connection", methods=["POST"])
@require_lead_when_auth
def test_connection():
    body = request.get_json(silent=True) or {}
    try:
        provider_name, cfg = _config_from_body(body)
    except ValueError as e:
        return _err(str(e))
    try:
        provider = get_provider(provider_name, cfg)
        result = provider.test_connection()
    except ProviderUnavailable as e:
        return _err(str(e), 501)
    except Exception as e:  # real driver/network error -- surface it, don't swallow it
        return _err(f"Connection failed: {e}", 502)
    return jsonify({"ok": True, "config": result.get("config", redact_connection_config(cfg.__dict__))})


@bp.route("/db/schema", methods=["POST"])
@require_lead_when_auth
def db_schema():
    body = request.get_json(silent=True) or {}
    try:
        provider_name, cfg = _config_from_body(body)
    except ValueError as e:
        return _err(str(e))
    if not cfg.table:
        return _err("'table' is required to discover a schema.")
    try:
        provider = get_provider(provider_name, cfg)
        schema = provider.discover_schema()
        schema["estimated_row_count"] = provider.estimate_row_count()
    except ProviderUnavailable as e:
        return _err(str(e), 501)
    except Exception as e:
        return _err(f"Schema discovery failed: {e}", 502)
    return jsonify(schema)


@bp.route("/db/ingest", methods=["POST"])
@require_lead_when_auth
def db_ingest():
    body = request.get_json(silent=True) or {}
    try:
        provider_name, cfg = _config_from_body(body)
    except ValueError as e:
        return _err(str(e))
    if not cfg.table:
        return _err("'table' is required.")

    raw_limit = body.get("row_limit", DEFAULT_ROW_LIMIT)
    row_limit = int(raw_limit) if raw_limit is not None else None
    if row_limit is not None and row_limit <= 0:
        return _err("'row_limit' must be a positive integer, or null for no cap.")

    try:
        provider = get_provider(provider_name, cfg)
        rows, truncated = [], False
        for chunk in provider.extract_chunks(chunk_size=5000):
            rows.extend(chunk)
            if row_limit is not None and len(rows) >= row_limit:
                rows = rows[:row_limit]
                truncated = True
                break
    except ProviderUnavailable as e:
        return _err(str(e), 501)
    except Exception as e:
        return _err(f"Ingestion failed: {e}", 502)

    if not rows:
        return _err(f"'{cfg.table}' returned zero rows (empty table, or the query matched nothing).", 404)

    columns = list(rows[0].keys())
    label = f"{cfg.schema + '.' if cfg.schema else ''}{cfg.table}"
    filename = f"{provider_name}:{label}"
    payload = {"columns": columns, "rows": rows, "row_count": len(rows), "format": provider_name, "geometry": None}

    file_id = _uid()
    upload_dir = current_app.config["UPLOAD_DIR"]
    # No raw file on disk for a DB-sourced "upload" -- the cached parsed
    # payload IS the durable artifact, so it doubles as stored_path. This
    # keeps the existing `files` schema, delete/cleanup routes, and
    # load_parsed_payload() all working unmodified.
    parsed_path = save_parsed_payload(upload_dir, file_id, payload)

    conn = get_conn()
    conn.execute(
        "INSERT INTO files (id, filename, stored_path, columns_json, row_count, format, has_geometry, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (file_id, filename, parsed_path, json.dumps(columns), len(rows), provider_name, 0, _now()),
    )
    conn.commit()

    return jsonify({
        "file_id": file_id, "filename": filename, "columns": columns,
        "row_count": len(rows), "format": provider_name, "has_geometry": False,
        "truncated": truncated,
    }), 201
