import io
import json
import os
import time

BASE_CONFIG = {
    "project_name": "batch8_test",
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
    return project_id, src, tgt


# --------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------- #

def test_profile_upload_returns_column_stats(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    resp = client.get(f"/api/upload/{src['file_id']}/profile")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["row_count"] == 5
    assert any(c["column"] == "id" for c in data["columns"])


def test_profile_upload_with_identifier_warnings(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    resp = client.get(f"/api/upload/{src['file_id']}/profile?id_column=id")
    assert resp.status_code == 200
    assert "identifier_warnings" in resp.get_json()


def test_profile_upload_unknown_file_404s(client):
    resp = client.get("/api/upload/doesnotexist/profile")
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# Schema + drift
# --------------------------------------------------------------------- #

def test_get_upload_schema(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    resp = client.get(f"/api/upload/{src['file_id']}/schema")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["field_count"] == 2


def test_schema_drift_404s_without_prior_snapshot(client, fixtures_dir):
    src = _upload(client, fixtures_dir, "source.csv")
    resp = client.get(f"/api/upload/{src['file_id']}/schema/drift")
    assert resp.status_code == 404


def test_schema_drift_detects_change_across_reuploads(client, fixtures_dir, tmp_path):
    src1 = _upload(client, fixtures_dir, "source.csv")
    client.get(f"/api/upload/{src1['file_id']}/schema")  # stores a snapshot

    # Re-upload a file with the SAME filename but a different column shape.
    changed = tmp_path / "source.csv"
    changed.write_text("id,name,email\n1,Alpha,a@x.com\n")
    resp = client.post("/api/upload", data={"file": (io.BytesIO(changed.read_bytes()), "source.csv")}, content_type="multipart/form-data")
    src2 = resp.get_json()

    resp = client.get(f"/api/upload/{src2['file_id']}/schema/drift")
    assert resp.status_code == 200
    diff = resp.get_json()
    assert diff["has_drift"] is True
    assert "email" in diff["added_fields"]


# --------------------------------------------------------------------- #
# Blocking diagnostics + metrics
# --------------------------------------------------------------------- #

def test_blocking_diagnostics(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/diagnostics/blocking")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total_target_blocks" in data


def test_metrics_endpoint(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/metrics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tier"] == "observed"
    assert data["total_source_records"] == 5


def test_metrics_evaluate_without_ground_truth_is_unavailable(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/metrics/evaluate", json={})
    assert resp.status_code == 200
    assert resp.get_json()["tier"] == "unavailable"


def test_metrics_evaluate_with_ground_truth(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/metrics/evaluate", json={"ground_truth": {"1": "101"}})
    assert resp.status_code == 200
    assert resp.get_json()["tier"] == "ground_truth"


# --------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------- #

def test_review_sample_random(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/review/sample?strategy=random&n=2&seed=1")
    assert resp.status_code == 200
    assert resp.get_json()["count"] <= 2


def test_review_sample_unknown_strategy(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/review/sample?strategy=bogus")
    assert resp.status_code == 400


def test_review_sample_by_kind_requires_kind_param(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/review/sample?strategy=by_kind")
    assert resp.status_code == 400


def test_review_sample_by_kind(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.get(f"/api/projects/{project_id}/review/sample?strategy=by_kind&kind=type_leak")
    assert resp.status_code == 200


# --------------------------------------------------------------------- #
# Structured review decisions
# --------------------------------------------------------------------- #

def test_record_review_decision(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/review/1/decision",
                        json={"decision_type": "approve", "reviewer": "carol", "target_id": "101"})
    assert resp.status_code == 201


def test_record_review_decision_invalid_type(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/review/1/decision", json={"decision_type": "maybe"})
    assert resp.status_code == 400


def test_record_review_decision_invalid_reason(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/review/1/decision",
                        json={"decision_type": "reject", "reason": "not_a_real_reason"})
    assert resp.status_code == 400


def test_review_decision_history(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    client.post(f"/api/projects/{project_id}/review/1/decision",
                json={"decision_type": "approve", "reviewer": "carol"})
    client.post(f"/api/projects/{project_id}/review/1/decision",
                json={"decision_type": "reject", "reviewer": "dave", "reason": "wrong_entity"})
    resp = client.get(f"/api/projects/{project_id}/review/1/decision/history")
    assert resp.status_code == 200
    history = resp.get_json()["history"]
    assert len(history) == 2
    assert history[-1]["reviewer"] == "dave"


# --------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------- #

def test_create_and_list_exception(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/exceptions",
                        json={"scope": "allowed_type_mismatch", "reason": "known case", "creator": "carol"})
    assert resp.status_code == 201
    exc_id = resp.get_json()["id"]

    resp = client.get(f"/api/projects/{project_id}/exceptions")
    assert resp.status_code == 200
    assert len(resp.get_json()["exceptions"]) == 1


def test_create_exception_invalid_scope(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/exceptions", json={"scope": "nonsense", "reason": "x"})
    assert resp.status_code == 400


def test_create_exception_missing_reason(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/exceptions", json={"scope": "ignored_record"})
    assert resp.status_code == 400


def test_deactivate_exception(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/exceptions",
                        json={"scope": "ignored_record", "reason": "x", "creator": "carol"})
    exc_id = resp.get_json()["id"]

    resp = client.post(f"/api/projects/{project_id}/exceptions/{exc_id}/deactivate",
                        json={"actor": "dave", "reason": "no longer needed"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["active"] is False
    assert data["history"][0]["actor"] == "dave"

    resp = client.get(f"/api/projects/{project_id}/exceptions?active_only=true")
    assert len(resp.get_json()["exceptions"]) == 0


def test_deactivate_unknown_exception_404s(client, fixtures_dir):
    project_id, _, _ = _create_and_run_project(client, fixtures_dir)
    resp = client.post(f"/api/projects/{project_id}/exceptions/doesnotexist/deactivate", json={})
    assert resp.status_code == 404


# --------------------------------------------------------------------- #
# Retention / cleanup
# --------------------------------------------------------------------- #

def test_admin_storage_summary(client, fixtures_dir):
    _upload(client, fixtures_dir, "source.csv")
    resp = client.get("/api/admin/storage")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["uploads"]["exists"] is True
    assert data["uploads"]["file_count"] >= 1


def test_admin_cleanup_preview_protects_referenced_files(client, fixtures_dir):
    project_id, src, tgt = _create_and_run_project(client, fixtures_dir)
    resp = client.get("/api/admin/cleanup/preview?max_age_days=0")
    assert resp.status_code == 200
    candidates = resp.get_json()["candidates"]
    # source/target files are referenced by the project, so they must never
    # appear as cleanup candidates even with max_age_days=0.
    paths = [c["path"] for c in candidates]
    assert not any(src["file_id"] in p for p in paths)
    assert not any(tgt["file_id"] in p for p in paths)


def test_admin_cleanup_requires_confirm(client, fixtures_dir):
    resp = client.post("/api/admin/cleanup", json={"items": [{"path": "/tmp/whatever", "bytes": 1}], "confirm": False})
    assert resp.status_code == 400


def test_admin_cleanup_requires_items(client, fixtures_dir):
    resp = client.post("/api/admin/cleanup", json={"confirm": True})
    assert resp.status_code == 400


# --------------------------------------------------------------------- #
# Connector capabilities
# --------------------------------------------------------------------- #

def test_connector_capabilities_endpoint(client):
    resp = client.get("/api/connectors/capabilities")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "postgresql" in data and "clickhouse" in data and "file" in data
