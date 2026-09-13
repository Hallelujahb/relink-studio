import pytest

from app import match_config as mc


def test_add_and_get_version():
    hist = mc.ConfigHistory()
    v1 = hist.add({"auto_approve": 0.9}, name="initial")
    assert v1.version == 1
    assert hist.get(1).config["auto_approve"] == 0.9


def test_latest_returns_most_recent():
    hist = mc.ConfigHistory()
    hist.add({"a": 1})
    v2 = hist.add({"a": 2})
    assert hist.latest().version == v2.version


def test_clone_creates_new_version_with_same_config():
    hist = mc.ConfigHistory()
    hist.add({"a": 1})
    cloned = hist.clone(1)
    assert cloned.version == 2
    assert cloned.config == {"a": 1}


def test_restore_does_not_rewrite_history():
    hist = mc.ConfigHistory()
    hist.add({"a": 1})
    hist.add({"a": 2})
    restored = hist.restore(1)
    assert restored.version == 3
    assert hist.get(1).config == {"a": 1}  # original untouched


def test_clone_unknown_version_raises():
    hist = mc.ConfigHistory()
    with pytest.raises(ValueError):
        hist.clone(99)


def test_compare_configs_detects_nested_changes():
    hist = mc.ConfigHistory()
    hist.add({"thresholds": {"auto_approve": 0.9}})
    hist.add({"thresholds": {"auto_approve": 0.95}})
    diff = hist.compare_configs(1, 2)
    assert diff["has_changes"] is True
    assert diff["changes"][0]["field"] == "thresholds.auto_approve"


ROW_A = {"sourceId": "1", "approved": True, "kind": "consensus",
         "methods": {"A": {"targetId": "t1", "score": 0.95}}}
ROW_B_OLD = {"sourceId": "2", "approved": False, "kind": "unmatched", "methods": {}}
ROW_B_NEW = {"sourceId": "2", "approved": True, "kind": "consensus",
             "methods": {"A": {"targetId": "t2", "score": 0.9}}}


def test_dry_run_compare_detects_newly_matched():
    result = mc.dry_run_compare([ROW_A, ROW_B_OLD], [ROW_A, ROW_B_NEW])
    assert "2" in result["newly_matched"]
    assert any(c["sourceId"] == "2" for c in result["changed_approvals"])


def test_dry_run_compare_detects_newly_unmatched():
    result = mc.dry_run_compare([ROW_A, ROW_B_NEW], [ROW_A, ROW_B_OLD])
    assert "2" in result["newly_unmatched"]


def test_dry_run_compare_detects_score_change():
    old = [{"sourceId": "1", "approved": True, "kind": "consensus", "methods": {"A": {"targetId": "t1", "score": 0.8}}}]
    new = [{"sourceId": "1", "approved": True, "kind": "consensus", "methods": {"A": {"targetId": "t1", "score": 0.95}}}]
    result = mc.dry_run_compare(old, new)
    assert result["score_changes"][0] == {"sourceId": "1", "old_score": 0.8, "new_score": 0.95}


def test_dry_run_compare_new_collision():
    old = [{"sourceId": "1", "approved": True, "kind": "consensus", "methods": {}}]
    new = [{"sourceId": "1", "approved": False, "kind": "collision", "methods": {}}]
    result = mc.dry_run_compare(old, new)
    assert "1" in result["new_collisions"]
