"""
Extra routes for session restore, shared folders and sharing mode.

GET  /api/upload/<file_id>            file details (add ?geometry=1 for GeoJSON geometry and properties)
GET  /api/library                     files in the shared folder (RELINK_LIBRARY_DIR), if one is set
POST /api/library/import              copy one of those files in, as if it had been uploaded
POST /api/admin/switch-mode           restart Relink as "local" or "lan" (needs ./relink.sh)

The shared folder is read only from Relink's side. It is how a teammate's
configuration finds the files it names: put the files in one folder that both
of you can reach (a network drive, a synced folder) and set RELINK_LIBRARY_DIR.
"""
import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from .auth import require_lead_when_auth
from .db import get_conn
from .parsers import ParseError, load_parsed_payload, parse_file, save_parsed_payload

bp = Blueprint("api_library", __name__)

_ALLOWED = {"csv", "xlsx", "xls", "geojson", "json"}
_LOOPBACK = ("127.0.0.1", "::1")


def _uid():
    return uuid.uuid4().hex[:12]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _err(message, status=400):
    return jsonify({"error": message}), status


def _library_dir():
    path = os.environ.get("RELINK_LIBRARY_DIR", "").strip()
    return os.path.realpath(os.path.expanduser(path)) if path and os.path.isdir(os.path.expanduser(path)) else None


def _file_json(f, parsed=None):
    return {
        "file_id": f["id"], "filename": f["filename"], "columns": json.loads(f["columns_json"]),
        "row_count": f["row_count"], "format": f["format"], "has_geometry": bool(f["has_geometry"]),
    }


@bp.route("/upload/<file_id>", methods=["GET"])
def get_upload(file_id):
    f = get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        return _err("File not found.", 404)
    out = _file_json(f)
    if request.args.get("geometry") == "1" and f["has_geometry"]:
        try:
            payload = load_parsed_payload(current_app.config["UPLOAD_DIR"], file_id)
        except ParseError as e:
            return _err(str(e), 404)
        out["geometry"] = payload.get("geometry")
        out["rows"] = payload.get("rows")
    return jsonify(out)


@bp.route("/library", methods=["GET"])
def list_library():
    lib = _library_dir()
    if not lib:
        return jsonify({"enabled": False, "folder": None, "files": []})
    files = []
    for name in sorted(os.listdir(lib)):
        full = os.path.join(lib, name)
        if os.path.isfile(full) and name.rsplit(".", 1)[-1].lower() in _ALLOWED and not name.startswith("."):
            files.append({"name": name, "bytes": os.path.getsize(full)})
    return jsonify({"enabled": True, "folder": os.path.basename(lib) or lib, "files": files})


@bp.route("/library/import", methods=["POST"])
def import_from_library():
    lib = _library_dir()
    if not lib:
        return _err("No shared folder is set. Start Relink with RELINK_LIBRARY_DIR pointing at a folder.", 404)
    name = str((request.get_json(silent=True) or {}).get("filename") or "")
    # Bare file names only: no paths, no ".." and nothing that resolves outside the folder.
    if not name or name != os.path.basename(name) or name.startswith("."):
        return _err("'filename' must be a plain file name from the shared folder.")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _ALLOWED:
        return _err(f"Unsupported file type: .{ext}.")
    src = os.path.realpath(os.path.join(lib, name))
    if os.path.dirname(src) != lib or not os.path.isfile(src):
        return _err(f"'{name}' is not in the shared folder.", 404)
    if os.path.getsize(src) == 0:
        return _err("That file is empty.")
    if os.path.getsize(src) > current_app.config["MAX_CONTENT_LENGTH"]:
        return _err("That file is larger than RELINK_MAX_UPLOAD_BYTES.", 413)

    file_id = _uid()
    upload_dir = current_app.config["UPLOAD_DIR"]
    stored = os.path.join(upload_dir, f"{file_id}.{ext}")
    shutil.copyfile(src, stored)
    try:
        parsed = parse_file(stored, name)
    except ParseError as e:
        os.remove(stored)
        return _err(str(e))
    save_parsed_payload(upload_dir, file_id, parsed)
    conn = get_conn()
    conn.execute(
        "INSERT INTO files (id, filename, stored_path, columns_json, row_count, format, has_geometry, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (file_id, name, stored, json.dumps(parsed["columns"]), parsed["row_count"], parsed["format"], 1 if parsed.get("geometry") else 0, _now()),
    )
    conn.commit()
    return jsonify({
        "file_id": file_id, "filename": name, "columns": parsed["columns"], "row_count": parsed["row_count"],
        "format": parsed["format"], "has_geometry": bool(parsed.get("geometry")),
    }), 201


@bp.route("/admin/switch-mode", methods=["POST"])
@require_lead_when_auth
def switch_mode():
    mode = (request.get_json(silent=True) or {}).get("mode")
    if mode not in ("local", "lan"):
        return _err("'mode' must be 'local' or 'lan'.")
    flag = os.environ.get("RELINK_RESTART_FILE")
    if not flag:
        return _err("Switching needs Relink to be started with ./relink.sh. Restart it with ./relink.sh or ./relink.sh --lan yourself.", 501)
    cfg = current_app.config["RELINK_CONFIG"]
    if mode == cfg.mode:
        return jsonify({"mode": mode, "restarting": False})
    if not current_app.config.get("RELINK_REQUIRE_AUTH") and request.remote_addr not in _LOOPBACK:
        return _err("Only the machine running Relink can change the sharing mode.", 403)
    with open(flag, "w", encoding="utf-8") as fh:
        fh.write(mode)
    # Answer first, then exit. relink.sh sees the file and starts Relink again in the new mode.
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return jsonify({"mode": mode, "restarting": True}), 202
