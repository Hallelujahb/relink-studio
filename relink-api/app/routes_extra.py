"""
Batch 8: wires the standalone modules from batches 1-7 (profiling,
schema_tools, blocking_diagnostics, metrics, sampling, exceptions_mgmt,
review_decisions, retention, connectors) into real HTTP endpoints.

Deliberately a SEPARATE blueprint from routes.py rather than edits to it:
routes.py is actively being worked on directly in the repo, and keeping
this wiring in its own file means applying it can never corrupt or
conflict with routes.py's content. Registered in app/__init__.py:

    from .routes_extra import bp as api_bp_extra
    app.register_blueprint(api_bp_extra, url_prefix="/api")

All endpoints here reuse the same DB connection (app.db.get_conn),
upload directory, and error-response shape ({"error": "..."}) as
routes.py, so they behave consistently with the rest of the API.
"""
import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from .db import get_conn
from .parsers import load_parsed_payload, ParseError
from .profiling import profile_dataset, identifier_warnings
from .schema_tools import discover_schema, diff_schema
from .blocking_diagnostics import analyze_blocking
from .metrics import run_metrics, evaluation_metrics
from .sampling import (
    random_sample, deterministic_sample, lowest_confidence_sample,
    highest_confidence_sample, largest_score_gap_sample, by_kind_sample,
)
from .exceptions_mgmt import EXCEPTION_SCOPES
from .review_decisions import REJECTION_REASONS, DECISION_TYPES
from .retention import storage_usage_summary, preview_cleanup, delete_items, cleanup_audit_entry
from .connectors import list_capabilities
from .explainability import explain_review_row
from .auth import actor_name

bp = Blueprint("api_extra", __name__)


def _uid():
    return uuid.uuid4().hex[:12]


def _now():
    return datetime.utcnow().isoformat()


def _err(message, status=400):
    return jsonify({"error": message}), status


def _log_audit(conn, project_id, action, detail=None, actor=None):
    if actor is None:
        actor = actor_name()
    conn.execute(
        "INSERT INTO audit_log (id, project_id, actor, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (_uid(), project_id, actor, action, json.dumps(detail) if detail is not None else None, _now()),
    )
    conn.commit()


def _get_file_or_404(conn, file_id):
    return conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()


def _get_project_or_404(conn, project_id):
    return conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


# --------------------------------------------------------------------- #
# Data profiling (section 3)
# --------------------------------------------------------------------- #

@bp.route("/upload/<file_id>/profile", methods=["GET"])
def profile_upload(file_id):
    conn = get_conn()
    f = _get_file_or_404(conn, file_id)
    if not f:
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


# --------------------------------------------------------------------- #
# Schema discovery + drift detection (section 2)
# --------------------------------------------------------------------- #

@bp.route("/upload/<file_id>/schema", methods=["GET"])
def get_upload_schema(file_id):
    """Discovers the schema for this upload and stores a snapshot keyed by
    filename, so a later re-upload of "the same" source (by filename) can
    be diffed against it via .../schema/drift below."""
    conn = get_conn()
    f = _get_file_or_404(conn, file_id)
    if not f:
        return _err("File not found.", 404)
    try:
        parsed = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
    except ParseError as e:
        return _err(str(e), 404)

    schema = discover_schema(parsed["columns"], parsed["rows"], parsed.get("geometry"))
    conn.execute(
        "INSERT INTO schema_snapshots (id, filename, file_id, schema_json, created_at) VALUES (?,?,?,?,?)",
        (_uid(), f["filename"], file_id, json.dumps(schema), _now()),
    )
    conn.commit()
    return jsonify(schema)


@bp.route("/upload/<file_id>/schema/drift", methods=["GET"])
def get_upload_schema_drift(file_id):
    """Compares this file's schema against the most recent PRIOR snapshot
    for the same filename (i.e. the last time a file with this name was
    uploaded and schema'd). 404s clearly if there's no prior snapshot to
    compare against, rather than silently reporting "no drift"."""
    conn = get_conn()
    f = _get_file_or_404(conn, file_id)
    if not f:
        return _err("File not found.", 404)
    try:
        parsed = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
    except ParseError as e:
        return _err(str(e), 404)

    new_schema = discover_schema(parsed["columns"], parsed["rows"], parsed.get("geometry"))
    prior = conn.execute(
        "SELECT schema_json FROM schema_snapshots WHERE filename=? AND file_id!=? "
        "ORDER BY created_at DESC LIMIT 1",
        (f["filename"], file_id),
    ).fetchone()
    if not prior:
        return _err(f"No prior schema snapshot found for filename '{f['filename']}' to compare against.", 404)

    old_schema = json.loads(prior["schema_json"])
    return jsonify(diff_schema(old_schema, new_schema))


# --------------------------------------------------------------------- #
# Blocking / candidate-generation diagnostics (section 7)
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/diagnostics/blocking", methods=["GET"])
def project_blocking_diagnostics(project_id):
    conn = get_conn()
    p = _get_project_or_404(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    config = json.loads(p["config_json"])
    upload_dir = current_app.config["UPLOAD_DIR"]
    try:
        source_payload = load_parsed_payload(upload_dir, p["source_file_id"])
        target_payload = load_parsed_payload(upload_dir, p["target_file_id"])
    except ParseError as e:
        return _err(str(e), 404)

    result = analyze_blocking(
        source_payload["rows"], target_payload["rows"],
        config["source"]["id_column"], config["source"]["match_column"],
        config["target"]["id_column"], config["target"]["match_column"],
        config.get("matching", {}),
    )
    return jsonify(result)


# --------------------------------------------------------------------- #
# Match-quality metrics (section 8)
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/metrics", methods=["GET"])
def project_metrics(project_id):
    conn = get_conn()
    p = _get_project_or_404(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    upload_dir = current_app.config["UPLOAD_DIR"]
    try:
        source_payload = load_parsed_payload(upload_dir, p["source_file_id"])
        target_payload = load_parsed_payload(upload_dir, p["target_file_id"])
    except ParseError as e:
        return _err(str(e), 404)

    rows = conn.execute("SELECT * FROM review_rows WHERE project_id=?", (project_id,)).fetchall()
    review_rows = [
        {"sourceId": r["source_id"], "approved": bool(r["approved"]), "kind": r["kind"],
         "methods": json.loads(r["methods_json"])}
        for r in rows
    ]
    result = run_metrics(review_rows, len(source_payload["rows"]), len(target_payload["rows"]))
    return jsonify(result)


@bp.route("/projects/<project_id>/metrics/evaluate", methods=["POST"])
def project_metrics_evaluate(project_id):
    """Ground-truth-gated precision/recall/F1. Body: {"ground_truth":
    {sourceId: expected_target_id_or_null, ...}}. Returns an explicit
    'unavailable' result (not a fabricated score) if omitted."""
    conn = get_conn()
    p = _get_project_or_404(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    body = request.get_json(silent=True) or {}
    ground_truth = body.get("ground_truth")

    rows = conn.execute("SELECT * FROM review_rows WHERE project_id=?", (project_id,)).fetchall()
    review_rows = [
        {"sourceId": r["source_id"], "approved": bool(r["approved"]), "kind": r["kind"],
         "methods": json.loads(r["methods_json"])}
        for r in rows
    ]
    return jsonify(evaluation_metrics(review_rows, ground_truth))


# --------------------------------------------------------------------- #
# Sampling / QC workflows (section 9)
# --------------------------------------------------------------------- #

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
    if not _get_project_or_404(conn, project_id):
        return _err("Project not found.", 404)

    strategy = request.args.get("strategy", "random")
    n = int(request.args.get("n", 10))
    seed = request.args.get("seed")
    seed = int(seed) if seed is not None else None
    kind = request.args.get("kind")  # for strategy=by_kind

    rows = conn.execute("SELECT * FROM review_rows WHERE project_id=?", (project_id,)).fetchall()
    review_rows = [
        {"sourceId": r["source_id"], "sourceName": r["source_name"], "approved": bool(r["approved"]),
         "kind": r["kind"], "methods": json.loads(r["methods_json"])}
        for r in rows
    ]

    if strategy == "by_kind":
        if not kind:
            return _err("strategy=by_kind requires a 'kind' query parameter (e.g. collision, unmatched).")
        sampled = by_kind_sample(review_rows, kind, n)
    elif strategy in _SAMPLE_STRATEGIES:
        sampled = _SAMPLE_STRATEGIES[strategy](review_rows, n, seed)
    else:
        return _err(f"Unknown strategy '{strategy}'. Use one of: {', '.join(list(_SAMPLE_STRATEGIES) + ['by_kind'])}.")

    return jsonify({"strategy": strategy, "seed": seed, "count": len(sampled), "rows": sampled})


# --------------------------------------------------------------------- #
# Structured review decisions (section 10)
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/review/<source_id>/decision", methods=["POST"])
def record_review_decision(project_id, source_id):
    """Supplementary structured logging alongside the existing lightweight
    PATCH .../review/<id> in routes.py -- that endpoint still does the
    actual approve/reject; this one records the richer decision record
    (reason, reviewer, confidence-at-decision) for audit/reporting. Call
    both, or call this first and then PATCH."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, source_id)
    ).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    body = request.get_json(silent=True) or {}
    decision_type = body.get("decision_type")
    reviewer = body.get("reviewer") or actor_name()
    reason = body.get("reason")

    if decision_type not in DECISION_TYPES:
        return _err(f"decision_type must be one of {DECISION_TYPES}.")
    if decision_type == "reject" and reason and reason not in REJECTION_REASONS:
        return _err(f"reason must be one of {REJECTION_REASONS}.")

    methods = json.loads(row["methods_json"])
    scored = [m["score"] for m in methods.values() if m and m.get("score") is not None]
    confidence = max(scored) if scored else None

    conn.execute(
        "INSERT INTO review_decision_log (id, project_id, source_id, decision_type, reviewer, target_id, "
        "confidence_at_decision, config_version, reason, note, is_bulk, batch_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_uid(), project_id, source_id, decision_type, reviewer, body.get("target_id"),
         confidence, body.get("config_version"), reason, body.get("note"),
         int(bool(body.get("is_bulk"))), body.get("batch_id"), _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "structured_decision_recorded",
               {"source_id": source_id, "decision_type": decision_type, "reason": reason}, actor=reviewer)
    return jsonify({"recorded": True}), 201


@bp.route("/projects/<project_id>/review/<source_id>/decision/history", methods=["GET"])
def get_review_decision_history(project_id, source_id):
    conn = get_conn()
    rows = conn.execute(
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


# --------------------------------------------------------------------- #
# Exception / policy management (section 11)
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/exceptions", methods=["GET"])
def list_project_exceptions(project_id):
    conn = get_conn()
    if not _get_project_or_404(conn, project_id):
        return _err("Project not found.", 404)
    active_only = request.args.get("active_only", "false").lower() == "true"
    query = "SELECT * FROM project_exceptions WHERE project_id=?"
    params = [project_id]
    if active_only:
        query += " AND active=1"
    rows = conn.execute(query + " ORDER BY created_at DESC", params).fetchall()
    return jsonify({"exceptions": [_exception_to_json(r) for r in rows]})


@bp.route("/projects/<project_id>/exceptions", methods=["POST"])
def create_project_exception(project_id):
    conn = get_conn()
    if not _get_project_or_404(conn, project_id):
        return _err("Project not found.", 404)
    body = request.get_json(silent=True) or {}
    scope = body.get("scope")
    reason = body.get("reason")
    creator = body.get("creator") or actor_name()

    if scope not in EXCEPTION_SCOPES:
        return _err(f"scope must be one of {EXCEPTION_SCOPES}.")
    if not reason:
        return _err("'reason' is required.")

    exc_id = _uid()
    conn.execute(
        "INSERT INTO project_exceptions (id, project_id, scope, reason, creator, affected_source_id, "
        "affected_target_id, affected_fields_json, active, review_date, expires_at, history_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (exc_id, project_id, scope, reason, creator, body.get("affected_source_id"),
         body.get("affected_target_id"), json.dumps(body.get("affected_fields", [])), 1,
         body.get("review_date"), body.get("expires_at"), "[]", _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "exception_created", {"scope": scope, "reason": reason}, actor=creator)
    row = conn.execute("SELECT * FROM project_exceptions WHERE id=?", (exc_id,)).fetchone()
    return jsonify(_exception_to_json(row)), 201


@bp.route("/projects/<project_id>/exceptions/<exception_id>/deactivate", methods=["POST"])
def deactivate_project_exception(project_id, exception_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM project_exceptions WHERE id=? AND project_id=?", (exception_id, project_id)
    ).fetchone()
    if not row:
        return _err("Exception not found.", 404)
    body = request.get_json(silent=True) or {}
    actor = body.get("actor") or actor_name()
    history = json.loads(row["history_json"])
    history.append({"action": "deactivated", "actor": actor, "reason": body.get("reason", ""), "at": _now()})
    conn.execute(
        "UPDATE project_exceptions SET active=0, history_json=? WHERE id=?",
        (json.dumps(history), exception_id),
    )
    conn.commit()
    _log_audit(conn, project_id, "exception_deactivated", {"exception_id": exception_id}, actor=actor)
    updated = conn.execute("SELECT * FROM project_exceptions WHERE id=?", (exception_id,)).fetchone()
    return jsonify(_exception_to_json(updated))


def _exception_to_json(row):
    return {
        "id": row["id"], "scope": row["scope"], "reason": row["reason"], "creator": row["creator"],
        "affectedSourceId": row["affected_source_id"], "affectedTargetId": row["affected_target_id"],
        "affectedFields": json.loads(row["affected_fields_json"]) if row["affected_fields_json"] else [],
        "active": bool(row["active"]), "reviewDate": row["review_date"], "expiresAt": row["expires_at"],
        "history": json.loads(row["history_json"]), "createdAt": row["created_at"],
    }


# --------------------------------------------------------------------- #
# Retention / cleanup (section 15) -- local disk only, always previews
# before deleting, requires explicit confirm.
# --------------------------------------------------------------------- #

@bp.route("/admin/storage", methods=["GET"])
def admin_storage_summary():
    upload_dir = current_app.config["UPLOAD_DIR"]
    data_dir = current_app.config["DATA_DIR"]
    return jsonify(storage_usage_summary({"uploads": upload_dir, "data": data_dir}))


@bp.route("/admin/cleanup/preview", methods=["GET"])
def admin_cleanup_preview():
    upload_dir = current_app.config["UPLOAD_DIR"]
    max_age_days = int(request.args.get("max_age_days", 90))

    conn = get_conn()
    referenced = set()
    for p in conn.execute("SELECT source_file_id, target_file_id FROM projects").fetchall():
        for fid in (p["source_file_id"], p["target_file_id"]):
            if fid:
                f = conn.execute("SELECT stored_path FROM files WHERE id=?", (fid,)).fetchone()
                if f:
                    referenced.add(f["stored_path"])
                    # The cached parsed payload is just as load-bearing for an
                    # active project as the raw upload -- deleting it would
                    # break re-running the pipeline even though the original
                    # file is untouched, so it needs the same protection.
                    referenced.add(os.path.join(upload_dir, f"{fid}.parsed.json"))

    candidates = preview_cleanup(upload_dir, max_age_days, protected_paths=referenced)
    return jsonify({"candidates": candidates, "count": len(candidates),
                     "total_bytes": sum(c["bytes"] for c in candidates)})


@bp.route("/admin/cleanup", methods=["POST"])
def admin_cleanup_execute():
    """Requires the caller to have already seen the preview and pass the
    exact candidate list back with confirm=true -- this endpoint never
    re-derives "old files" itself and deletes them; it only deletes
    exactly what's handed to it, and only when explicitly confirmed."""
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    confirm = bool(body.get("confirm"))
    actor = body.get("actor") or actor_name()

    if not items:
        return _err("'items' (the candidate list from GET .../cleanup/preview) is required.")
    if not confirm:
        return _err("Refusing to delete without confirm=true. Show the preview to the user first.")

    result = delete_items(items, confirm=True)
    conn = get_conn()
    entry = cleanup_audit_entry(actor, "manual_admin_cleanup", items, confirmed=True)
    conn.execute(
        "INSERT INTO audit_log (id, project_id, actor, action, detail, created_at) VALUES (?,?,?,?,?,?)",
        (_uid(), "system", entry["actor"], entry["action"], json.dumps(entry["detail"]), entry["created_at"]),
    )
    conn.commit()
    return jsonify(result)


# --------------------------------------------------------------------- #
# Connector capabilities (section 13)
# --------------------------------------------------------------------- #

@bp.route("/connectors/capabilities", methods=["GET"])
def connector_capabilities():
    return jsonify(list_capabilities())


# --------------------------------------------------------------------- #
# Matching explainability (section 3): "why did ReLink make this match?"
# Re-derives the explanation from the matcher's own logic (normalize(),
# _blocking_key()) rather than inventing one after the fact.
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/review/<source_id>/explain", methods=["GET"])
def explain_review_row_route(project_id, source_id):
    conn = get_conn()
    p = _get_project_or_404(conn, project_id)
    if not p:
        return _err("Project not found.", 404)
    row = conn.execute(
        "SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, source_id)
    ).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    config = json.loads(p["config_json"])
    upload_dir = current_app.config["UPLOAD_DIR"]
    try:
        source_payload = load_parsed_payload(upload_dir, p["source_file_id"])
        target_payload = load_parsed_payload(upload_dir, p["target_file_id"])
    except ParseError as e:
        return _err(str(e), 404)

    src_id_col = config["source"]["id_column"]
    src_match_col = config["source"]["match_column"]
    tgt_id_col = config["target"]["id_column"]
    tgt_match_col = config["target"]["match_column"]

    source_row = next((r for r in source_payload["rows"] if str(r.get(src_id_col)) == source_id), None)
    if source_row is None:
        return _err(f"Source row '{source_id}' not found in the cached source file.", 404)

    methods_json = json.loads(row["methods_json"])
    wanted_target_ids = {m["targetId"] for m in methods_json.values() if m and m.get("targetId")}
    target_values_by_id = {
        t.get(tgt_id_col): t.get(tgt_match_col)
        for t in target_payload["rows"]
        if t.get(tgt_id_col) in wanted_target_ids
    }

    row_dict = {
        "sourceId": row["source_id"], "kind": row["kind"], "approved": bool(row["approved"]),
        "chosenMethod": row["chosen_method"], "collisionPartner": row["collision_partner"],
        "methods": methods_json,
    }
    result = explain_review_row(
        row_dict, source_row.get(src_match_col), target_values_by_id,
        config.get("matching", {}), config.get("thresholds", {}), config.get("safety", {}),
    )
    return jsonify(result)
