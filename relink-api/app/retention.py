"""
Retention and cleanup controls (section 15). Everything here is local
disk bookkeeping -- no network calls. Nothing is deleted without an
explicit confirm=True; preview_cleanup() always shows what WOULD be
removed before delete_items() actually removes anything.
"""
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RetentionPolicy:
    raw_uploads_days: int = 90
    temp_files_days: int = 7
    import_previews_days: int = 7
    intermediate_normalized_days: int = 30
    candidate_pairs_days: int = 30
    job_logs_days: int = 60
    audit_records_days: int = 365
    old_exports_days: int = 90
    old_backups_days: int = 180
    source_snapshots_days: int = 180


def _age_days(mtime: float) -> float:
    return (time.time() - mtime) / 86400.0


def storage_usage_summary(directories: dict) -> dict:
    """directories: {"uploads": "/path", "exports": "/path", ...}.
    Returns per-directory size/file counts; skips any path that doesn't
    exist rather than raising."""
    summary = {}
    total_bytes = 0
    for label, path in directories.items():
        if not path or not os.path.isdir(path):
            summary[label] = {"exists": False, "bytes": 0, "file_count": 0}
            continue
        size, count = 0, 0
        for root, _, files in os.walk(path):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    size += os.path.getsize(fp)
                    count += 1
                except OSError:
                    continue
        summary[label] = {"exists": True, "bytes": size, "file_count": count}
        total_bytes += size
    summary["_total_bytes"] = total_bytes
    return summary


def preview_cleanup(directory: str, max_age_days: int, protected_paths: set = None) -> list:
    """Lists files older than max_age_days, excluding anything in
    protected_paths (e.g. files still referenced by an active project).
    Never deletes -- this is read-only."""
    protected_paths = protected_paths or set()
    if not os.path.isdir(directory):
        return []
    candidates = []
    for root, _, files in os.walk(directory):
        for fn in files:
            fp = os.path.join(root, fn)
            if fp in protected_paths:
                continue
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            age = _age_days(mtime)
            if age >= max_age_days:
                candidates.append({"path": fp, "age_days": round(age, 1), "bytes": os.path.getsize(fp)})
    return candidates


def delete_items(items: list, confirm: bool = False) -> dict:
    """items: output of preview_cleanup(). Refuses to delete anything
    unless confirm=True is passed explicitly by the caller (i.e. the user
    has seen the preview and agreed)."""
    if not confirm:
        return {"deleted": [], "skipped": len(items), "reason": "confirm=False; nothing was deleted"}
    deleted, errors = [], []
    for item in items:
        try:
            os.remove(item["path"])
            deleted.append(item["path"])
        except OSError as e:
            errors.append({"path": item["path"], "error": str(e)})
    return {"deleted": deleted, "errors": errors, "deleted_count": len(deleted)}


def cleanup_audit_entry(actor: str, policy_name: str, items: list, confirmed: bool) -> dict:
    """Structured record for the project's audit_log table -- callers
    insert this, this module doesn't touch the DB directly."""
    return {
        "actor": actor,
        "action": "retention_cleanup",
        "detail": {
            "policy": policy_name,
            "confirmed": confirmed,
            "item_count": len(items),
            "total_bytes": sum(i.get("bytes", 0) for i in items),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
