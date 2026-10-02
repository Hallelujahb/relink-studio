"""
Endpoints that sit next to the core flow in routes.py: data profiling, schema
snapshots and drift, blocking diagnostics, run metrics, review sampling,
structured decision history, exceptions, row explanations, storage cleanup and
connector capabilities. Registered under /api in app/__init__.py.
"""
import json
import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from .auth import actor_name, get_current_user, require_lead_when_auth
from .blocking_diagnostics import analyze_blocking
from .connectors import list_capabilities
from .db import get_conn
from .exceptions_mgmt import EXCEPTION_SCOPES
from .explainability import explain_review_row
from .httputil import int_arg
from .metrics import evaluation_metrics, run_metrics
from .parsers import ParseError, load_parsed_payload
from .profiling import identifier_warnings, profile_dataset
from .retention import cleanup_audit_entry, delete_items, preview_cleanup, storage_usage_summary
from .review_decisions import DECISION_TYPES, REJECTION_REASONS
from .sampling import (
    by_kind_sample, deterministic_sample, highest_confidence_sample,
    largest_score_gap_sample, lowest_confidence_sample, random_sample,
)
from .schema_tools import diff_schema, discover_schema

bp = Blueprint("api_extra", __name__)


def _uid():
    return uuid.uuid4().hex[:12]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _err(message, status=400):
    return jsonify({"error": message}), status


def _actor(claimed=None):
    """Who did this. A logged in user always wins over whatever the request body
    claims. Only when nobody is logged in is the claimed name used."""
    user = get_current_user()
    return user["username"] if user else (claimed or "anonymous")


def _text_or_none(value):
    return None if value is None else str(value)


def _log_audit(conn, project_id, action, detail=None, actor=None):
    conn.execute(
        "INSERT INTO audit_log (id, project_id, actor, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (_uid(), project_id, actor or actor_name(), action, json.dumps(detail) if detail is not None else None, _now()),
    )
    conn.commit()


def _get_file(conn, file_id):
    return conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()


def _get_project(conn, project_id):
    return conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


def _review_rows(conn, project_id, with_name=False):
    out = []
    for r in conn.execute("SELECT * FROM review_rows WHERE project_id=?", (project_id,)).fetchall():
        row = {"sourceId": r["source_id"], "approved": bool(r["approved"]), "decision": r["decision"],
               "kind": r["kind"], "methods": json.loads(r["methods_json"])}
        if with_name:
            row["sourceName"] = r["source_name"]
        out.append(row)
    return out


# Profiling ------------------------------------------------------------- #

@bp.route("/upload/<file_id>/profile", methods=["GET"])
def profile_upload(file_id):
    conn = get_conn()
    if not _get_file(conn, file_id):
        return _err("File not found.", 404)
    try:
        parsed = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
    except ParseError as e:
        return _err(str(e), 404)
    result = profile_dataset(parsed["columns"], parsed["rows"])
    id_column = request.args.get("id_column")
    if id_column:
        result["identifier_warnings"] = identifier_warnings(result, id_column)
    return jsonify(result)


# Schema and drift ------------------------------------------------------- #

def _store_snapshot(conn, f, file_id, schema):
    conn.execute(
        "INSERT INTO schema_snapshots (id, filename, file_id, schema_json, created_at) VALUES (?,?,?,?,?)",
        (_uid(), f["filename"], file_id, json.dumps(schema), _now()),
    )
    conn.commit()


@bp.route("/upload/<file_id>/schema", methods=["GET"])
def get_upload_schema(file_id):
    """Discovers this upload's schema and keeps a snapshot keyed by filename, so
    a later upload of the same filename can be compared with it."""
    conn = get_conn()
    f = _get_file(conn, file_id)
    if not f:
        return _err("File not found.", 404)
    try:
        parsed = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
    except ParseError as e:
        return _err(str(e), 404)
    schema = discover_schema(parsed["columns"], parsed["rows"], parsed.get("geometry"))
    _store_snapshot(conn, f, file_id, schema)
    return jsonify(schema)


@bp.route("/upload/<file_id>/schema/drift", methods=["GET"])
def get_upload_schema_drift(file_id):
    """Compares this upload with the latest earlier snapshot for the same
    filename. It answers 404 when there is nothing to compare against instead of
    reporting "no drift". The comparison also leaves a snapshot for this upload."""
    conn = get_conn()
    f = _get_file(conn, file_id)
    if not f:
        return _err("File not found.", 404)
    try:
        parsed = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
    except ParseError as e:
        return _err(str(e), 404)

    new_schema = discover_schema(parsed["columns"], parsed["rows"], parsed.get("geometry"))
    prior = conn.execute(
        "SELECT schema_json FROM schema_snapshots WHERE filename=? AND file_id!=? ORDER BY created_at DESC LIMIT 1",
        (f["filename"], file_id),
    ).fetchone()
    if not conn.execute("SELECT 1 FROM schema_snapshots WHERE file_id=?", (file_id,)).fetchone():
        _store_snapshot(conn, f, file_id, new_schema)
    if not prior:
        return _err(f"No earlier schema snapshot found for filename '{f['filename']}' to compare against.", 404)
    return jsonify(diff_schema(json.loads(prior["schema_json"]), new_schema))


# Blocking diagnostics and metrics --------------------------------------- #

def _load_project_payloads(p):
    upload_dir = current_app.config["UPLOAD_DIR"]
    return load_parsed_payload(upload_dir, p["source_file_id"]), load_parsed_payload(upload_dir, p["target_file_id"])


@bp.route("/projects/<project_id>/diagnostics/blocking", methods=["GET"])
def project_blocking_diagnostics(project_id):
    conn = get_conn()
    p = _get_project(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    config = json.loads(p["config_json"])
    try:
        source_payload, target_payload = _load_project_payloads(p)
    except ParseError as e:
        return _err(str(e), 404)
    return jsonify(analyze_blocking(
        source_payload["rows"], target_payload["rows"],
        config["source"]["id_column"], config["source"]["match_column"],
        config["target"]["id_column"], config["target"]["match_column"],
        config.get("matching", {}),
    ))


@bp.route("/projects/<project_id>/metrics", methods=["GET"])
def project_metrics(project_id):
    conn = get_conn()
    p = _get_project(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    try:
        source_payload, target_payload = _load_project_payloads(p)
    except ParseError as e:
        return _err(str(e), 404)
    return jsonify(run_metrics(_review_rows(conn, project_id), len(source_payload["rows"]), len(target_payload["rows"])))


@bp.route("/projects/<project_id>/metrics/evaluate", methods=["POST"])
def project_metrics_evaluate(project_id):
    """Precision, recall and F1 against ground truth: {"ground_truth": {sourceId: targetId or null}}.
    Without ground truth the answer says it is unavailable."""
    conn = get_conn()
    if not _get_project(conn, project_id):
        return _err("Project not found.", 404)
    ground_truth = (request.get_json(silent=True) or {}).get("ground_truth")
    return jsonify(evaluation_metrics(_review_rows(conn, project_id), ground_truth))


# Sampling --------------------------------------------------------------- #

_SAMPLE_STRATEGIES = {
    "random": lambda rows, n, seed: random_sample(rows, n, seed),
    "deterministic": lambda rows, n, seed: deterministic_sample(rows, n),
    "lowest_confidence": lambda rows, n, seed: lowest_confidence_sample(rows, n),
    "highest_confidence": lambda rows, n, seed: highest_confidence_sample(rows, n),
    "largest_score_gap": lambda rows, n, seed: largest_score_gap_sample(rows, n),
}


@bp.route("/projects/<project_id>/review/sample", methods=["GET"])
def project_review_sample(project_id):
    conn = get_conn()
    if not _get_project(conn, project_id):
        return _err("Project not found.", 404)

    strategy = request.args.get("strategy", "random")
    n = int_arg("n", 10, lo=1, hi=1000)
    seed = int_arg("seed", None)
    kind = request.args.get("kind")
    rows = _review_rows(conn, project_id, with_name=True)

    if strategy == "by_kind":
        if not kind:
            return _err("strategy=by_kind requires a 'kind' query parameter (for example collision or unmatched).")
        sampled = by_kind_sample(rows, kind, n)
    elif strategy in _SAMPLE_STRATEGIES:
        sampled = _SAMPLE_STRATEGIES[strategy](rows, n, seed)
    else:
        return _err(f"Unknown strategy '{strategy}'. Use one of: {', '.join(list(_SAMPLE_STRATEGIES) + ['by_kind'])}.")
    return jsonify({"strategy": strategy, "seed": seed, "count": len(sampled), "rows": sampled})


# Structured decisions --------------------------------------------------- #

@bp.route("/projects/<project_id>/review/<source_id>/decision", methods=["POST"])
def record_review_decision(project_id, source_id):
    """Records the fuller decision (reason, reviewer, confidence at the time).
    It does not approve or reject anything itself. PATCH .../review/<id> does that."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    body = request.get_json(silent=True) or {}
    decision_type, reason = body.get("decision_type"), body.get("reason")
    reviewer = _actor(body.get("reviewer"))

    if decision_type not in DECISION_TYPES:
        return _err(f"decision_type must be one of {DECISION_TYPES}.")
    if decision_type == "reject" and reason and reason not in REJECTION_REASONS:
        return _err(f"reason must be one of {REJECTION_REASONS}.")

    scored = [m["score"] for k, m in json.loads(row["methods_json"]).items() if k != "D" and m and m.get("score") is not None]
    conn.execute(
        "INSERT INTO review_decision_log (id, project_id, source_id, decision_type, reviewer, target_id, "
        "confidence_at_decision, config_version, reason, note, is_bulk, batch_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_uid(), project_id, source_id, decision_type, reviewer, body.get("target_id"),
         max(scored) if scored else None, body.get("config_version"), reason, body.get("note"),
         int(bool(body.get("is_bulk"))), body.get("batch_id"), _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "structured_decision_recorded",
               {"source_id": source_id, "decision_type": decision_type, "reason": reason}, actor=reviewer)
    return jsonify({"recorded": True}), 201


@bp.route("/projects/<project_id>/review/<source_id>/decision/history", methods=["GET"])
def get_review_decision_history(project_id, source_id):
    rows = get_conn().execute(
        "SELECT * FROM review_decision_log WHERE project_id=? AND source_id=? ORDER BY created_at ASC",
        (project_id, source_id),
    ).fetchall()
    return jsonify({"history": [
        {"decisionType": r["decision_type"], "reviewer": r["reviewer"], "targetId": r["target_id"],
         "confidenceAtDecision": r["confidence_at_decision"], "configVersion": r["config_version"],
         "reason": r["reason"], "note": r["note"], "isBulk": bool(r["is_bulk"]),
         "batchId": r["batch_id"], "createdAt": r["created_at"]}
        for r in rows
    ]})


# Exceptions ------------------------------------------------------------- #

def _exception_to_json(row):
    return {
        "id": row["id"], "scope": row["scope"], "reason": row["reason"], "creator": row["creator"],
        "affectedSourceId": row["affected_source_id"], "affectedTargetId": row["affected_target_id"],
        "affectedFields": json.loads(row["affected_fields_json"]) if row["affected_fields_json"] else [],
        "active": bool(row["active"]), "reviewDate": row["review_date"], "expiresAt": row["expires_at"],
        "history": json.loads(row["history_json"]), "createdAt": row["created_at"],
    }


@bp.route("/projects/<project_id>/exceptions", methods=["GET"])
def list_project_exceptions(project_id):
    conn = get_conn()
    if not _get_project(conn, project_id):
        return _err("Project not found.", 404)
    query = "SELECT * FROM project_exceptions WHERE project_id=?"
    if request.args.get("active_only", "false").lower() == "true":
        query += " AND active=1"
    rows = conn.execute(query + " ORDER BY created_at DESC", (project_id,)).fetchall()
    return jsonify({"exceptions": [_exception_to_json(r) for r in rows]})


@bp.route("/projects/<project_id>/exceptions", methods=["POST"])
def create_project_exception(project_id):
    """Exceptions take effect on the next run. See exceptions_mgmt.py for the
    scopes that change results."""
    conn = get_conn()
    if not _get_project(conn, project_id):
        return _err("Project not found.", 404)
    body = request.get_json(silent=True) or {}
    scope, reason = body.get("scope"), body.get("reason")
    creator = _actor(body.get("creator"))

    if scope not in EXCEPTION_SCOPES:
        return _err(f"scope must be one of {EXCEPTION_SCOPES}.")
    if not reason:
        return _err("'reason' is required.")

    exc_id = _uid()
    conn.execute(
        "INSERT INTO project_exceptions (id, project_id, scope, reason, creator, affected_source_id, "
        "affected_target_id, affected_fields_json, active, review_date, expires_at, history_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (exc_id, project_id, scope, reason, creator, _text_or_none(body.get("affected_source_id")),
         _text_or_none(body.get("affected_target_id")), json.dumps(body.get("affected_fields", [])), 1,
         body.get("review_date"), body.get("expires_at"), "[]", _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "exception_created", {"scope": scope, "reason": reason}, actor=creator)
    return jsonify(_exception_to_json(conn.execute("SELECT * FROM project_exceptions WHERE id=?", (exc_id,)).fetchone())), 201


@bp.route("/projects/<project_id>/exceptions/<exception_id>/deactivate", methods=["POST"])
def deactivate_project_exception(project_id, exception_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM project_exceptions WHERE id=? AND project_id=?", (exception_id, project_id)).fetchone()
    if not row:
        return _err("Exception not found.", 404)
    body = request.get_json(silent=True) or {}
    actor = _actor(body.get("actor"))
    history = json.loads(row["history_json"])
    history.append({"action": "deactivated", "actor": actor, "reason": body.get("reason", ""), "at": _now()})
    conn.execute("UPDATE project_exceptions SET active=0, history_json=? WHERE id=?", (json.dumps(history), exception_id))
    conn.commit()
    _log_audit(conn, project_id, "exception_deactivated", {"exception_id": exception_id}, actor=actor)
    return jsonify(_exception_to_json(conn.execute("SELECT * FROM project_exceptions WHERE id=?", (exception_id,)).fetchone()))


# Storage cleanup: local disk only, preview first, explicit confirm ------- #

@bp.route("/admin/storage", methods=["GET"])
@require_lead_when_auth
def admin_storage_summary():
    return jsonify(storage_usage_summary({"uploads": current_app.config["UPLOAD_DIR"], "data": current_app.config["DATA_DIR"]}))


def _protected_paths(conn, upload_dir):
    """Files a project still needs: the raw upload and its cached parsed copy."""
    protected = set()
    for prj in conn.execute("SELECT source_file_id, target_file_id FROM projects").fetchall():
        for fid in (prj["source_file_id"], prj["target_file_id"]):
            if not fid:
                continue
            frow = conn.execute("SELECT stored_path FROM files WHERE id=?", (fid,)).fetchone()
            if frow:
                protected.add(os.path.realpath(frow["stored_path"]))
            protected.add(os.path.realpath(os.path.join(upload_dir, f"{fid}.parsed.json")))
    return protected


@bp.route("/admin/cleanup/preview", methods=["GET"])
@require_lead_when_auth
def admin_cleanup_preview():
    upload_dir = current_app.config["UPLOAD_DIR"]
    max_age_days = int_arg("max_age_days", 90, lo=0, hi=36500)
    protected = _protected_paths(get_conn(), upload_dir)
    candidates = [c for c in preview_cleanup(upload_dir, max_age_days) if os.path.realpath(c["path"]) not in protected]
    return jsonify({"candidates": candidates, "count": len(candidates), "total_bytes": sum(c["bytes"] for c in candidates)})


@bp.route("/admin/cleanup", methods=["POST"])
@require_lead_when_auth
def admin_cleanup_execute():
    """Deletes exactly the items it is handed (the list from .../cleanup/preview)
    and only with confirm=true. It re-checks every path, since the list comes
    from the client."""
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not items:
        return _err("'items' (the candidate list from GET .../cleanup/preview) is required.")
    if not body.get("confirm"):
        return _err("Refusing to delete without confirm=true. Show the preview to the user first.")

    upload_dir = current_app.config["UPLOAD_DIR"]
    upload_root = os.path.realpath(upload_dir)
    conn = get_conn()
    protected = _protected_paths(conn, upload_dir)
    safe_items = []
    for it in items:
        real = os.path.realpath(str((it or {}).get("path", "")))
        if not real.startswith(upload_root + os.sep):
            return _err(f"Refusing to delete a path outside the upload directory: {(it or {}).get('path')!r}", 400)
        if real in protected:
            return _err(f"Refusing to delete a file an active project still uses: {real!r}", 400)
        if os.path.isfile(real):
            safe_items.append({"path": real, "bytes": os.path.getsize(real)})

    result = delete_items(safe_items, confirm=True)
    # A file with either artifact gone is unusable, so drop its record too.
    for path in result.get("deleted", []):
        conn.execute("DELETE FROM files WHERE id=?", (os.path.basename(path).split(".")[0],))

    actor = _actor(body.get("actor"))
    entry = cleanup_audit_entry(actor, "manual_admin_cleanup", safe_items, confirmed=True)
    conn.execute(
        "INSERT INTO audit_log (id, project_id, actor, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (_uid(), "system", entry["actor"], entry["action"], json.dumps(entry["detail"]), entry["created_at"]),
    )
    conn.commit()
    return jsonify(result)


# Connector capabilities ------------------------------------------------- #

@bp.route("/connectors/capabilities", methods=["GET"])
def connector_capabilities():
    return jsonify(list_capabilities())


# Explanation: why did Relink produce this row? -------------------------- #

@bp.route("/projects/<project_id>/review/<source_id>/explain", methods=["GET"])
def explain_review_row_route(project_id, source_id):
    conn = get_conn()
    p = _get_project(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    config = json.loads(p["config_json"])
    try:
        source_payload, target_payload = _load_project_payloads(p)
    except ParseError as e:
        return _err(str(e), 404)

    src_id_col, src_match_col = config["source"]["id_column"], config["source"]["match_column"]
    tgt_id_col, tgt_match_col = config["target"]["id_column"], config["target"]["match_column"]

    source_row = next((r for r in source_payload["rows"] if str(r.get(src_id_col)) == source_id), None)
    if source_row is None:
        return _err(f"Source row '{source_id}' not found in the cached source file.", 404)

    methods_json = json.loads(row["methods_json"])
    wanted = {str(m["targetId"]) for m in methods_json.values() if m and m.get("targetId")}
    target_values_by_id = {str(t.get(tgt_id_col)): t.get(tgt_match_col)
                           for t in target_payload["rows"] if str(t.get(tgt_id_col)) in wanted}

    row_dict = {"sourceId": row["source_id"], "kind": row["kind"], "approved": bool(row["approved"]),
                "chosenMethod": row["chosen_method"], "collisionPartner": row["collision_partner"], "methods": methods_json}
    return jsonify(explain_review_row(
        row_dict, source_row.get(src_match_col), target_values_by_id,
        config.get("matching", {}), config.get("thresholds", {}), config.get("safety", {}),
    ))
