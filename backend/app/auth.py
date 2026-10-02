"""
Username and password auth with opaque bearer tokens and two roles,
"reviewer" (default) and "lead".

Whether auth is required is decided in app/config.py and stored on the Flask
app as RELINK_REQUIRE_AUTH. It is read per request, not frozen at import.

Tokens are only ever stored as a SHA-256 hash, so a copy of the database does
not hand out working sessions.

Approval hierarchy: when a project sets safety.require_approval_hierarchy,
rows are submitted by anyone and approved by a lead, and never by the person
who submitted them.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_conn

SESSION_LIFETIME_HOURS = 12


def _uid():
    return uuid.uuid4().hex[:12]


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def register_user(username, password, role="reviewer"):
    if role not in ("reviewer", "lead"):
        raise ValueError("role must be 'reviewer' or 'lead'")
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError(f"Username '{username}' is already taken.")
    user_id = _uid()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?,?,?,?,?)",
        (user_id, username, generate_password_hash(password), role, _now().isoformat()),
    )
    conn.commit()
    return {"id": user_id, "username": username, "role": role}


def login(username, password):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return None
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(),))
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires_at = (_now() + timedelta(hours=SESSION_LIFETIME_HOURS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (_hash_token(token), user["id"], _now().isoformat(), expires_at),
    )
    conn.commit()
    return {"token": token, "username": user["username"], "role": user["role"], "expires_at": expires_at}


def logout(token):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))
    conn.commit()


def _resolve_token(token):
    if not token:
        return None
    conn = get_conn()
    hashed = _hash_token(token)
    row = conn.execute(
        "SELECT s.token, s.expires_at, u.id as user_id, u.username, u.role "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (hashed,),
    ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < _now():
        conn.execute("DELETE FROM sessions WHERE token=?", (hashed,))
        conn.commit()
        return None
    return {"id": row["user_id"], "username": row["username"], "role": row["role"]}


def bearer_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else None


def get_current_user():
    """The caller from an `Authorization: Bearer <token>` header, or None."""
    if not hasattr(g, "_relink_user_resolved"):
        g.user = _resolve_token(bearer_token())
        g._relink_user_resolved = True
    return g.user


_AUTH_HINT = "Authentication required. Send 'Authorization: Bearer <token>' from POST /api/auth/login."


def require_auth(role=None):
    """Rejects the request when auth is required globally, or when a role is
    asked for (a role check is meaningless without a real user)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            needed = current_app.config.get("RELINK_REQUIRE_AUTH", False) or role
            if needed and not user:
                return jsonify({"error": _AUTH_HINT}), 401
            if role and user and user["role"] != role:
                return jsonify({"error": f"This action requires the '{role}' role; you are '{user['role']}'."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_lead_when_auth(fn):
    """Lead only once logins are on. On a single machine with auth off there is
    only one user, so the check is skipped."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_app.config.get("RELINK_REQUIRE_AUTH"):
            user = get_current_user()
            if not user:
                return jsonify({"error": _AUTH_HINT}), 401
            if user["role"] != "lead":
                return jsonify({"error": "This action requires the 'lead' role."}), 403
        return fn(*args, **kwargs)
    return wrapper


def actor_name():
    user = get_current_user()
    return user["username"] if user else "anonymous"
