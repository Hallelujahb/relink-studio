import os
import shutil
import tempfile

import pytest

from app import create_app, db as db_module


def _drop_conn():
    # db.py keeps one sqlite connection per thread. Tests share a thread, so a
    # stale one would keep pointing at the previous test's deleted database.
    if hasattr(db_module._local, "conn"):
        db_module._local.conn.close()
        del db_module._local.conn


@pytest.fixture
def app():
    data_dir, upload_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
    saved = {k: os.environ.get(k) for k in ("RELINK_DATA_DIR", "RELINK_UPLOAD_DIR", "RELINK_REQUIRE_AUTH")}
    os.environ["RELINK_DATA_DIR"] = data_dir
    os.environ["RELINK_UPLOAD_DIR"] = upload_dir
    os.environ.pop("RELINK_REQUIRE_AUTH", None)
    _drop_conn()

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    yield flask_app

    _drop_conn()
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(upload_dir, ignore_errors=True)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
