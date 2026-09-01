"""Entry point for the `relink-studio-api` console script (see pyproject.toml)."""
import os


def main():
    from . import create_app
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("RELINK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
