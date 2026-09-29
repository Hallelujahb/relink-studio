import os
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from .config import load_config
from .db import init_db
from .httputil import BadParam

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app():
    cfg = load_config()

    frontend_dist = os.environ.get("RELINK_FRONTEND_DIST")
    app = Flask(
        __name__,
        static_folder=frontend_dist if frontend_dist and os.path.isdir(frontend_dist) else None,
        static_url_path="",
    )

    app.config["RELINK_CONFIG"] = cfg
    app.config["RELINK_REQUIRE_AUTH"] = cfg.require_auth
    app.config["DATA_DIR"] = cfg.data_dir
    app.config["UPLOAD_DIR"] = cfg.upload_dir
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("RELINK_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    CORS(app, resources={r"/api/*": {"origins": cfg.cors_origins}})
    init_db(app.config["DATA_DIR"])

    from .routes import bp as api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    from .routes_extra import bp as api_bp_extra
    app.register_blueprint(api_bp_extra, url_prefix="/api")

    from .routes_db_ingest import bp as api_bp_db_ingest
    app.register_blueprint(api_bp_db_ingest, url_prefix="/api")

    @app.before_request
    def _guard_browser_requests():
        # Without logins, anything a browser on this machine can reach is fair
        # game, including pages on other sites. Two checks keep those out:
        # the Host header (DNS rebinding) and the Origin of state changing
        # requests (a page on another site posting to localhost).
        if not app.config.get("RELINK_REQUIRE_AUTH"):
            hostname = (urlsplit("//" + (request.host or "")).hostname or "").lower()
            allowed = _LOOPBACK_HOSTS | {cfg.host.lower()} | set(cfg.allowed_hosts)
            if hostname not in allowed:
                return jsonify({"error": f"Host '{hostname}' is not allowed. Set RELINK_ALLOWED_HOSTS to permit it."}), 403
        origin = request.headers.get("Origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            same_site = urlsplit(origin).netloc.lower() == (request.host or "").lower()
            if not same_site and origin not in cfg.cors_origins:
                return jsonify({"error": "Cross-origin request refused."}), 403
        return None

    @app.before_request
    def _enforce_auth_globally():
        # With logins on, every /api route needs a token except login and register.
        if not app.config.get("RELINK_REQUIRE_AUTH"):
            return None
        if request.method == "OPTIONS" or not request.path.startswith("/api/"):
            return None
        if request.path in ("/api/auth/login", "/api/auth/register"):
            return None
        from .auth import get_current_user
        if not get_current_user():
            return jsonify({"error": "Authentication required. Send 'Authorization: Bearer <token>' from POST /api/auth/login."}), 401
        return None

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "mode": cfg.mode, "auth_required": app.config.get("RELINK_REQUIRE_AUTH")})

    @app.errorhandler(BadParam)
    def bad_param(e):
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Increase RELINK_MAX_UPLOAD_BYTES if this is expected."}), 413

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed on this endpoint."}), 405

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad request."}), 400

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal server error."}), 500

    if os.environ.get("RELINK_UPLOAD_EXPIRY_DAYS"):
        from .cleanup import start_cleanup_thread
        start_cleanup_thread(app)

    # When RELINK_FRONTEND_DIST points at a built frontend (relink.sh sets it),
    # this process serves the UI as well as the API. Anything that is not a real
    # file or an /api path falls through to index.html for the single page app.
    if app.static_folder:
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path):
            if path.startswith("api/"):
                return jsonify({"error": "Not found."}), 404
            if path and os.path.exists(os.path.join(app.static_folder, path)):
                return send_from_directory(app.static_folder, path)
            return send_from_directory(app.static_folder, "index.html")

    return app
