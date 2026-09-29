import os
import sqlite3
import threading
from datetime import datetime, timezone

_local = threading.local()
_DB_PATH = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    format TEXT NOT NULL,
    has_geometry INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    source_file_id TEXT,
    target_file_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status ON jobs(project_id, status);

CREATE TABLE IF NOT EXISTS review_rows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'consensus',
    approved INTEGER NOT NULL DEFAULT 0,
    decision TEXT,
    chosen_method TEXT,
    collision_partner TEXT,
    methods_json TEXT NOT NULL,
    note TEXT,
    submitted_by TEXT,
    submitted_at TEXT,
    approved_by TEXT,
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_review_rows_project ON review_rows(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_rows_project_source ON review_rows(project_id, source_id);
CREATE INDEX IF NOT EXISTS idx_review_rows_project_kind ON review_rows(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_review_rows_project_approved ON review_rows(project_id, approved);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    undo_token TEXT NOT NULL,
    previous_state_json TEXT NOT NULL,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_undo_token ON decisions(undo_token);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'reviewer',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- token holds a SHA-256 of the bearer token, never the token itself.
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'anonymous',
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_audit_log_project ON audit_log(project_id);

CREATE TABLE IF NOT EXISTS schema_snapshots (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_id TEXT,
    schema_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_schema_snapshots_filename ON schema_snapshots(filename, created_at);

CREATE TABLE IF NOT EXISTS project_exceptions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    creator TEXT NOT NULL,
    affected_source_id TEXT,
    affected_target_id TEXT,
    affected_fields_json TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    review_date TEXT,
    expires_at TEXT,
    history_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_project_exceptions_project ON project_exceptions(project_id);

CREATE TABLE IF NOT EXISTS review_decision_log (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    target_id TEXT,
    confidence_at_decision REAL,
    config_version INTEGER,
    reason TEXT,
    note TEXT,
    is_bulk INTEGER NOT NULL DEFAULT 0,
    batch_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_review_decision_log_project_source ON review_decision_log(project_id, source_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_project_created ON audit_log(project_id, created_at);
"""

# Columns added after the first release. Older databases get them on startup.
_ADDED_COLUMNS = (
    ("review_rows", "decision", "TEXT"),
    ("decisions", "after_json", "TEXT"),
)

# Housekeeping audit entries (storage cleanup) are not tied to a real project,
# but audit_log has a foreign key, so they hang off this placeholder row.
SYSTEM_PROJECT_ID = "system"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def init_db(data_dir):
    global _DB_PATH
    _DB_PATH = os.path.join(data_dir, "relink_studio.sqlite3")
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)

    for table, column, ddl in _ADDED_COLUMNS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, config_json, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (SYSTEM_PROJECT_ID, "system", "{}", "system", now, now),
    )
    # A job that was running when the process died will never finish.
    conn.execute(
        "UPDATE jobs SET status='error', message='Interrupted by a restart. Run it again.', updated_at=? "
        "WHERE status IN ('pending','running')",
        (now,),
    )
    conn.commit()
    conn.close()


def get_conn():
    # One connection per thread: the request threads and the background matcher each need their own.
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(_DB_PATH, timeout=30)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets the matching thread write while a request thread polls job status.
        _local.conn.execute("PRAGMA journal_mode = WAL")
    return _local.conn
