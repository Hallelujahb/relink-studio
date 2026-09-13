import os
import time

from app import retention as rt


def test_storage_usage_summary_missing_dir_reports_not_exists(tmp_path):
    result = rt.storage_usage_summary({"uploads": str(tmp_path / "nope")})
    assert result["uploads"]["exists"] is False


def test_storage_usage_summary_counts_files(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    (d / "a.csv").write_text("hello")
    (d / "b.csv").write_text("world!")
    result = rt.storage_usage_summary({"uploads": str(d)})
    assert result["uploads"]["file_count"] == 2
    assert result["uploads"]["bytes"] == 11
    assert result["_total_bytes"] == 11


def test_preview_cleanup_finds_old_files(tmp_path):
    d = tmp_path / "temp"
    d.mkdir()
    old_file = d / "old.tmp"
    old_file.write_text("x")
    old_time = time.time() - (10 * 86400)
    os.utime(old_file, (old_time, old_time))

    new_file = d / "new.tmp"
    new_file.write_text("y")

    result = rt.preview_cleanup(str(d), max_age_days=7)
    paths = [c["path"] for c in result]
    assert str(old_file) in paths
    assert str(new_file) not in paths


def test_preview_cleanup_respects_protected_paths(tmp_path):
    d = tmp_path / "temp"
    d.mkdir()
    old_file = d / "old.tmp"
    old_file.write_text("x")
    old_time = time.time() - (10 * 86400)
    os.utime(old_file, (old_time, old_time))

    result = rt.preview_cleanup(str(d), max_age_days=7, protected_paths={str(old_file)})
    assert result == []


def test_delete_items_without_confirm_deletes_nothing(tmp_path):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    result = rt.delete_items([{"path": str(f), "bytes": 1}], confirm=False)
    assert result["deleted"] == []
    assert f.exists()


def test_delete_items_with_confirm_deletes(tmp_path):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    result = rt.delete_items([{"path": str(f), "bytes": 1}], confirm=True)
    assert result["deleted_count"] == 1
    assert not f.exists()


def test_cleanup_audit_entry_shape():
    entry = rt.cleanup_audit_entry("carol", "temp_files_days", [{"bytes": 100}], confirmed=True)
    assert entry["action"] == "retention_cleanup"
    assert entry["detail"]["total_bytes"] == 100
    assert entry["detail"]["confirmed"] is True
