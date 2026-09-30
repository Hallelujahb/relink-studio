"""Tests for the shared-folder, file-details and sharing-mode routes."""
import os


def test_file_details_and_library_import(client, app, tmp_path, monkeypatch):
    (tmp_path / "a.csv").write_text("id,name\n1,x\n2,y\n")
    monkeypatch.setenv("RELINK_LIBRARY_DIR", str(tmp_path))
    listing = client.get("/api/library").get_json()
    assert listing["enabled"] and [f["name"] for f in listing["files"]] == ["a.csv"]
    r = client.post("/api/library/import", json={"filename": "a.csv"})
    assert r.status_code == 201
    fid = r.get_json()["file_id"]
    assert client.get(f"/api/upload/{fid}").get_json()["row_count"] == 2
    assert client.get("/api/upload/nope").status_code == 404


def test_library_rejects_paths(client, tmp_path, monkeypatch):
    monkeypatch.setenv("RELINK_LIBRARY_DIR", str(tmp_path))
    for bad in ("../x.csv", "/etc/passwd", "sub/a.csv", ".hidden.csv", "missing.csv"):
        assert client.post("/api/library/import", json={"filename": bad}).status_code in (400, 404)


def test_switch_mode_needs_relink_sh(client, monkeypatch):
    monkeypatch.delenv("RELINK_RESTART_FILE", raising=False)
    assert client.post("/api/admin/switch-mode", json={"mode": "lan"}).status_code == 501
