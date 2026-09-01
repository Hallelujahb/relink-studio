import os
import shutil
import tempfile

import pytest

from app import create_app, db as db_module


@pytest.fixture
def app():
    data_dir = tempfile.mkdtemp()
    upload_dir = tempfile.mkdtemp()
    os.environ["RELINK_DATA_DIR"] = data_dir
    os.environ["RELINK_UPLOAD_DIR"] = upload_dir
    os.environ.pop("RELINK_REQUIRE_AUTH", None)

    # db.py caches one sqlite3 connection per thread in a module-level
    # thread-local. Tests run in the same thread, so without clearing this
    # each test would silently keep talking to the previous test's (now
    # deleted) database file.
    if hasattr(db_module._local, "conn"):
        db_module._local.conn.close()
        del db_module._local.conn

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    yield flask_app

    if hasattr(db_module._local, "conn"):
        db_module._local.conn.close()
        del db_module._local.conn
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
