"""
Tests for app/config.py (RELINK_MODE/HOST/PORT/REQUIRE_AUTH/CORS_ORIGINS
resolution), the GET /health endpoint, and confirmation that RELINK_MODE
actually drives the auth gate on real routes -- not just a config object
in isolation.
"""
import os
import shutil
import tempfile

import pytest

from app import config as config_module
from app import db as db_module


# --------------------------------------------------------------------- #
# Pure config.load_config() tests. These don't touch the filesystem or
# Flask at all -- load_config() is a pure function of the env mapping
# you pass it, so no fixtures/cleanup are needed.
# --------------------------------------------------------------------- #

def test_default_mode_is_local():
    cfg = config_module.load_config(env={})
    assert cfg.mode == "local"
    assert cfg.host == "127.0.0.1"
    assert cfg.require_auth is False
    assert cfg.debug is False


def test_lan_mode_defaults_to_0000_and_requires_auth():
    cfg = config_module.load_config(env={"RELINK_MODE": "lan"})
    assert cfg.host == "0.0.0.0"
    assert cfg.require_auth is True
    assert cfg.debug is False  # LAN mode never runs with debug on


def test_lan_mode_ignores_relink_debug():
    cfg = config_module.load_config(env={"RELINK_MODE": "lan", "RELINK_DEBUG": "1"})
    assert cfg.debug is False


def test_local_mode_can_enable_debug_explicitly():
    cfg = config_module.load_config(env={"RELINK_MODE": "local", "RELINK_DEBUG": "1"})
    assert cfg.debug is True


def test_explicit_host_overrides_mode_default():
    cfg = config_module.load_config(env={"RELINK_MODE": "lan", "RELINK_HOST": "127.0.0.1"})
    assert cfg.host == "127.0.0.1"


def test_explicit_require_auth_overrides_lan_default():
    cfg = config_module.load_config(env={"RELINK_MODE": "lan", "RELINK_REQUIRE_AUTH": "0"})
    assert cfg.require_auth is False


def test_explicit_require_auth_overrides_local_default():
    cfg = config_module.load_config(env={"RELINK_MODE": "local", "RELINK_REQUIRE_AUTH": "1"})
    assert cfg.require_auth is True


def test_default_port_is_5000():
    assert config_module.load_config(env={}).port == 5000


def test_explicit_port_is_used():
    assert config_module.load_config(env={"RELINK_PORT": "8080"}).port == 8080


def test_cors_origins_parsed_as_list():
    cfg = config_module.load_config(env={"RELINK_CORS_ORIGINS": "http://a.test, http://b.test"})
    assert cfg.cors_origins == ["http://a.test", "http://b.test"]


def test_data_and_upload_dirs_use_env_overrides():
    cfg = config_module.load_config(env={"RELINK_DATA_DIR": "/tmp/x-data", "RELINK_UPLOAD_DIR": "/tmp/x-uploads"})
    assert cfg.data_dir.endswith("x-data")
    assert cfg.upload_dir.endswith("x-uploads")


def test_invalid_mode_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_MODE"):
        config_module.load_config(env={"RELINK_MODE": "cloud"})


def test_invalid_port_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_PORT"):
        config_module.load_config(env={"RELINK_PORT": "not-a-number"})


def test_port_out_of_range_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_PORT"):
        config_module.load_config(env={"RELINK_PORT": "70000"})


def test_port_zero_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_PORT"):
        config_module.load_config(env={"RELINK_PORT": "0"})


def test_invalid_require_auth_value_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_REQUIRE_AUTH"):
        config_module.load_config(env={"RELINK_REQUIRE_AUTH": "maybe"})


def test_empty_host_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_HOST"):
        config_module.load_config(env={"RELINK_HOST": "   "})


def test_empty_cors_origins_raises_clear_error():
    with pytest.raises(config_module.ConfigError, match="RELINK_CORS_ORIGINS"):
        config_module.load_config(env={"RELINK_CORS_ORIGINS": "   , , "})


# --------------------------------------------------------------------- #
# Flask-app-level tests: the /health endpoint, and that RELINK_MODE
# actually drives the auth gate on a real route (not just the config
# object in isolation above).
# --------------------------------------------------------------------- #

def _build_app(**env_overrides):
    """Builds a fresh Flask app with its own temp data/upload dirs plus
    the given extra env vars, mirroring conftest.py's `app` fixture but
    self-contained here so LAN-mode tests don't need a shared fixture.
    Returns (flask_app, cleanup) -- call cleanup() when done."""
    data_dir = tempfile.mkdtemp()
    upload_dir = tempfile.mkdtemp()
    prev = {k: os.environ.get(k) for k in env_overrides}
    os.environ["RELINK_DATA_DIR"] = data_dir
    os.environ["RELINK_UPLOAD_DIR"] = upload_dir
    for k, v in env_overrides.items():
        os.environ[k] = v

    if hasattr(db_module._local, "conn"):
        db_module._local.conn.close()
        del db_module._local.conn

    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True

    def cleanup():
        if hasattr(db_module._local, "conn"):
            db_module._local.conn.close()
            del db_module._local.conn
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(upload_dir, ignore_errors=True)
        os.environ.pop("RELINK_DATA_DIR", None)
        os.environ.pop("RELINK_UPLOAD_DIR", None)
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return flask_app, cleanup


def test_health_endpoint_local_mode():
    app, cleanup = _build_app()
    try:
        resp = app.test_client().get("/health")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "mode": "local", "auth_required": False}
    finally:
        cleanup()


def test_health_endpoint_lan_mode_defaults_auth_required_true():
    app, cleanup = _build_app(RELINK_MODE="lan")
    try:
        resp = app.test_client().get("/health")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "mode": "lan", "auth_required": True}
    finally:
        cleanup()


def test_lan_mode_actually_requires_auth_on_a_real_route():
    # .../submit is @require_auth() with no role -- only bites when the
    # global RELINK_REQUIRE_AUTH flag (set here via RELINK_MODE=lan) or a
    # role is in play. Confirms the mode default really gates a route,
    # not just the config object in isolation.
    app, cleanup = _build_app(RELINK_MODE="lan")
    try:
        resp = app.test_client().post("/api/projects/doesnotexist/review/1/submit")
        assert resp.status_code == 401
    finally:
        cleanup()


def test_local_mode_does_not_require_auth_on_submit_route():
    app, cleanup = _build_app()
    try:
        resp = app.test_client().post("/api/projects/doesnotexist/review/1/submit")
        # No global auth requirement in local mode -- falls through to
        # the normal 404 (project not found) instead of a 401.
        assert resp.status_code == 404
    finally:
        cleanup()


def test_invalid_config_raises_before_app_is_created():
    os.environ["RELINK_MODE"] = "not-a-real-mode"
    try:
        with pytest.raises(config_module.ConfigError):
            from app import create_app
            create_app()
    finally:
        os.environ.pop("RELINK_MODE", None)

