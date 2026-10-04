import csv
import io
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, Response, current_app, jsonify, request, send_file, stream_with_context

from . import auth as auth_module
from . import matching
from .auth import actor_name, require_auth, require_lead_when_auth
from .db import get_conn
from .exceptions_mgmt import Exception_, ExceptionRegistry
from .httputil import int_arg
from .matching_embedding import EmbeddingUnavailable, method_c_embedding
from .matching_splink import SplinkUnavailable, method_e_splink
from .parsers import ParseError, load_parsed_payload, parse_file, save_parsed_payload

bp = Blueprint("api", __name__)


def _uid():
    return uuid.uuid4().hex[:12]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


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


# Nominatim allows one request per second and asks apps to identify themselves.
# The limiter is process wide on purpose, it protects a shared outside quota.
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
        "decision": row["decision"],
        "chosenMethod": row["chosen_method"],
        "collisionPartner": row["collision_partner"],
        "methods": json.loads(row["methods_json"]),
        "note": row["note"],
        "submittedBy": row["submitted_by"],
        "approvedBy": row["approved_by"],
    }


def _state_of(row):
    """The parts of a review row a person can change. Used to snapshot for undo."""
    return {
        "approved": bool(row["approved"]),
        "decision": row["decision"],
        "chosen_method": row["chosen_method"],
        "note": row["note"],
    }


def _candidate(methods, chosen=None):
    """The match a row would export: the chosen method's, else the best scored.
    Method D only corroborates, it never proposes a target."""
    if chosen and chosen != "D":
        m = methods.get(chosen)
        if m and m.get("targetId") is not None:
            return m
    scored = [m for k, m in methods.items() if k != "D" and m and m.get("targetId") is not None]
    return max(scored, key=lambda m: m["score"] or 0) if scored else None


def _project_config(conn, project_id):
    p = conn.execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
    return json.loads(p["config_json"]) if p else {}


def _hierarchy_required(config):
    return bool(config.get("safety", {}).get("require_approval_hierarchy"))


# --------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------- #

_login_failures = {}
_login_lock = threading.Lock()
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300


def _login_key(username):
    return (str(username).lower(), request.remote_addr)


def _login_throttled(key):
    with _login_lock:
        recent = [t for t in _login_failures.get(key, []) if time.time() - t < _LOGIN_WINDOW_SECONDS]
        _login_failures[key] = recent
        return len(recent) >= _MAX_LOGIN_FAILURES


def _record_login_failure(key):
    with _login_lock:
        _login_failures.setdefault(key, []).append(time.time())


@bp.route("/auth/register", methods=["POST"])
def auth_register():
    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not username or not password:
        return _err("Missing 'username' and/or 'password'.")
    if len(password) < 8:
        return _err("Password must be at least 8 characters.")
    role = body.get("role", "reviewer")
    first_user = get_conn().execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    caller = auth_module.get_current_user()
    caller_is_lead = bool(caller and caller["role"] == "lead")
    auth_required = current_app.config.get("RELINK_REQUIRE_AUTH")

    # On a shared network the first account becomes the admin, so it can only be
    # created from the machine Relink runs on.
    if auth_required and first_user and request.remote_addr not in ("127.0.0.1", "::1"):
        return _err("The first account has to be created from the machine running Relink.", 403)
    if role == "lead" and not (first_user or caller_is_lead):
        return _err("Only an existing lead can create a lead account.", 403)
    if auth_required and not (first_user or caller_is_lead):
        return _err("Registration is closed. Ask a lead to create your account.", 403)
    try:
        user = auth_module.register_user(username, password, role)
    except ValueError as e:
        return _err(str(e))
    return jsonify(user), 201


@bp.route("/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True) or {}
    username, password = body.get("username"), body.get("password")
    if not username or not password:
        return _err("Missing 'username' and/or 'password'.")
    key = _login_key(username)
    if _login_throttled(key):
        return _err("Too many failed logins. Wait a few minutes and try again.", 429)
    session = auth_module.login(username, password)
    if not session:
        _record_login_failure(key)
        return _err("Invalid username or password.", 401)
    with _login_lock:
        _login_failures.pop(key, None)
    return jsonify(session)


@bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    token = auth_module.bearer_token()
    if token:
        auth_module.logout(token)
    return "", 204


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

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return _err(f"Unsupported file type: .{ext}. Use .csv, .xlsx, .xls, .geojson, or .json.")

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
        "file_id": file_id, "filename": f.filename, "columns": parsed["columns"],
        "row_count": parsed["row_count"], "format": parsed["format"],
        "has_geometry": bool(parsed.get("geometry")),
    }), 201


@bp.route("/upload/<file_id>", methods=["DELETE"])
def delete_upload(file_id):
    conn = get_conn()
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        return _err("File not found.", 404)
    if conn.execute("SELECT 1 FROM projects WHERE source_file_id=? OR target_file_id=?", (file_id, file_id)).fetchone():
        return _err("File is referenced by a project. Delete the project first.", 409)
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

def _check_config_columns(conn, config, source_file_id, target_file_id):
    """Returns an error message if the config names a column the files do not have."""
    for side, file_id in (("source", source_file_id), ("target", target_file_id)):
        section = config.get(side)
        if not isinstance(section, dict):
            return f"config.{side} is missing."
        for key in ("id_column", "match_column"):
            if not section.get(key):
                return f"config.{side}.{key} is required."
        columns = set(json.loads(conn.execute("SELECT columns_json FROM files WHERE id=?", (file_id,)).fetchone()["columns_json"]))
        wanted = [section["id_column"], section["match_column"]]
        if side == "target" and section.get("type_column"):
            wanted.append(section["type_column"])
        missing = [c for c in wanted if c not in columns]
        if missing:
            return f"The {side} file has no column named {', '.join(repr(m) for m in missing)}."
    return None


@bp.route("/projects", methods=["POST"])
def create_project():
    body = request.get_json(silent=True) or {}
    config = body.get("config")
    source_file_id = body.get("source_file_id")
    target_file_id = body.get("target_file_id")

    if not config:
        return _err("Missing 'config' (the project config object).")
    if not source_file_id or not target_file_id:
        return _err("Missing 'source_file_id' and/or 'target_file_id' (returned by POST /api/upload).")

    conn = get_conn()
    for fid, label in ((source_file_id, "source_file_id"), (target_file_id, "target_file_id")):
        if not conn.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone():
            return _err(f"Unknown {label}: {fid}. Upload it first via POST /api/upload.")

    problem = _check_config_columns(conn, config, source_file_id, target_file_id)
    if problem:
        return _err(problem)

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


@bp.route("/projects/<project_id>", methods=["DELETE"])
@require_lead_when_auth
def delete_project(project_id):
    conn = get_conn()
    if project_id == "system" or not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)
    if conn.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('pending','running')", (project_id,)).fetchone():
        return _err("A run is still in progress for this project.", 409)
    for table in ("decisions", "review_rows", "review_decision_log", "project_exceptions", "jobs", "audit_log"):
        conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    return "", 204


@bp.route("/projects/<project_id>/config", methods=["GET"])
def get_config(project_id):
    p = get_conn().execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        return _err("Project not found.", 404)
    return jsonify(json.loads(p["config_json"]))


@bp.route("/projects/<project_id>/config", methods=["POST"])
@require_lead_when_auth
def update_config(project_id):
    conn = get_conn()
    if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)
    config = request.get_json(silent=True)
    if not config:
        return _err("Missing JSON body (the config object).")
    conn.execute("UPDATE projects SET config_json=?, updated_at=? WHERE id=?", (json.dumps(config), _now(), project_id))
    conn.commit()
    _log_audit(conn, project_id, "config_updated")
    return jsonify(config)


# --------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------- #

def _prepare_ids(rows, column, label):
    """IDs become strings so every method and the database agree on their type.
    Blank or repeated IDs stop the run: results are keyed by ID and would
    silently overwrite each other."""
    out, seen, dupes, blanks, slashes = [], set(), [], 0, []
    for r in rows:
        v = r.get(column)
        if v is None or v != v or str(v).strip() == "":  # v != v catches NaN
            blanks += 1
            continue
        sid = str(v)
        if sid in seen:
            dupes.append(sid)
        seen.add(sid)
        if "/" in sid:
            slashes.append(sid)
        out.append({**r, column: sid})
    if blanks:
        raise ValueError(f"The {label} file has {blanks} row(s) with an empty '{column}'. Fill or remove them.")
    if dupes:
        shown = ", ".join(sorted(set(dupes))[:5])
        raise ValueError(f"The {label} file has {len(dupes)} repeated value(s) in '{column}' (for example {shown}). IDs must be unique.")
    if slashes:
        raise ValueError(f"IDs in '{column}' cannot contain '/' (for example {slashes[0]!r}).")
    return out


def _apply_exceptions(conn, project_id, rows, auto_approve):
    """Applies active, unexpired project exceptions. Three scopes change the
    result: excluded_source and ignored_record drop the row, allowed_type_mismatch
    clears a type leak, and approved_many_to_one clears a collision on that target."""
    registry = ExceptionRegistry()
    for e in conn.execute("SELECT * FROM project_exceptions WHERE project_id=? AND active=1", (project_id,)).fetchall():
        try:
            registry.add(Exception_(
                scope=e["scope"], reason=e["reason"], creator=e["creator"],
                affected_source_id=None if e["affected_source_id"] is None else str(e["affected_source_id"]),
                affected_target_id=None if e["affected_target_id"] is None else str(e["affected_target_id"]),
                expires_at=e["expires_at"],
            ))
        except ValueError:
            continue

    kept, applied = [], 0
    many_to_one = {x.affected_target_id for x in registry.active_for_scope("approved_many_to_one")}
    for r in rows:
        sid = str(r["sourceId"])
        if registry.applies_to(sid, "excluded_source") or registry.applies_to(sid, "ignored_record"):
            applied += 1
            continue
        cand = _candidate(r["methods"], r["chosenMethod"])
        score = cand["score"] if cand else None
        if r["kind"] == "type_leak" and registry.applies_to(sid, "allowed_type_mismatch"):
            r["kind"], r["approved"] = "consensus", score is not None and score >= auto_approve
            applied += 1
        elif r["kind"] == "collision" and cand and str(cand["targetId"]) in many_to_one:
            r["kind"], r["collisionPartner"] = "consensus", None
            r["approved"] = score is not None and score >= auto_approve
            applied += 1
        kept.append(r)
    return kept, applied


def _run_matching_job(app, project_id, job_id, triggered_by):
    with app.app_context():
        conn = get_conn()

        def set_job(status, progress=None, total=None, message=None):
            fields, params = ["status=?", "updated_at=?"], [status, _now()]
            for name, value in (("progress", progress), ("total", total), ("message", message)):
                if value is not None:
                    fields.append(f"{name}=?")
                    params.append(value)
            params.append(job_id)
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", params)
            conn.commit()

        try:
            p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            config = json.loads(p["config_json"])
            upload_dir = app.config["UPLOAD_DIR"]

            source_payload = load_parsed_payload(upload_dir, p["source_file_id"])
            target_payload = load_parsed_payload(upload_dir, p["target_file_id"])

            src_id_col = config["source"]["id_column"]
            src_match_col = config["source"]["match_column"]
            tgt_id_col = config["target"]["id_column"]
            tgt_match_col = config["target"]["match_column"]
            tgt_type_col = config["target"].get("type_column")
            tgt_type_expected = config["target"].get("type_expected")

            for label, payload, cols in (("source", source_payload, (src_id_col, src_match_col)),
                                         ("target", target_payload, (tgt_id_col, tgt_match_col))):
                gone = [c for c in cols if c not in payload["columns"]]
                if gone:
                    raise ValueError(f"The {label} file has no column named {', '.join(repr(c) for c in gone)}.")

            source_rows = _prepare_ids(source_payload["rows"], src_id_col, "source")
            target_rows = _prepare_ids(target_payload["rows"], tgt_id_col, "target")

            set_job("running", progress=0, total=len(source_rows), message="Matching in progress")

            matching_cfg = config.get("matching", {})
            blocking_floor = matching_cfg.get("blocking_floor", 0.60)
            methods_enabled = set(config.get("methods", []))

            method_results, method_meta = {}, {}
            if "fuzzy" in methods_enabled:
                method_results["A"] = matching.method_a_fuzzy(
                    source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                    matching_cfg, blocking_floor)
            if "recordlinkage" in methods_enabled:
                method_results["B"] = matching.method_b_token_overlap(
                    source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                    matching_cfg, blocking_floor)
            if "splink" in methods_enabled:
                try:
                    method_results["E"], method_meta["E"] = method_e_splink(
                        source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                        matching_cfg, blocking_floor)
                except SplinkUnavailable as e:
                    method_results["E"], method_meta["E"] = {}, {"error": str(e)}
            if "embedding" in methods_enabled:
                try:
                    method_results["C"], method_meta["C"] = method_c_embedding(
                        source_rows, target_rows, src_id_col, src_match_col, tgt_id_col, tgt_match_col,
                        matching_cfg, blocking_floor)
                except EmbeddingUnavailable as e:
                    method_results["C"], method_meta["C"] = {}, {"error": str(e)}

            rows = matching.combine_methods(
                source_rows, src_id_col, src_match_col, method_results,
                target_rows, tgt_id_col, tgt_type_col, tgt_type_expected, config)

            src_geom, tgt_geom = source_payload.get("geometry"), target_payload.get("geometry")
            if "geometry_corroboration" in methods_enabled and src_geom and tgt_geom:
                tgt_index_by_id = {t.get(tgt_id_col): i for i, t in enumerate(target_rows)}
                src_index_by_id = {s.get(src_id_col): i for i, s in enumerate(source_rows)}
                candidate_by_src = {}
                for r in rows:
                    cand = _candidate(r["methods"], r["chosenMethod"])
                    src_idx = src_index_by_id.get(r["sourceId"])
                    if cand and src_idx is not None:
                        candidate_by_src[src_idx] = tgt_index_by_id.get(cand["targetId"])
                flags = matching.method_d_geometry_corroboration(
                    src_geom, tgt_geom, candidate_by_src, max_km=matching_cfg.get("geometry_max_km", 25))
                for r in rows:
                    flag = flags.get(src_index_by_id.get(r["sourceId"]))
                    if flag and flag["suspect"]:
                        if r["kind"] == "consensus":
                            r["kind"] = "geometry_suspect"
                        r["approved"] = False
                    closeness = None
                    if flag and flag["distance_km"] is not None:
                        closeness = round(1.0 - min(flag["distance_km"] / 100, 1.0), 4)
                    r["methods"]["D"] = {"name": None, "targetId": None, "score": closeness}

            auto_approve = config.get("thresholds", {}).get("auto_approve", 0.95)
            rows, exceptions_applied = _apply_exceptions(conn, project_id, rows, auto_approve)

            # One transaction: a failure anywhere leaves the previous results untouched.
            conn.execute("DELETE FROM decisions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM review_rows WHERE project_id=?", (project_id,))
            conn.executemany(
                "INSERT INTO review_rows (id, project_id, source_id, source_name, kind, approved, "
                "chosen_method, collision_partner, methods_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(_uid(), project_id, str(r["sourceId"]), str(r["sourceName"] if r["sourceName"] is not None else ""),
                  r["kind"], int(r["approved"]), r["chosenMethod"], r["collisionPartner"],
                  json.dumps(r["methods"]), _now(), _now()) for r in rows],
            )
            conn.execute("UPDATE projects SET status='matched', updated_at=? WHERE id=?", (_now(), project_id))
            conn.commit()

            warnings = [f"Method {k}: {v['error']}" for k, v in method_meta.items() if v.get("error")]
            message = f"Matched {len(rows)} row(s)." + (" Warning: " + "; ".join(warnings) if warnings else "")
            set_job("done", progress=len(rows), total=len(rows), message=message)
            _log_audit(conn, project_id, "run_completed",
                       {"row_count": len(rows), "methods": sorted(methods_enabled), "method_notes": method_meta,
                        "exceptions_applied": exceptions_applied}, actor=triggered_by)
        except Exception as e:
            conn.rollback()
            set_job("error", message=str(e))
            _log_audit(conn, project_id, "run_failed", {"error": str(e)}, actor=triggered_by)


@bp.route("/projects/<project_id>/run", methods=["POST"])
def run_project(project_id):
    conn = get_conn()
    if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)
    body = request.get_json(silent=True) or {}

    if conn.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('pending','running')", (project_id,)).fetchone():
        return _err("A run is already in progress for this project.", 409)
    decided = conn.execute(
        "SELECT COUNT(*) c FROM review_rows WHERE project_id=? AND decision IS NOT NULL", (project_id,)
    ).fetchone()["c"]
    if decided and not body.get("force"):
        return _err(f"Running again replaces all results, including {decided} decision(s) already made. "
                    "Send {\"force\": true} to do it anyway.", 409)

    job_id = _uid()
    conn.execute("INSERT INTO jobs (id, project_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                 (job_id, project_id, "pending", _now(), _now()))
    conn.commit()
    triggered_by = actor_name()  # the background thread has no request to read this from
    _log_audit(conn, project_id, "run_started", {"job_id": job_id, "replaced_decisions": decided}, actor=triggered_by)

    app = current_app._get_current_object()
    threading.Thread(target=_run_matching_job, args=(app, project_id, job_id, triggered_by), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "pending"}), 202


@bp.route("/projects/<project_id>/jobs/<job_id>", methods=["GET"])
def job_status(project_id, job_id):
    j = get_conn().execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
    if not j:
        return _err("Job not found.", 404)
    return jsonify({"job_id": j["id"], "status": j["status"], "progress": j["progress"],
                    "total": j["total"], "message": j["message"], "updated_at": j["updated_at"]})


@bp.route("/projects/<project_id>/jobs/<job_id>/stream", methods=["GET"])
def job_status_stream(project_id, job_id):
    """Server-Sent Events version of job_status. Browsers cannot attach an
    Authorization header to an EventSource, so with logins on, poll instead."""
    app = current_app._get_current_object()

    def event_stream():
        with app.app_context():
            conn = get_conn()
            last_payload, started = None, time.time()
            while time.time() - started < 300:
                j = conn.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
                if not j:
                    yield "event: error\ndata: {\"error\": \"Job not found.\"}\n\n"
                    return
                payload = json.dumps({"job_id": j["id"], "status": j["status"], "progress": j["progress"],
                                      "total": j["total"], "message": j["message"]})
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
    page = int_arg("page", 1, lo=1)
    page_size = int_arg("page_size", 50, lo=1, hi=500)

    where, params = ["project_id=?"], [project_id]
    if filt == "approved":
        where.append("approved=1")
    elif filt == "rejected":
        where.append("decision='rejected'")
    elif filt not in ("all", ""):
        where.append("kind=?")
        params.append(filt)
    if search:
        where.append("(LOWER(source_name) LIKE ? OR LOWER(source_id) LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) c FROM review_rows WHERE {where_sql}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM review_rows WHERE {where_sql} ORDER BY CAST(source_id AS INTEGER), source_id LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"rows": [_row_to_json(r) for r in rows], "total": total, "page": page, "page_size": page_size})


def _record_change(conn, project_id, row, before, undo_token):
    after = _state_of(conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone())
    conn.execute(
        "INSERT INTO decisions (id, project_id, row_id, undo_token, previous_state_json, after_json, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (_uid(), project_id, row["id"], undo_token, json.dumps(before), json.dumps(after), _now()),
    )


@bp.route("/projects/<project_id>/review/<row_source_id>", methods=["PATCH"])
def patch_review_row(project_id, row_source_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    body = request.get_json(silent=True) or {}
    approved = body.get("approved")
    if approved is not None and not isinstance(approved, bool):
        return _err("'approved' must be true or false.")

    methods = json.loads(row["methods_json"])
    chosen = body.get("chosen_method", row["chosen_method"])
    if chosen is not None:
        m = methods.get(chosen)
        if chosen == "D" or not m or m.get("targetId") is None:
            return _err("'chosen_method' must be a method that produced a candidate for this row.")

    if approved is True:
        if _hierarchy_required(_project_config(conn, project_id)):
            return _err("This project requires submit and lead approval. Use .../submit then .../approve.", 403)
        if not _candidate(methods, chosen):
            return _err("There is no candidate target to approve for this row.")

    before = _state_of(row)
    decision = row["decision"] if approved is None else ("approved" if approved else "rejected")
    new_approved = bool(row["approved"]) if approved is None else approved
    conn.execute(
        "UPDATE review_rows SET approved=?, decision=?, chosen_method=?, note=?, updated_at=? WHERE id=?",
        (int(new_approved), decision, chosen, body.get("note", row["note"]), _now(), row["id"]),
    )
    undo_token = _uid()
    _record_change(conn, project_id, row, before, undo_token)
    conn.commit()
    _log_audit(conn, project_id, "review_row_updated",
               {"source_id": row_source_id, "approved": new_approved, "decision": decision, "chosen_method": chosen})

    updated = conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()
    return jsonify({"row": _row_to_json(updated), "undo_token": undo_token})


@bp.route("/projects/<project_id>/review/<row_source_id>/submit", methods=["POST"])
@require_auth()
def submit_review_row(project_id, row_source_id):
    """Step one of the approval hierarchy. Does not approve anything."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)
    conn.execute("UPDATE review_rows SET submitted_by=?, submitted_at=?, updated_at=? WHERE id=?",
                 (actor_name(), _now(), _now(), row["id"]))
    conn.commit()
    _log_audit(conn, project_id, "review_row_submitted", {"source_id": row_source_id})
    return jsonify(_row_to_json(conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()))


@bp.route("/projects/<project_id>/review/<row_source_id>/approve", methods=["POST"])
@require_auth(role="lead")
def approve_review_row(project_id, row_source_id):
    """Step two: only a lead, and when the hierarchy is required, never the person who submitted it."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, row_source_id)).fetchone()
    if not row:
        return _err("Review row not found.", 404)

    if _hierarchy_required(_project_config(conn, project_id)):
        if not row["submitted_by"]:
            return _err("This project requires rows to be submitted before they can be approved. Call .../submit first.")
        if row["submitted_by"] == actor_name():
            return _err("A row has to be approved by someone other than the person who submitted it.", 403)
    if not _candidate(json.loads(row["methods_json"]), row["chosen_method"]):
        return _err("There is no candidate target to approve for this row.")

    conn.execute("UPDATE review_rows SET approved=1, decision='approved', approved_by=?, updated_at=? WHERE id=?",
                 (actor_name(), _now(), row["id"]))
    conn.commit()
    _log_audit(conn, project_id, "review_row_approved", {"source_id": row_source_id})
    return jsonify(_row_to_json(conn.execute("SELECT * FROM review_rows WHERE id=?", (row["id"],)).fetchone()))


@bp.route("/projects/<project_id>/review/bulk", methods=["POST"])
def bulk_review(project_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)

    body = request.get_json(silent=True) or {}
    action, source_ids = body.get("action"), body.get("source_ids")
    if action not in ("approve", "reject"):
        return _err("'action' must be 'approve' or 'reject'.")
    if not source_ids or not isinstance(source_ids, list):
        return _err("'source_ids' must be a non-empty list of sourceId strings.")
    if action == "approve" and _hierarchy_required(_project_config(conn, project_id)):
        return _err("This project requires submit and lead approval. Bulk approve is not allowed.", 403)

    undo_token, affected, missing, ineligible = _uid(), 0, [], []
    approved = action == "approve"
    for sid in source_ids:
        row = conn.execute("SELECT * FROM review_rows WHERE project_id=? AND source_id=?", (project_id, str(sid))).fetchone()
        if not row:
            missing.append(sid)
            continue
        if approved and not _candidate(json.loads(row["methods_json"]), row["chosen_method"]):
            ineligible.append(sid)
            continue
        before = _state_of(row)
        conn.execute("UPDATE review_rows SET approved=?, decision=?, updated_at=? WHERE id=?",
                     (int(approved), "approved" if approved else "rejected", _now(), row["id"]))
        _record_change(conn, project_id, row, before, undo_token)
        affected += 1
    conn.commit()
    _log_audit(conn, project_id, f"bulk_{action}",
               {"count": affected, "undo_token": undo_token, "skipped_missing": len(missing), "skipped_no_candidate": len(ineligible)})
    return jsonify({"affected_count": affected, "undo_token": undo_token,
                    "skipped_missing": missing[:50], "skipped_no_candidate": ineligible[:50]})


@bp.route("/projects/<project_id>/review/undo", methods=["POST"])
def undo_review(project_id):
    conn = get_conn()
    undo_token = (request.get_json(silent=True) or {}).get("undo_token")
    if not undo_token:
        return _err("Missing 'undo_token'.")

    decisions = conn.execute(
        "SELECT * FROM decisions WHERE project_id=? AND undo_token=? ORDER BY created_at DESC", (project_id, undo_token)
    ).fetchall()
    if not decisions:
        return _err("Unknown or already-used undo_token.", 404)

    reverted = skipped = 0
    for d in decisions:
        row = conn.execute("SELECT * FROM review_rows WHERE id=?", (d["row_id"],)).fetchone()
        after = json.loads(d["after_json"]) if d["after_json"] else None
        # Someone changed the row after this action, so reverting would erase their decision.
        if not row or (after is not None and _state_of(row) != after):
            skipped += 1
            continue
        prev = json.loads(d["previous_state_json"])
        conn.execute(
            "UPDATE review_rows SET approved=?, decision=?, chosen_method=?, note=?, updated_at=? WHERE id=?",
            (int(bool(prev["approved"])), prev.get("decision"), prev.get("chosen_method"), prev.get("note"), _now(), d["row_id"]),
        )
        reverted += 1
    conn.execute("DELETE FROM decisions WHERE undo_token=?", (undo_token,))
    conn.commit()
    _log_audit(conn, project_id, "undo_applied", {"undo_token": undo_token, "rows_reverted": reverted, "rows_skipped": skipped})
    return jsonify({"reverted_count": reverted, "skipped_count": skipped})


# --------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------- #

def _csv_safe(value):
    """Stops spreadsheet apps from running a name like =HYPERLINK(...) as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


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
        m = _candidate(json.loads(r["methods_json"]), r["chosen_method"]) or {"targetId": None, "name": None, "score": None}
        writer.writerow([r["source_id"], _csv_safe(r["source_name"]), m["targetId"], _csv_safe(m["name"]),
                         r["chosen_method"] or "", m["score"]])

    _log_audit(conn, project_id, "exported", {"row_count": len(rows)})
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=f"{p['name']}_export.csv")


# --------------------------------------------------------------------- #
# Nominatim lookup: one row at a time, rate limited, per its usage policy.
# Set RELINK_NOMINATIM_USER_AGENT to real contact info or it may answer 403.
# --------------------------------------------------------------------- #

@bp.route("/geocode", methods=["POST"])
def geocode_single_row():
    body = request.get_json(silent=True) or {}
    if isinstance(body.get("query"), list):
        return _err("Bulk geocoding isn't supported here, per Nominatim's usage policy. Call this once per row.")
    query = (body.get("query") or "").strip()
    if not query:
        return _err("Missing 'query' (a single free-form address or place string).")

    global _nominatim_last_call
    with _nominatim_lock:
        wait = _NOMINATIM_MIN_INTERVAL_SECONDS - (time.time() - _nominatim_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": _NOMINATIM_USER_AGENT}, timeout=10)
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
        "query": query, "found": True, "display_name": top.get("display_name"),
        "lat": float(top["lat"]) if top.get("lat") else None,
        "lon": float(top["lon"]) if top.get("lon") else None,
        "osm_type": top.get("osm_type"), "osm_id": top.get("osm_id"), "importance": top.get("importance"),
    })


# --------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------- #

@bp.route("/projects/<project_id>/audit", methods=["GET"])
def get_audit_log(project_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        return _err("Project not found.", 404)

    page = int_arg("page", 1, lo=1)
    page_size = int_arg("page_size", 50, lo=1, hi=5000)
    total = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE project_id=?", (project_id,)).fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE project_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (project_id, page_size, (page - 1) * page_size),
    ).fetchall()
    return jsonify({
        "entries": [{"actor": r["actor"], "action": r["action"],
                     "detail": json.loads(r["detail"]) if r["detail"] else None, "created_at": r["created_at"]}
                    for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })
