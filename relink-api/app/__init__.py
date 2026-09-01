import os
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from .db import init_db


def create_app():
    frontend_dist = os.environ.get("RELINK_FRONTEND_DIST")
    app = Flask(
        __name__,
        static_folder=frontend_dist if frontend_dist and os.path.isdir(frontend_dist) else None,
        static_url_path="",
    )

    app.config["DATA_DIR"] = os.environ.get(
        "RELINK_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data")
    )
    app.config["UPLOAD_DIR"] = os.environ.get(
        "RELINK_UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads")
    )
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.environ.get("RELINK_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)  # 50 MB default
    )
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    # Vite's default dev server port. Override with RELINK_CORS_ORIGINS
    # (comma-separated) for other setups.
    origins = os.environ.get("RELINK_CORS_ORIGINS", "http://localhost:5173").split(",")
    CORS(app, resources={r"/api/*": {"origins": origins}})

    init_db(app.config["DATA_DIR"])

    from .routes import bp as api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

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
        # Catches malformed-body errors Flask raises itself (e.g. broken
        # multipart) before a route's own request.get_json() runs.
        return jsonify({"error": "Bad request."}), 400

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal server error."}), 500

    if os.environ.get("RELINK_UPLOAD_EXPIRY_DAYS"):
        from .cleanup import start_cleanup_thread
        start_cleanup_thread(app)

    # Section 7: "a single command that starts both the API and serves the
    # frontend build together". Only active when RELINK_FRONTEND_DIST
    # points at a built `npm run build` output (e.g. relink-studio/dist).
    # SPA catch-all: anything that isn't /api/* and isn't a real static
    # file falls through to index.html so client-side routing still works.
    if app.static_folder:
        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path):
            if path and os.path.exists(os.path.join(app.static_folder, path)):
                return send_from_directory(app.static_folder, path)
            return send_from_directory(app.static_folder, "index.html")

    return app
