"""
Basic multi-user auth. Deliberately simple: username + password with
werkzeug's hashing (already a Flask dependency, no new heavy dep), opaque
bearer tokens stored in a `sessions` table with an expiry, and two roles:
"reviewer" (default) and "lead".

This is NOT enforced globally by default -- set RELINK_REQUIRE_AUTH=1 to
require a valid token on every /api/projects/* route. Left optional
because turning it on breaks the existing curl/test flow used elsewhere in
this project unless every call is updated to send a token; see README for
how to turn it on for a real deployment.

Approval hierarchy (also section 5): when a project's
config.safety.require_approval_hierarchy is true, a non-"lead" user can
only *submit* a review row (POST .../submit); only a "lead" user can
*approve* a submitted row (POST .../approve). Without that config flag,
the original single-step PATCH .../review/{id} still works for anyone.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_conn

SESSION_LIFETIME_HOURS = 12
REQUIRE_AUTH = os.environ.get("RELINK_REQUIRE_AUTH", "0") == "1"


def _uid():
    return uuid.uuid4().hex[:12]


def register_user(username, password, role="reviewer"):
    if role not in ("reviewer", "lead"):
        raise ValueError("role must be 'reviewer' or 'lead'")
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError(f"Username '{username}' is already taken.")
    user_id = _uid()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?,?,?,?,?)",
        (user_id, username, generate_password_hash(password), role, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return {"id": user_id, "username": username, "role": role}


def login(username, password):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return None
    token = uuid.uuid4().hex
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_LIFETIME_HOURS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user["id"], datetime.now(timezone.utc).isoformat(), expires_at),
    )
    conn.commit()
    return {"token": token, "username": user["username"], "role": user["role"], "expires_at": expires_at}


def _resolve_token(token):
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT s.token, s.expires_at, u.id as user_id, u.username, u.role "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,),
    ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    return {"id": row["user_id"], "username": row["username"], "role": row["role"]}


def get_current_user():
    """Resolves the caller from an `Authorization: Bearer <token>` header.
    Returns None (not an error) when there's no/invalid token -- callers
    decide whether that's acceptable via @require_auth or by checking g.user."""
    if not hasattr(g, "_relink_user_resolved"):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else None
        g.user = _resolve_token(token)
        g._relink_user_resolved = True
    return g.user


def require_auth(role=None):
    """Decorator. Always resolves g.user from the token if present. Only
    *rejects* the request if RELINK_REQUIRE_AUTH=1, or if `role` is given
    and the resolved user doesn't have it (role check implies auth
    required for that route regardless of the global flag, since a role
    check without a real user makes no sense)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if (REQUIRE_AUTH or role) and not user:
                return jsonify({"error": "Authentication required. Send 'Authorization: Bearer <token>' from POST /api/auth/login."}), 401
            if role and user and user["role"] != role:
                return jsonify({"error": f"This action requires the '{role}' role; you are '{user['role']}'."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def actor_name():
    user = get_current_user()
    return user["username"] if user else "anonymous"
