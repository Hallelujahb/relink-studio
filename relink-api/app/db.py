import os
import sqlite3
import threading

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

CREATE TABLE IF NOT EXISTS review_rows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'consensus',
    approved INTEGER NOT NULL DEFAULT 0,
    chosen_method TEXT,
    collision_partner TEXT,
    methods_json TEXT NOT NULL,
    note TEXT,
    submitted_by TEXT,
    submitted_at TEXT,
    approved_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_review_rows_project ON review_rows(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_rows_project_source ON review_rows(project_id, source_id);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    undo_token TEXT NOT NULL,
    previous_state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_undo_token ON decisions(undo_token);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'reviewer',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
"""


def init_db(data_dir):
    global _DB_PATH
    _DB_PATH = os.path.join(data_dir, "relink_studio.sqlite3")
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_conn():
    # One connection per thread (Flask's dev server and the background
    # matching thread each need their own).
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(_DB_PATH, timeout=30)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
    return _local.conn
