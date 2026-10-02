"""Regression tests for the extra endpoints, metrics, sampling and schema helpers."""
import io
import os

from app import metrics, sampling, schema_tools
from app.db import get_conn
from test_hardening import CONFIG, SRC, TGT, _run, _upload_bytes


def test_cleanup_writes_its_audit_row_and_forgets_the_file(app, client):
    up = _upload_bytes(client, "old.csv", b"id,name\n1,A\n")
    parsed = os.path.join(app.config["UPLOAD_DIR"], f"{up['file_id']}.parsed.json")
    r = client.post("/api/admin/cleanup", json={"items": [{"path": parsed, "bytes": 1}], "confirm": True})
    assert r.status_code == 200 and r.get_json()["deleted_count"] == 1
    assert get_conn().execute("SELECT 1 FROM files WHERE id=?", (up["file_id"],)).fetchone() is None
    assert get_conn().execute("SELECT action FROM audit_log WHERE project_id='system'").fetchone()["action"] == "retention_cleanup"


def test_cleanup_will_not_touch_files_a_project_uses(app, client):
    pid, _ = _run(client, SRC, TGT)
    src_id = get_conn().execute("SELECT source_file_id FROM projects WHERE id=?", (pid,)).fetchone()[0]
    parsed = os.path.join(app.config["UPLOAD_DIR"], f"{src_id}.parsed.json")
    r = client.post("/api/admin/cleanup", json={"items": [{"path": parsed, "bytes": 1}], "confirm": True})
    assert r.status_code == 400 and os.path.exists(parsed)


def test_logged_in_user_cannot_claim_to_be_someone_else(client):
    pid, _ = _run(client, SRC, TGT)
    client.post("/api/auth/register", json={"username": "real", "password": "password123"})
    tok = client.post("/api/auth/login", json={"username": "real", "password": "password123"}).get_json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    client.post(f"/api/projects/{pid}/review/1/decision", json={"decision_type": "defer", "reviewer": "someone else"}, headers=h)
    hist = client.get(f"/api/projects/{pid}/review/1/decision/history").get_json()["history"]
    assert hist[0]["reviewer"] == "real"


def test_drift_leaves_a_snapshot_so_the_next_upload_can_be_compared(client):
    a = _upload_bytes(client, "same.csv", b"id,name\n1,A\n")
    assert client.get(f"/api/upload/{a['file_id']}/schema/drift").status_code == 404
    b = _upload_bytes(client, "same.csv", b"id,name,email\n1,A,a@x.com\n")
    d = client.get(f"/api/upload/{b['file_id']}/schema/drift").get_json()
    assert d["has_drift"] and d["added_fields"] == ["email"]


def test_metrics_and_sampling_ignore_method_d():
    rows = [{"sourceId": "1", "approved": True, "kind": "consensus", "decision": None,
             "methods": {"A": {"targetId": "t1", "score": 0.9}, "D": {"targetId": None, "score": 1.0}}}]
    assert metrics.run_metrics(rows, 1, 1)["score_max"] == 0.9
    assert sampling.highest_confidence_sample(rows, 1)[0]["sourceId"] == "1"
    assert metrics.evaluation_metrics(rows, {"1": "t1"})["true_positives"] == 1


def test_wrong_approved_link_counts_as_false_positive_and_f1_is_zero_not_none():
    rows = [{"sourceId": "1", "approved": True, "kind": "consensus", "methods": {"A": {"targetId": "wrong", "score": 0.9}}}]
    r = metrics.evaluation_metrics(rows, {"1": "right"})
    assert r["false_positives"] == 1 and r["false_negatives"] == 1
    assert r["precision"] == 0.0 and r["f1_score"] == 0.0


def test_schema_roles_use_whole_words():
    cols = ["hotel", "width", "user_id", "created_at", "Email"]
    roles = {f["name"]: f["semantic_role"] for f in schema_tools.discover_schema(cols, [{c: "x" for c in cols}])["fields"]}
    assert roles["hotel"] == "free_text" and roles["width"] == "free_text"
    assert roles["user_id"] == "primary_identifier" and roles["created_at"] == "date" and roles["Email"] == "email"
    old = schema_tools.discover_schema(["a"], [{"a": "x"}])
    new = schema_tools.discover_schema(["a"], [{"a": "1"}])
    assert schema_tools.diff_schema(old, new)["has_drift"]


def test_cp1252_csv_and_geojson_columns(client):
    up = _upload_bytes(client, "win.csv", "id,name\n1,Café\n".encode("cp1252"))
    assert up["row_count"] == 1
    gj = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"a":1},"geometry":null},{"type":"Feature","properties":{"b":2},"geometry":null}]}'
    assert _upload_bytes(client, "x.geojson", gj)["columns"] == ["a", "b"]


def test_database_values_that_json_cannot_hold_are_saved(tmp_path):
    import datetime, decimal
    from app.parsers import load_parsed_payload, save_parsed_payload
    save_parsed_payload(str(tmp_path), "f", {"rows": [{"d": datetime.date(2024, 1, 2), "n": decimal.Decimal("1.5")}]})
    assert load_parsed_payload(str(tmp_path), "f")["rows"][0] == {"d": "2024-01-02", "n": "1.5"}
