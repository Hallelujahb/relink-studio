import csv
import io
import json
import os
import threading
import time
import uuid
from datetime import datetime

import requests
from flask import Blueprint, current_app, jsonify, request, send_file, Response, stream_with_context

from . import matching
from . import auth as auth_module
from .auth import require_auth, actor_name
from .db import get_conn
from .parsers import ParseError, parse_file, save_parsed_payload, load_parsed_payload
from .matching_splink import method_e_splink, SplinkUnavailable
from .matching_linktransformer import method_c_linktransformer, LinkTransformerUnavailable

bp = Blueprint("api", __name__)


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


# --------------------------------------------------------------------- #
# Nominatim rate limiting (their usage policy: max 1 request/sec, no bulk
# geocoding, identify your app via User-Agent). This is process-global,
# not per-request, on purpose -- it's a shared external quota.
# --------------------------------------------------------------------- #
_nominatim_lock = threading.Lock()
_nominatim_last_call = 0.0
_NOMINATIM_MIN_INTERVAL_SECONDS = 1.1
_NOMINATIM_USER_AGENT = os.environ.get(
    "RELINK_NOMINATIM_USER_AGENT", "relink-studio/0.1 (set RELINK_NOMINATIM_USER_AGENT to your contact info)"
)


def _row_to_json(row):
    return {
        "sourceId": row["source_id"],
        "sourceName": row["source_name"],
        "kind": row["kind"],
        "approved": bool(row["approved"]),
        "chosenMethod": row["chosen_method"],
        "collisionPartner": row["collision_partner"],
        "methods": json.loads(row["methods_json"]),
        "note": row["note"],
        "submittedBy": row["submitted_by"],
        "approvedBy": row["approved_by"],
    }


# --------------------------------------------------------------------- #
# Auth (section 5). Optional: enforced only where @require_auth(role=...)
# is used, or everywhere once RELINK_REQUIRE_AUTH=1 is set. See auth.py.
# --------------------------------------------------------------------- #

@bp.route("/auth/register", methods=["POST"])
def auth_register():
    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not username or not password:
        return _err("Missing 'username' and/or 'password'.")
    if len(password) < 8:
        return _err("Password must be at least 8 characters.")
    try:
        user = auth_module.register_user(username, password, body.get("role", "reviewer"))
    except ValueError as e:
        return _err(str(e))
    return jsonify(user), 201


@bp.route("/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not username or not password:
        return _err("Missing 'username' and/or 'password'.")
    session = auth_module.login(username, password)
    if not session:
        return _err("Invalid username or password.", 401)
    return jsonify(session)


# --------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------- #

_ALLOWED_UPLOAD_EXTENSIONS = {"csv", "xlsx", "xls", "geojson", "json"}


@bp.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return _err("No file part in the request (expected multipart field 'file').")
    f = request.files["file"]
    if not f.filename:
        return _err("No file selected.")

    # Validate before touching disk: extension whitelist (defense in depth
    # alongside parse_file's own check, which only runs after saving).
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return _err(f"Unsupported file type: .{ext}. Use .csv, .xlsx, .xls, .geojson, or .json.")

    # Belt-and-suspenders size check. MAX_CONTENT_LENGTH already rejects
    # oversized request bodies at the Flask/Werkzeug layer (see 413
    # handler in app/__init__.py); this catches a zero-byte file, which
    # slips through that check but isn't useful to store or parse.
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size == 0:
        return _err("Uploaded file is empty.")

    file_id = _uid()
    stored_path = os.path.join(current_app.config["UPLOAD_DIR"], f"{file_id}.{ext}")
    f.save(stored_path)

    try:
        parsed = parse_file(stored_path, f.filename)
    except ParseError as e:
        os.remove(stored_path)
        return _err(str(e))

    save_parsed_payload(current_app.config["UPLOAD_DIR"], file_id, parsed)

    conn = get_conn()
    conn.execute(
        "INSERT INTO files (id, filename, stored_path, columns_json, row_count, format, has_geometry, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (file_id, f.filename, stored_path, json.dumps(parsed["columns"]), parsed["row_count"],
         parsed["format"], 1 if parsed.get("geometry") else 0, _now()),
    )
    conn.commit()

    return jsonify({
        "file_id": file_id,
        "filename": f.filename,
        "columns": parsed["columns"],
        "row_count": parsed["row_count"],
        "format": parsed["format"],
        "has_geometry": bool(parsed.get("geometry")),
    }), 201


@bp.route("/upload/<file_id>", methods=["DELETE"])
def delete_upload(file_id):
    conn = get_conn()
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        return _err("File not found.", 404)
    in_use = conn.execute(
        "SELECT 1 FROM projects WHERE source_file_id=? OR target_file_id=?", (file_id, file_id)
    ).fetchone()
    if in_use:
        return _err("File is referenced by a project; delete the project first, or leave it for automatic expiry.", 409)
    if os.path.exists(f["stored_path"]):
        os.remove(f["stored_path"])
    parsed_path = os.path.join(current_app.config["UPLOAD_DIR"], f"{file_id}.parsed.json")
    if os.path.exists(parsed_path):
        os.remove(parsed_path)
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()
    return "", 204


# --------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------- #

@bp.route("/projects", methods=["POST"])
def create_project():
    body = request.get_json(silent=True) or {}
    config = body.get("config")
    source_file_id = body.get("source_file_id")
    target_file_id = body.get("target_file_id")

    if not config:
        return _err("Missing 'config' (the project config object, matching the frontend's config JSON shape).")
    if not source_file_id or not target_file_id:
        return _err("Missing 'source_file_id' and/or 'target_file_id' (returned by POST /api/upload).")

    conn = get_conn()
    for fid, label in ((source_file_id, "source_file_id"), (target_file_id, "target_file_id")):
        if not conn.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
            return _err(f"Unknown {label}: {fid}. Upload it first via POST /api/upload.")

    project_id = _uid()
    name = config.get("project_name", "untitled_project")
    conn.execute(
        "INSERT INTO projects (id, name, config_json, source_file_id, target_file_id, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (project_id, name, json.dumps(config), source_file_id, target_file_id, "draft", _now(), _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "project_created", {"name": name})

    return jsonify({"id": project_id, "name": name, "status": "draft"}), 201


@bp.route("/projects/<project_id>", methods=["GET"])
def get_project(project_id):
    conn = get_conn()
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)
    return jsonify({
        "id": p["id"], "name": p["name"], "status": p["status"],
        "config": json.loads(p["config_json"]),
        "source_file_id": p["source_file_id"], "target_file_id": p["target_file_id"],
        "created_at": p["created_at"], "updated_at": p["updated_at"],
    })


@bp.route("/projects/<project_id>/config", methods=["GET"])
def get_config(project_id):
    conn = get_conn()
    p = conn.execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)
    return jsonify(json.loads(p["config_json"]))


@bp.route("/projects/<project_id>/config", methods=["POST"])
def update_config(project_id):
    conn = get_conn()
    p = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)
    config = request.get_json(silent=True)
    if not config:
        return _err("Missing JSON body (the config object).")
    conn.execute("UPDATE projects SET config_json=?, updated_at=? WHERE id=?", (json.dumps(config), _now(), project_id))
    conn.commit()
    _log_audit(conn, project_id, "config_updated")
    return jsonify(config)


# --------------------------------------------------------------------- #
# Run (background matching job)
# --------------------------------------------------------------------- #

def _run_matching_job(app, project_id, job_id, triggered_by):
    with app.app_context():
        conn = get_conn()

        def set_job(status, progress=None, total=None, message=None):
            fields, params = ["status=?", "updated_at=?"], [status, _now()]
            if progress is not None:
                fields.append("progress=?"); params.append(progress)
            if total is not None:
                fields.append("total=?"); params.append(total)
            if message is not None:
                fields.append("message=?"); params.append(message)
            params.append(job_id)
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", params)
            conn.commit()

        try:
            p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            config = json.loads(p["config_json"])
            upload_dir = app.config["UPLOAD_DIR"]

            source_payload = load_parsed_payload(upload_dir, p["source_file_id"])
            target_payload = load_parsed_payload(upload_dir, p["target_file_id"])
            source_rows, target_rows = source_payload["rows"], target_payload["rows"]

            set_job("running", progress=0, total=len(source_rows), message="Matching in progress")

            src_id_col = config["source"]["id_column"]
            src_match_col = config["source"]["match_column"]
            src_name_col = src_match_col
            tgt_id_col = config["target"]["id_column"]
            tgt_match_col = config["target"]["match_column"]
            tgt_type_col = config["target"].get("type_column")
            tgt_type_expected = config["target"].get("type_expected")

            matching_cfg = config.get("matching", {})
            blocking_floor = matching_cfg.get("blocking_floor", 0.5)
            methods_enabled = set(config.get("methods", []))

            method_results = {}
            if "fuzzy" in methods_enabled:
                method_results["A"] = matching.method_a_fuzzy(
                    source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                    matching_cfg, blocking_floor)
            if "recordlinkage" in methods_enabled:
                method_results["B"] = matching.method_b_token_overlap(
                    source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                    matching_cfg, blocking_floor)
            method_meta = {}
            if "linktransformer" in methods_enabled:
                try:
                    method_results["C"] = method_c_linktransformer(
                        source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                        matching_cfg, blocking_floor)
                except LinkTransformerUnavailable as e:
                    method_results["C"] = {}
                    method_meta["C"] = {"error": str(e)}
            if "splink" in methods_enabled:
                try:
                    method_results["E"], splink_meta = method_e_splink(
                        source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                        matching_cfg, blocking_floor)
                    method_meta["E"] = splink_meta
                except SplinkUnavailable as e:
                    method_results["E"] = {}
                    method_meta["E"] = {"error": str(e)}

            rows = matching.combine_methods(
                source_rows, src_id_col, src_name_col, method_results,
                target_rows, tgt_id_col, tgt_type_col, tgt_type_expected, config)

            if "geometry_corroboration" in methods_enabled and source_payload.get("geometry") and target_payload.get("geometry"):
                src_geom, tgt_geom = source_payload["geometry"], target_payload["geometry"]
                tgt_index_by_id = {t.get(tgt_id_col): i for i, t in enumerate(target_rows)}
                src_index_by_id = {s.get(src_id_col): i for i, s in enumerate(source_rows)}
                candidate_tgt_idx_by_src_idx = {}
                for r in rows:
                    best = next((m for m in r["methods"].values() if m["targetId"] is not None), None)
                    src_idx = src_index_by_id.get(r["sourceId"])
                    if best and src_idx is not None:
                        candidate_tgt_idx_by_src_idx[src_idx] = tgt_index_by_id.get(best["targetId"])
                geo_flags = matching.method_d_geometry_corroboration(src_geom, tgt_geom, candidate_tgt_idx_by_src_idx)
                for r in rows:
                    src_idx = src_index_by_id.get(r["sourceId"])
                    flag = geo_flags.get(src_idx)
                    if flag and flag["suspect"]:
                        r["kind"] = "geometry_suspect" if r["kind"] == "consensus" else r["kind"]
                        r["approved"] = False
                    r["methods"]["D"] = {
                        "name": None, "targetId": None,
                        "score": None if not flag else (1.0 - min(flag["distance_km"] / 100, 1.0) if flag["distance_km"] is not None else None),
                    }

            conn.execute("DELETE FROM review_rows WHERE project_id=?", (project_id,))
            for i, r in enumerate(rows):
                conn.execute(
                    "INSERT INTO review_rows (id, project_id, source_id, source_name, kind, approved, "
                    "chosen_method, collision_partner, methods_json, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (_uid(), project_id, str(r["sourceId"]), r["sourceName"], r["kind"], int(r["approved"]),
                     r["chosenMethod"], r["collisionPartner"], json.dumps(r["methods"]), _now(), _now()),
                )
                if i % 200 == 0:
                    set_job("running", progress=i, total=len(rows))
            conn.commit()

            conn.execute("UPDATE projects SET status='matched', updated_at=? WHERE id=?", (_now(), project_id))
            conn.commit()
            set_job("done", progress=len(rows), total=len(rows), message=f"Matched {len(rows)} row(s).")
            _log_audit(conn, project_id, "run_completed",
                       {"row_count": len(rows), "methods": list(methods_enabled), "method_notes": method_meta},
                       actor=triggered_by)

        except Exception as e:
            set_job("error", message=str(e))
            _log_audit(conn, project_id, "run_failed", {"error": str(e)}, actor=triggered_by)


@bp.route("/projects/<project_id>/run", methods=["POST"])
def run_project(project_id):
    conn = get_conn()
    p = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)

    job_id = _uid()
    conn.execute(
        "INSERT INTO jobs (id, project_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (job_id, project_id, "pending", _now(), _now()),
    )
    conn.commit()
    triggered_by = actor_name()  # capture now -- the background thread has no request context to read this from later
    _log_audit(conn, project_id, "run_started", {"job_id": job_id}, actor=triggered_by)

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_matching_job, args=(app, project_id, job_id, triggered_by), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202


@bp.route("/projects/<project_id>/jobs/<job_id>", methods=["GET"])
def job_status(project_id, job_id):
    conn = get_conn()
    j = conn.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
    if not j:
        return _err("Job not found.", 404)
    return jsonify({
        "job_id": j["id"], "status": j["status"], "progress": j["progress"],
        "total": j["total"], "message": j["message"], "updated_at": j["updated_at"],
    })


@bp.route("/projects/<project_id>/jobs/<job_id>/stream", methods=["GET"])
def job_status_stream(project_id, job_id):
    """Server-Sent Events version of job_status, for the frontend's
    'Execute pipeline' button to show real live progress instead of the
    current fake toast. Polls the DB every 0.5s server-side and pushes an
    event only when something changed; closes the stream once the job
    reaches a terminal state (done/error) or after a 5-minute safety cap."""
    app = current_app._get_current_object()

    def event_stream():
        with app.app_context():
            conn = get_conn()
            last_payload = None
            started = time.time()
            while time.time() - started < 300:
                j = conn.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
                if not j:
                    yield "event: error\ndata: {\"error\": \"Job not found.\"}\n\n"
                    return
                payload = json.dumps({
                    "job_id": j["id"], "status": j["status"], "progress": j["progress"],
                    "total": j["total"], "message": j["message"],
                })
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                if j["status"] in ("done", "error"):
                    return
                time.sleep(0.5)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------- #
# Review queue
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/review", methods=["GET"])
def get_review_queue(project_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)

    filt = request.args.get("filter", "all")
    search = (request.args.get("search") or "").strip().lower()
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(500, max(1, int(request.args.get("page_size", 50))))

    where, params = ["project_id=?"], [project_id]
    if filt == "approved":
        where.append("approved=1")
    elif filt not in ("all", ""):
        where.append("kind=?"); params.append(filt)
    if search:
        where.append("(LOWER(source_name) LIKE ? OR LOWER(source_id) LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) c FROM review_rows WHERE {where_sql}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM review_rows WHERE {where_sql} ORDER BY CAST(source_id AS INTEGER), source_id "
        f"LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    return jsonify({
        "rows": [_row_to_json(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


@bp.route("/projects/<project_id>/review/<row_source_id>", methods=["PATCH"])
def patch_review_row(project_id, row_source_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)
    ).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    body = request.get_json(silent=True) or {}
    previous_state = _row_to_json(row)

    approved = body.get("approved", bool(row["approved"]))
    chosen_method = body.get("chosen_method", row["chosen_method"])
    note = body.get("note", row["note"])

    conn.execute(
        "UPDATE review_rows SET approved=?, chosen_method=?, note=?, updated_at=? WHERE id=?",
        (int(bool(approved)), chosen_method, note, _now(), row["id"]),
    )
    undo_token = _uid()
    conn.execute(
        "INSERT INTO decisions (id, project_id, row_id, undo_token, previous_state_json, created_at) VALUES (?,?,?,?,?,?)",
        (_uid(), project_id, row["id"], undo_token, json.dumps(previous_state), _now()),
    )
    conn.commit()
    _log_audit(conn, project_id, "review_row_updated", {"source_id": row_source_id, "approved": approved, "chosen_method": chosen_method})

    updated = conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()
    return jsonify({"row": _row_to_json(updated), "undo_token": undo_token})


@bp.route("/projects/<project_id>/review/<row_source_id>/submit", methods=["POST"])
@require_auth()
def submit_review_row(project_id, row_source_id):
    """Step 1 of the optional approval hierarchy: any authenticated user
    marks a row as submitted. Does NOT set approved -- see .../approve."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)
    conn.execute(
        "UPDATE review_rows SET submitted_by=?, submitted_at=?, updated_at=? WHERE id=?",
        (actor_name(), _now(), _now(), row["id"]),
    )
    conn.commit()
    _log_audit(conn, project_id, "review_row_submitted", {"source_id": row_source_id})
    updated = conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()
    return jsonify(_row_to_json(updated))


@bp.route("/projects/<project_id>/review/<row_source_id>/approve", methods=["POST"])
@require_auth(role="lead")
def approve_review_row(project_id, row_source_id):
    """Step 2 of the optional approval hierarchy: only a 'lead' can call
    this. Requires the row to already be submitted when the project's
    config has safety.require_approval_hierarchy set."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    p = conn.execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
    config = json.loads(p["config_json"]) if p else {}
    hierarchy_required = config.get("safety", {}).get("require_approval_hierarchy")
    if hierarchy_required and not row["submitted_by"]:
        return _err("This project requires rows to be submitted before they can be approved. Call .../submit first.")

    conn.execute(
        "UPDATE review_rows SET approved=1, approved_by=?, updated_at=? WHERE id=?",
        (actor_name(), _now(), row["id"]),
    )
    conn.commit()
    _log_audit(conn, project_id, "review_row_approved", {"source_id": row_source_id})
    updated = conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()
    return jsonify(_row_to_json(updated))


@bp.route("/projects/<project_id>/review/bulk", methods=["POST"])
def bulk_review(project_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)

    body = request.get_json(silent=True) or {}
    action = body.get("action")
    source_ids = body.get("source_ids")
    if action not in ("approve", "reject"):
        return _err("'action' must be 'approve' or 'reject'.")
    if not source_ids or not isinstance(source_ids, list):
        return _err("'source_ids' must be a non-empty list of sourceId strings.")

    undo_token = _uid()
    affected = 0
    for sid in source_ids:
        row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, sid)).fetchone()
        if not row:
            continue
        previous_state = _row_to_json(row)
        approved = 1 if action == "approve" else 0
        conn.execute("UPDATE review_rows SET approved=?, updated_at=? WHERE id=?", (approved, _now(), row["id"]))
        conn.execute(
            "INSERT INTO decisions (id, project_id, row_id, undo_token, previous_state_json, created_at) VALUES (?,?,?,?,?,?)",
            (_uid(), project_id, row["id"], undo_token, json.dumps(previous_state), _now()),
        )
        affected += 1
    conn.commit()
    _log_audit(conn, project_id, f"bulk_{action}", {"count": affected, "undo_token": undo_token})

    return jsonify({"affected_count": affected, "undo_token": undo_token})


@bp.route("/projects/<project_id>/review/undo", methods=["POST"])
def undo_review(project_id):
    conn = get_conn()
    body = request.get_json(silent=True) or {}
    undo_token = body.get("undo_token")
    if not undo_token:
        return _err("Missing 'undo_token'.")

    decisions = conn.execute(
        "SELECT * FROM decisions WHERE project_id=? AND undo_token=? ORDER BY created_at DESC", (project_id, undo_token)
    ).fetchall()
    if not decisions:
        return _err("Unknown or already-used undo_token.", 404)

    for d in decisions:
        prev = json.loads(d["previous_state_json"])
        conn.execute(
            "UPDATE review_rows SET approved=?, chosen_method=?, note=?, updated_at=? WHERE id=?",
            (int(bool(prev["approved"])), prev["chosenMethod"], prev.get("note"), _now(), d["row_id"]),
        )
    conn.execute("DELETE FROM decisions WHERE undo_token=?", (undo_token,))
    conn.commit()
    _log_audit(conn, project_id, "undo_applied", {"undo_token": undo_token, "rows_reverted": len(decisions)})

    return jsonify({"reverted_count": len(decisions)})


# --------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/export", methods=["POST"])
def export_project(project_id):
    conn = get_conn()
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)

    rows = conn.execute(
        "SELECT * FROM review_rows WHERE project_id=? AND approved=1 ORDER BY CAST(source_id AS INTEGER), source_id",
        (project_id,),
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_id", "source_name", "target_id", "target_name", "method", "score"])
    for r in rows:
        methods = json.loads(r["methods_json"])
        chosen = r["chosen_method"]
        m = methods.get(chosen) if chosen else next((v for v in methods.values() if v["targetId"] is not None), None)
        m = m or {"targetId": None, "name": None, "score": None}
        writer.writerow([r["source_id"], r["source_name"], m["targetId"], m["name"], chosen or "", m["score"]])

    _log_audit(conn, project_id, "exported", {"row_count": len(rows)})
    conn.commit()

    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    filename = f"{p['name']}_export.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


# --------------------------------------------------------------------- #
# Nominatim lookup -- single row only, rate-limited, per Nominatim's usage
# policy (https://operations.osmfoundation.org/policies/nominatim/):
# max 1 req/sec, no bulk/systematic geocoding, identify the app in
# User-Agent. Nominatim can return a 403 Forbidden if the User-Agent
# doesn't identify a real application (its abuse protection rejects
# generic or missing User-Agent strings). Set RELINK_NOMINATIM_USER_AGENT
# to real contact info (app name plus an email or project URL) to avoid
# that before relying on this for anything beyond occasional testing.
# --------------------------------------------------------------------- #

@bp.route("/geocode", methods=["POST"])
def geocode_single_row():
    body = request.get_json(silent=True) or {}
    if isinstance(body.get("query"), list):
        return _err("Bulk geocoding isn't supported here, per Nominatim's usage policy. Call this once per row.")
    query = (body.get("query") or "").strip()
    if not query:
        return _err("Missing 'query' (a single free-form address/place string). This endpoint is single-row only -- no batch input.")

    global _nominatim_last_call
    with _nominatim_lock:
        wait = _NOMINATIM_MIN_INTERVAL_SECONDS - (time.time() - _nominatim_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": _NOMINATIM_USER_AGENT},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return _err(f"Nominatim lookup failed: {e}", 502)
        finally:
            _nominatim_last_call = time.time()

    results = resp.json()
    if not results:
        return jsonify({"query": query, "found": False})

    top = results[0]
    return jsonify({
        "query": query,
        "found": True,
        "display_name": top.get("display_name"),
        "lat": float(top["lat"]) if top.get("lat") else None,
        "lon": float(top["lon"]) if top.get("lon") else None,
        "osm_type": top.get("osm_type"),
        "osm_id": top.get("osm_id"),
        "importance": top.get("importance"),
    })


# --------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/audit", methods=["GET"])
def get_audit_log(project_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)

    page = max(1, int(request.args.get("page", 1)))
    page_size = min(500, max(1, int(request.args.get("page_size", 50))))
    total = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE project_id=?", (project_id,)).fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE project_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (project_id, page_size, (page - 1) * page_size),
    ).fetchall()

    return jsonify({
        "entries": [
            {"actor": r["actor"], "action": r["action"],
             "detail": json.loads(r["detail"]) if r["detail"] else None,
             "created_at": r["created_at"]}
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    })
