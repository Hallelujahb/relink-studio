"""Regression tests for matching correctness, review integrity and access control."""
import io
import json
import time

import pytest

from app import matching
from app.db import get_conn

CFG = {"strip_parentheticals": True, "strip_suffix_words": True, "keep_digits": True, "case_sensitive": False}

CONFIG = {
    "project_name": "hardening",
    "source": {"id_column": "id", "match_column": "name"},
    "target": {"id_column": "tid", "match_column": "tname", "type_column": "ttype", "type_expected": "municipal"},
    "methods": ["fuzzy"],
    "thresholds": {"auto_approve": 0.7, "needs_review": 0.4},
    "matching": {"blocking_floor": 0.3},
    "safety": {"collision_guard": True},
}


def _upload_bytes(client, name, data):
    r = client.post("/api/upload", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _run(client, src_csv, tgt_csv, config=None, wait=True):
    src = _upload_bytes(client, "s.csv", src_csv.encode())
    tgt = _upload_bytes(client, "t.csv", tgt_csv.encode())
    r = client.post("/api/projects", json={"config": config or CONFIG, "source_file_id": src["file_id"], "target_file_id": tgt["file_id"]})
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    return pid, _start(client, pid, wait)


def _start(client, pid, wait=True, body=None):
    r = client.post(f"/api/projects/{pid}/run", json=body or {})
    if r.status_code != 202:
        return r
    job = r.get_json()["job_id"]
    for _ in range(100):
        status = client.get(f"/api/projects/{pid}/jobs/{job}").get_json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError("job never finished")


SRC = "id,name\n1,Springfield Township\n2,Oak Grove Village\n3,Nowhere Land\n"
TGT = "tid,tname,ttype\n101,Springfield Twp,municipal\n102,Oak Grove Vlg,municipal\n"


# --- matching ---------------------------------------------------------------

def test_non_latin_names_are_not_erased():
    assert matching.normalize("አዲስ አበባ", CFG) == "አዲስ አበባ"
    assert matching.normalize("Café Münster", CFG) == "cafe munster"


def test_empty_names_never_match():
    res = matching.method_a_fuzzy([{"id": "1", "n": "!!!"}], [{"t": "9", "n": "???"}], "id", "n", "t", "n", CFG, 0.1)
    assert res["1"]["score"] is None
    res = matching.method_b_token_overlap([{"id": "1", "n": ""}], [{"t": "9", "n": ""}], "id", "n", "t", "n", CFG, 0.0)
    assert res["1"]["score"] is None


def test_suffix_words_only_trail_and_never_empty_a_name():
    assert matching.normalize("City Hall", CFG) == "city hall"
    assert matching.normalize("County", CFG) == "county"
    assert matching.normalize("Lakeview City", CFG) == "lakeview"
    assert matching.normalize("Acme", {**CFG, "suffix_words": ["acme"]}) == "acme"


def _combine(method_results, config, target_rows=None):
    return matching.combine_methods([{"id": "1", "name": "x"}], "id", "name", method_results,
                                    target_rows or [{"tid": "a"}, {"tid": "b"}], "tid", None, None, config)


def test_methods_pointing_at_different_targets_need_review():
    results = {"A": {"1": {"name": "A", "targetId": "a", "score": 0.96}},
               "B": {"1": {"name": "B", "targetId": "b", "score": 0.97}}}
    rows = _combine(results, {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}})
    assert rows[0]["approved"] is False


def test_weak_claims_do_not_trigger_collisions_and_many_to_one_is_honoured():
    src = [{"id": "1", "name": "x"}, {"id": "2", "name": "y"}]
    results = {"A": {"1": {"name": "T", "targetId": "a", "score": 0.97}, "2": {"name": "T", "targetId": "a", "score": 0.30}}}
    cfg = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {"collision_guard": True}}
    rows = matching.combine_methods(src, "id", "name", results, [{"tid": "a"}], "tid", None, None, cfg)
    assert rows[0]["approved"] is True and rows[0]["kind"] == "consensus"

    results["A"]["2"]["score"] = 0.95
    rows = matching.combine_methods(src, "id", "name", results, [{"tid": "a"}], "tid", None, None, cfg)
    assert {r["kind"] for r in rows} == {"collision"}
    rows = matching.combine_methods(src, "id", "name", results, [{"tid": "a"}], "tid", None, None,
                                    {**cfg, "target": {"allow_many_to_one": True}})
    assert all(r["approved"] for r in rows)


def test_missing_target_type_is_a_type_leak():
    results = {"A": {"1": {"name": "T", "targetId": "a", "score": 0.99}}}
    rows = matching.combine_methods([{"id": "1", "name": "x"}], "id", "name", results,
                                    [{"tid": "a", "ttype": None}], "tid", "ttype", "municipal",
                                    {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}})
    assert rows[0]["kind"] == "type_leak"


def test_geometry_centroids():
    multi = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [0, 2], [2, 2], [0, 0]]], [[[10, 10], [10, 12], [12, 12], [10, 10]]]]}
    assert matching._centroid(multi) is not None
    assert matching._centroid({"type": "Point", "coordinates": [500000, 4000000]}) is None
    assert matching._centroid({"type": "LineString", "coordinates": [[0, 0], [2, 2]]}) == (1.0, 1.0)


# --- runs -------------------------------------------------------------------

def test_duplicate_blank_and_slash_ids_fail_the_run_with_a_reason(client):
    _, status = _run(client, "id,name\n1,A\n1,B\n", TGT)
    assert status["status"] == "error" and "repeated" in status["message"]
    _, status = _run(client, "id,name\n,A\n2,B\n", TGT)
    assert status["status"] == "error" and "empty" in status["message"]
    _, status = _run(client, "id,name\nET/01,A\n", TGT)
    assert status["status"] == "error" and "'/'" in status["message"]


def test_config_columns_are_validated(client):
    src = _upload_bytes(client, "s.csv", SRC.encode())
    tgt = _upload_bytes(client, "t.csv", TGT.encode())
    bad = {**CONFIG, "source": {"id_column": "id", "match_column": "nope"}}
    r = client.post("/api/projects", json={"config": bad, "source_file_id": src["file_id"], "target_file_id": tgt["file_id"]})
    assert r.status_code == 400 and "nope" in r.get_json()["error"]


def test_rerun_needs_force_once_decisions_exist_and_failed_run_keeps_results(client):
    pid, status = _run(client, SRC, TGT)
    assert status["status"] == "done"
    client.patch(f"/api/projects/{pid}/review/1", json={"approved": False})
    r = client.post(f"/api/projects/{pid}/run", json={})
    assert r.status_code == 409
    assert _start(client, pid, body={"force": True})["status"] == "done"

    before = client.get(f"/api/projects/{pid}/review").get_json()["total"]
    conn = get_conn()
    conn.execute("UPDATE projects SET config_json=? WHERE id=?", (json.dumps({**CONFIG, "source": {"id_column": "id", "match_column": "gone"}}), pid))
    conn.commit()
    assert _start(client, pid)["status"] == "error"
    assert client.get(f"/api/projects/{pid}/review").get_json()["total"] == before


def test_stale_jobs_are_closed_on_startup(app):
    from app.db import init_db
    conn = get_conn()
    conn.execute("INSERT INTO projects (id,name,config_json) VALUES ('p','p','{}')")
    conn.execute("INSERT INTO jobs (id,project_id,status) VALUES ('j','p','running')")
    conn.commit()
    init_db(app.config["DATA_DIR"])
    assert conn.execute("SELECT status FROM jobs WHERE id='j'").fetchone()["status"] == "error"


# --- review integrity -------------------------------------------------------

def test_reject_is_remembered_and_unmatched_cannot_be_approved(client):
    pid, _ = _run(client, SRC, TGT)
    client.patch(f"/api/projects/{pid}/review/1", json={"approved": False})
    rows = {r["sourceId"]: r for r in client.get(f"/api/projects/{pid}/review").get_json()["rows"]}
    assert rows["1"]["decision"] == "rejected" and rows["1"]["approved"] is False
    assert client.get(f"/api/projects/{pid}/review?filter=rejected").get_json()["total"] == 1

    assert rows["3"]["kind"] == "unmatched"
    assert client.patch(f"/api/projects/{pid}/review/3", json={"approved": True}).status_code == 400
    r = client.post(f"/api/projects/{pid}/review/bulk", json={"action": "approve", "source_ids": ["3", "2"]}).get_json()
    assert r["affected_count"] == 1 and r["skipped_no_candidate"] == ["3"]
    assert client.patch(f"/api/projects/{pid}/review/1", json={"chosen_method": "Z"}).status_code == 400


def test_undo_will_not_overwrite_a_newer_decision(client):
    pid, _ = _run(client, SRC, TGT)
    bulk = client.post(f"/api/projects/{pid}/review/bulk", json={"action": "reject", "source_ids": ["1", "2"]}).get_json()
    client.patch(f"/api/projects/{pid}/review/1", json={"approved": True})
    undo = client.post(f"/api/projects/{pid}/review/undo", json={"undo_token": bulk["undo_token"]}).get_json()
    assert undo["reverted_count"] == 1 and undo["skipped_count"] == 1
    row1 = client.get(f"/api/projects/{pid}/review?search=Springfield").get_json()["rows"][0]
    assert row1["approved"] is True


def test_approval_hierarchy_cannot_be_bypassed(client):
    client.post("/api/auth/register", json={"username": "boss", "password": "password123", "role": "lead"})
    tok = client.post("/api/auth/login", json={"username": "boss", "password": "password123"}).get_json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    cfg = {**CONFIG, "safety": {"require_approval_hierarchy": True}}
    pid, _ = _run(client, SRC, TGT, cfg)
    assert client.patch(f"/api/projects/{pid}/review/1", json={"approved": True}).status_code == 403
    assert client.post(f"/api/projects/{pid}/review/bulk", json={"action": "approve", "source_ids": ["1"]}).status_code == 403
    client.post(f"/api/projects/{pid}/review/1/submit", headers=h)
    r = client.post(f"/api/projects/{pid}/review/1/approve", headers=h)
    assert r.status_code == 403 and "someone other" in r.get_json()["error"]


def test_exceptions_change_the_run(client):
    pid, _ = _run(client, "id,name\n1,Springfield Township\n2,Ghost Town\n", "tid,tname,ttype\n101,Springfield Twp,county\n")
    conn = get_conn()
    rows = {r["sourceId"]: r for r in client.get(f"/api/projects/{pid}/review").get_json()["rows"]}
    assert rows["1"]["kind"] == "type_leak"
    for scope, sid in (("allowed_type_mismatch", "1"), ("excluded_source", "2")):
        conn.execute("INSERT INTO project_exceptions (id,project_id,scope,reason,creator,affected_source_id) VALUES (?,?,?,?,?,?)",
                     (scope, pid, scope, "test", "me", sid))
    conn.commit()
    _start(client, pid, body={"force": True})
    rows = {r["sourceId"]: r for r in client.get(f"/api/projects/{pid}/review").get_json()["rows"]}
    assert rows["1"]["kind"] == "consensus" and "2" not in rows


def test_export_neutralises_formulas_and_delete_project_cleans_up(client):
    pid, _ = _run(client, "id,name\n1,Springfield Township\n", "tid,tname,ttype\n101,=Springfield Twp,municipal\n")
    csv_text = client.post(f"/api/projects/{pid}/export").data.decode()
    assert "'=Springfield Twp" in csv_text
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.delete("/api/projects/system").status_code == 404


def test_bad_paging_values_are_400_not_500(client):
    pid, _ = _run(client, SRC, TGT)
    assert client.get(f"/api/projects/{pid}/review?page=abc").status_code == 400
    assert client.get(f"/api/projects/{pid}/audit?page_size=x").status_code == 400


# --- access control ---------------------------------------------------------

def test_foreign_host_and_cross_origin_writes_are_refused(client):
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/health", headers={"Host": "localhost:5000"}).status_code == 200
    r = client.post("/api/projects", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={}, headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 400


def test_login_throttle_logout_and_hashed_sessions(client):
    client.post("/api/auth/register", json={"username": "amy", "password": "password123"})
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "amy", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "amy", "password": "password123"}).status_code == 429


def test_tokens_are_stored_hashed_and_logout_revokes(app, client):
    client.post("/api/auth/register", json={"username": "bo", "password": "password123"})
    token = client.post("/api/auth/login", json={"username": "bo", "password": "password123"}).get_json()["token"]
    assert get_conn().execute("SELECT 1 FROM sessions WHERE token=?", (token,)).fetchone() is None
    h = {"Authorization": f"Bearer {token}"}
    app.config["RELINK_REQUIRE_AUTH"] = True
    assert client.get("/api/projects/x", headers=h).status_code == 404
    client.post("/api/auth/logout", headers=h)
    assert client.get("/api/projects/x", headers=h).status_code == 401


def test_first_account_in_lan_mode_must_come_from_this_machine(app, client):
    app.config["RELINK_REQUIRE_AUTH"] = True
    body = {"username": "x", "password": "password123", "role": "lead"}
    assert client.post("/api/auth/register", json=body, environ_base={"REMOTE_ADDR": "192.168.1.9"}).status_code == 403
    assert client.post("/api/auth/register", json=body).status_code == 201
