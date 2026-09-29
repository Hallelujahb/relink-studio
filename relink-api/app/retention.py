"""
Storage bookkeeping for the upload and data directories. Everything here is
local disk work. Nothing is deleted without confirm=True, and
preview_cleanup() shows what would go before delete_items() removes anything.
"""
import os
import time
from datetime import datetime, timezone


def _age_days(mtime: float) -> float:
    return (time.time() - mtime) / 86400.0


def storage_usage_summary(directories: dict) -> dict:
    """directories: {"uploads": "/path", ...}. Paths that do not exist are reported, not raised."""
    summary, total_bytes = {}, 0
    for label, path in directories.items():
        if not path or not os.path.isdir(path):
            summary[label] = {"exists": False, "bytes": 0, "file_count": 0}
            continue
        size = count = 0
        for root, _, files in os.walk(path):
            for fn in files:
                try:
                    size += os.path.getsize(os.path.join(root, fn))
                    count += 1
                except OSError:
                    continue
        summary[label] = {"exists": True, "bytes": size, "file_count": count}
        total_bytes += size
    summary["_total_bytes"] = total_bytes
    return summary


def preview_cleanup(directory: str, max_age_days: int, protected_paths: set = None) -> list:
    """Files older than max_age_days that are not protected. Read only."""
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
                age = _age_days(os.path.getmtime(fp))
                if age >= max_age_days:
                    candidates.append({"path": fp, "age_days": round(age, 1), "bytes": os.path.getsize(fp)})
            except OSError:
                continue
    return candidates


def delete_items(items: list, confirm: bool = False) -> dict:
    """Deletes the given preview items, and only when confirm=True."""
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
    """A record for the audit_log table. The caller inserts it."""
    return {
        "actor": actor,
        "action": "retention_cleanup",
        "detail": {"policy": policy_name, "confirmed": confirmed, "item_count": len(items),
                   "total_bytes": sum(i.get("bytes", 0) for i in items)},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
