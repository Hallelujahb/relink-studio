"""Dev entrypoint: `python run.py`. For the packaged console script, see
app/cli.py (`relink-studio-api` after `pip install .`); both share the
same host/port/debug env vars."""
from app.cli import main

if __name__ == "__main__":
    main()
