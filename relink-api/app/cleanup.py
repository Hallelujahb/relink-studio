"""
Upload cleanup/expiry. Off by default -- only runs when
RELINK_UPLOAD_EXPIRY_DAYS is set. Deletes uploaded files (and their
cached parsed-data JSON) older than that many days AND not referenced by
any project's source_file_id/target_file_id, so an in-progress project's
files are never swept out from under it.
"""
import glob
import os
import threading
import time
from datetime import datetime, timedelta

from .db import get_conn

_CLEANUP_INTERVAL_SECONDS = 3600  # once an hour


def _referenced_file_ids(conn):
    rows = conn.execute("SELECT source_file_id, target_file_id FROM projects").fetchall()
    ids = set()
    for r in rows:
        if r["source_file_id"]:
            ids.add(r["source_file_id"])
        if r["target_file_id"]:
            ids.add(r["target_file_id"])
    return ids


def run_cleanup_once(app):
    with app.app_context():
        expiry_days = int(os.environ.get("RELINK_UPLOAD_EXPIRY_DAYS", "0"))
        if expiry_days <= 0:
            return 0
        conn = get_conn()
        cutoff = datetime.utcnow() - timedelta(days=expiry_days)
        referenced = _referenced_file_ids(conn)

        expired = conn.execute("SELECT * FROM files WHERE created_at < ?", (cutoff.isoformat(),)).fetchall()
        deleted = 0
        for f in expired:
            if f["id"] in referenced:
                continue
            if os.path.exists(f["stored_path"]):
                os.remove(f["stored_path"])
            for cached in glob.glob(os.path.join(app.config["UPLOAD_DIR"], f"{f['id']}.parsed.json")):
                os.remove(cached)
            conn.execute("DELETE FROM files WHERE id=?", (f["id"],))
            deleted += 1
        conn.commit()
        return deleted


def start_cleanup_thread(app):
    def loop():
        while True:
            try:
                run_cleanup_once(app)
            except Exception:
                pass  # best-effort background job; a failed sweep shouldn't kill the process
            time.sleep(_CLEANUP_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
