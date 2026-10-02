import io
import json
import time

BASE_CONFIG = {
    "project_name": "int_test",
    "source": {"id_column": "id", "match_column": "name"},
    "target": {"id_column": "tid", "match_column": "tname", "type_column": "ttype", "type_expected": "municipal"},
    "methods": ["fuzzy"],
    "thresholds": {"auto_approve": 0.7, "needs_review": 0.4},
    "matching": {"blocking_floor": 0.3, "strip_suffix_words": True},
    "safety": {"collision_guard": True},
}


def _upload(client, fixtures_dir, filename):
    with open(f"{fixtures_dir}/{filename}", "rb") as f:
        resp = client.post("/api/upload", data={"file": (io.BytesIO(f.read()), filename)}, content_type="multipart/form-data")
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _create_and_run_project(client, fixtures_dir, config=None):
    src = _upload(client, fixtures_dir, "source.csv")
    tgt = _upload(client, fixtures_dir, "target.csv")
    body = {"config": config or BASE_CONFIG, "source_file_id": src["file_id"], "target_file_id": tgt["file_id"]}
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 201, resp.get_json()
    project_id = resp.get_json()["id"]

    resp = client.post(f"/api/projects/{project_id}/run")
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    for _ in range(50):
        status = client.get(f"/api/projects/{project_id}/jobs/{job_id}").get_json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert status["status"] == "done", status
    return project_id


def test_upload_rejects_missing_file(client):
    resp = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_upload_rejects_unsupported_extension(client):
    resp = client.post("/api/upload", data={"file": (io.BytesIO(b"hi"), "notes.txt")}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_full_review_flow(client, fixtures_dir):
    project_id = _create_and_run_project(client, fixtures_dir)

    review = client.get(f"/api/projects/{project_id}/review").get_json()
    assert review["total"] == 5
    by_id = {r["sourceId"]: r for r in review["rows"]}
    assert by_id["2"]["kind"] == "type_leak"  # Riverside County matched a 'county' target, expected 'municipal'

    # Filter by kind
    filtered = client.get(f"/api/projects/{project_id}/review?filter=type_leak").get_json()
    assert filtered["total"] == 1

    # Single patch
    resp = client.patch(f"/api/projects/{project_id}/review/2", json={"approved": True, "chosen_method": "A"})
    assert resp.status_code == 200
    assert resp.get_json()["row"]["approved"] is True

    # Bulk reject + undo
    resp = client.post(f"/api/projects/{project_id}/review/bulk", json={"action": "reject", "source_ids": ["1", "3"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["affected_count"] == 2
    undo_token = body["undo_token"]

    row1_after_reject = client.get(f"/api/projects/{project_id}/review?search=Springfield").get_json()["rows"][0]
    assert row1_after_reject["approved"] is False

    undo_resp = client.post(f"/api/projects/{project_id}/review/undo", json={"undo_token": undo_token})
    assert undo_resp.status_code == 200
    assert undo_resp.get_json()["reverted_count"] == 2

    # Export
    export_resp = client.post(f"/api/projects/{project_id}/export")
    assert export_resp.status_code == 200
    csv_text = export_resp.data.decode("utf-8")
    assert "source_id,source_name,target_id,target_name,method,score" in csv_text

    # Audit log is real, not seeded
    audit = client.get(f"/api/projects/{project_id}/audit").get_json()
    actions = [e["action"] for e in audit["entries"]]
    assert "project_created" in actions
    assert "run_completed" in actions
    assert "bulk_reject" in actions
    assert "undo_applied" in actions
    assert "exported" in actions
    assert all(e["actor"] == "anonymous" for e in audit["entries"])  # no auth token was sent


def test_project_creation_requires_known_file_ids(client):
    resp = client.post("/api/projects", json={"config": BASE_CONFIG, "source_file_id": "nope", "target_file_id": "also_nope"})
    assert resp.status_code == 400


def test_get_missing_project_404s(client):
    resp = client.get("/api/projects/doesnotexist")
    assert resp.status_code == 404
    assert resp.get_json()["error"]


def test_undo_with_unknown_token_errors(client, fixtures_dir):
    project_id = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/review/undo", json={"undo_token": "not-a-real-token"})
    assert resp.status_code == 404


def test_auth_register_login_and_audit_attribution(client, fixtures_dir):
    resp = client.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    assert resp.status_code == 201

    resp = client.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    assert resp.status_code == 400  # duplicate username

    resp = client.post("/api/auth/login", json={"username": "carol", "password": "wrong"})
    assert resp.status_code == 401

    resp = client.post("/api/auth/login", json={"username": "carol", "password": "password123"})
    assert resp.status_code == 200
    token = resp.get_json()["token"]

    src = _upload(client, fixtures_dir, "source.csv")
    tgt = _upload(client, fixtures_dir, "target.csv")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post("/api/projects", json={
        "config": BASE_CONFIG, "source_file_id": src["file_id"], "target_file_id": tgt["file_id"],
    }, headers=headers)
    project_id = resp.get_json()["id"]

    audit = client.get(f"/api/projects/{project_id}/audit").get_json()
    assert audit["entries"][0]["actor"] == "carol"


def test_approval_hierarchy_requires_lead_role(client, fixtures_dir):
    client.post("/api/auth/register", json={"username": "lead1", "password": "password123", "role": "lead"})
    client.post("/api/auth/register", json={"username": "rev1", "password": "password123"})
    lead_token = client.post("/api/auth/login", json={"username": "lead1", "password": "password123"}).get_json()["token"]
    rev_token = client.post("/api/auth/login", json={"username": "rev1", "password": "password123"}).get_json()["token"]

    config = dict(BASE_CONFIG)
    config["safety"] = {"require_approval_hierarchy": True}
    project_id = _create_and_run_project(client, fixtures_dir, config=config)

    # A reviewer cannot approve directly.
    resp = client.post(f"/api/projects/{project_id}/review/1/approve", headers={"Authorization": f"Bearer {rev_token}"})
    assert resp.status_code == 403

    # A lead cannot approve before it's submitted.
    resp = client.post(f"/api/projects/{project_id}/review/1/approve", headers={"Authorization": f"Bearer {lead_token}"})
    assert resp.status_code == 400

    # Reviewer submits, then lead approves.
    resp = client.post(f"/api/projects/{project_id}/review/1/submit", headers={"Authorization": f"Bearer {rev_token}"})
    assert resp.status_code == 200
    assert resp.get_json()["submittedBy"] == "rev1"

    resp = client.post(f"/api/projects/{project_id}/review/1/approve", headers={"Authorization": f"Bearer {lead_token}"})
    assert resp.status_code == 200
    assert resp.get_json()["approved"] is True
    assert resp.get_json()["approvedBy"] == "lead1"


def test_delete_upload_blocked_while_referenced_by_project(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    tgt = _upload(client, fixtures_dir, "target.csv")
    client.post("/api/projects", json={"config": BASE_CONFIG, "source_file_id": src["file_id"], "target_file_id": tgt["file_id"]})

    resp = client.delete(f"/api/upload/{src['file_id']}")
    assert resp.status_code == 409


def test_delete_upload_succeeds_when_unreferenced(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    resp = client.delete(f"/api/upload/{src['file_id']}")
    assert resp.status_code == 204
