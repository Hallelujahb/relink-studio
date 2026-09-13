import sys

from .config import ConfigError


def main():
    try:
        from . import create_app
        app = create_app()
    except ConfigError as e:
        print(f"Relink configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    cfg = app.config["RELINK_CONFIG"]
    cfg.print_banner()
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)


if __name__ == "__main__":
    main()

