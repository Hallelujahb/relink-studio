"""Regression tests for the security/consistency fixes in apply_fixes.py."""
import os

import numpy as np

from app.matching_embedding import method_c_embedding


def _hash_encoder(texts):
    # Deterministic char-trigram hashing: similar strings -> similar vectors.
    out = np.zeros((len(texts), 64), dtype="float32")
    for i, t in enumerate(texts):
        for j in range(len(t) - 2):
            out[i, hash(t[j:j + 3]) % 64] += 1
        n = np.linalg.norm(out[i])
        if n:
            out[i] /= n
    return out


def test_embedding_method_picks_nearest_target():
    src = [{"id": "1", "name": "Riverside Distribution Center"}]
    tgt = [{"tid": "9", "tname": "Springfield Clinic"}, {"tid": "7", "tname": "Riverside Distribution Centre"}]
    res, meta = method_c_embedding(src, tgt, "id", "name", "tid", "tname", {}, 0.1, encoder=_hash_encoder)
    assert res["1"]["targetId"] == "7"


def test_embedding_respects_floor_and_empty_names():
    src = [{"id": "1", "name": "zzzz"}, {"id": "2", "name": ""}]
    tgt = [{"tid": "7", "tname": "Riverside"}]
    res, _ = method_c_embedding(src, tgt, "id", "name", "tid", "tname", {}, 0.99, encoder=_hash_encoder)
    assert res["1"]["score"] is None and res["2"]["score"] is None


def test_lead_registration_is_not_open(client):
    assert client.post("/api/auth/register", json={"username": "first", "password": "password123"}).status_code == 201
    r = client.post("/api/auth/register", json={"username": "sneaky", "password": "password123", "role": "lead"})
    assert r.status_code == 403


def test_global_auth_guard(app, client):
    app.config["RELINK_REQUIRE_AUTH"] = True
    assert client.get("/health").status_code == 200
    assert client.get("/api/projects/nope").status_code == 401
    assert client.post("/api/upload", data={}).status_code == 401
    boot = client.post("/api/auth/register", json={"username": "boss", "password": "password123", "role": "lead"})
    assert boot.status_code == 201  # first user bootstraps
    assert client.post("/api/auth/register", json={"username": "x", "password": "password123"}).status_code == 403
    tok = client.post("/api/auth/login", json={"username": "boss", "password": "password123"}).get_json()["token"]
    assert client.get("/api/projects/nope", headers={"Authorization": f"Bearer {tok}"}).status_code == 404


def test_cleanup_refuses_paths_outside_upload_dir(app, client, tmp_path):
    victim = tmp_path / "important.txt"
    victim.write_text("keep me")
    r = client.post("/api/admin/cleanup", json={"items": [{"path": str(victim), "bytes": 1}], "confirm": True})
    assert r.status_code == 400
    assert victim.exists()


def test_cleanup_deletes_inside_upload_dir(app, client):
    f = os.path.join(app.config["UPLOAD_DIR"], "stale.tmp")
    open(f, "w").write("x")
    r = client.post("/api/admin/cleanup", json={"items": [{"path": f, "bytes": 1}], "confirm": True})
    assert r.status_code == 200 and not os.path.exists(f)
