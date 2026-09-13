"""
Matching-configuration versioning + dry-run comparison between two runs'
review rows (the same shape app.matching.combine_methods() returns:
sourceId, approved, kind, methods{...}).

This module does not execute matching itself -- it stores/compares
configs and diffs two already-produced result sets, so a "dry run" is
just: run matching twice (old config, new config) and pass both result
lists here.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import copy


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConfigVersion:
    version: int
    name: str
    config: dict
    created_at: str = field(default_factory=_now)
    note: Optional[str] = None


class ConfigHistory:
    """Append-only version history for one project's matching config."""

    def __init__(self):
        self._versions: list[ConfigVersion] = []

    def add(self, config: dict, name: str = "", note: Optional[str] = None) -> ConfigVersion:
        version = ConfigVersion(
            version=len(self._versions) + 1,
            name=name or f"v{len(self._versions) + 1}",
            config=copy.deepcopy(config),
            note=note,
        )
        self._versions.append(version)
        return version

    def get(self, version: int) -> Optional[ConfigVersion]:
        return next((v for v in self._versions if v.version == version), None)

    def latest(self) -> Optional[ConfigVersion]:
        return self._versions[-1] if self._versions else None

    def clone(self, version: int, note: Optional[str] = None) -> ConfigVersion:
        src = self.get(version)
        if not src:
            raise ValueError(f"No such config version: {version}")
        return self.add(src.config, name=f"{src.name}-clone", note=note or f"cloned from v{version}")

    def all(self):
        return list(self._versions)

    def restore(self, version: int) -> ConfigVersion:
        """Restores an old version as a *new* top version (never rewrites
        history in place, so past runs stay reproducible against the
        version number they actually used)."""
        src = self.get(version)
        if not src:
            raise ValueError(f"No such config version: {version}")
        return self.add(src.config, name=f"{src.name}-restored", note=f"restored from v{version}")

    def compare_configs(self, version_a: int, version_b: int) -> dict:
        a, b = self.get(version_a), self.get(version_b)
        if not a or not b:
            raise ValueError("Both versions must exist to compare")
        return _diff_dict(a.config, b.config)


def _diff_dict(a: dict, b: dict, prefix: str = "") -> dict:
    changes = []
    keys = set(a.keys()) | set(b.keys())
    for k in sorted(keys):
        path = f"{prefix}.{k}" if prefix else k
        av, bv = a.get(k), b.get(k)
        if isinstance(av, dict) and isinstance(bv, dict):
            changes.extend(_diff_dict(av, bv, path)["changes"])
        elif av != bv:
            changes.append({"field": path, "old": av, "new": bv})
    return {"changes": changes, "has_changes": bool(changes)}


def _best_result(row):
    scored = [m for m in row.get("methods", {}).values() if m and m.get("score") is not None]
    return max(scored, key=lambda m: m["score"]) if scored else None


def dry_run_compare(old_rows: list, new_rows: list) -> dict:
    """Compares two matching runs' review rows (section 6's dry-run diff).
    Rows are matched up by sourceId; a sourceId present in only one run is
    reported, not silently dropped."""
    old_by_id = {r["sourceId"]: r for r in old_rows}
    new_by_id = {r["sourceId"]: r for r in new_rows}
    all_ids = set(old_by_id) | set(new_by_id)

    newly_matched, newly_unmatched = [], []
    score_changes, confidence_changes = [], []
    new_collisions, removed_collisions = [], []
    new_type_warnings, changed_approvals = [], []

    for sid in sorted(all_ids, key=str):
        old_row, new_row = old_by_id.get(sid), new_by_id.get(sid)

        old_approved = bool(old_row["approved"]) if old_row else False
        new_approved = bool(new_row["approved"]) if new_row else False
        if old_approved != new_approved:
            changed_approvals.append({"sourceId": sid, "old": old_approved, "new": new_approved})

        old_kind = old_row["kind"] if old_row else "unmatched"
        new_kind = new_row["kind"] if new_row else "unmatched"

        if old_kind == "unmatched" and new_kind != "unmatched":
            newly_matched.append(sid)
        if old_kind != "unmatched" and new_kind == "unmatched":
            newly_unmatched.append(sid)
        if old_kind != "collision" and new_kind == "collision":
            new_collisions.append(sid)
        if old_kind == "collision" and new_kind != "collision":
            removed_collisions.append(sid)
        if old_kind != "type_leak" and new_kind == "type_leak":
            new_type_warnings.append(sid)

        old_best = _best_result(old_row) if old_row else None
        new_best = _best_result(new_row) if new_row else None
        old_score = old_best["score"] if old_best else None
        new_score = new_best["score"] if new_best else None
        if old_score != new_score and (old_score is not None or new_score is not None):
            score_changes.append({"sourceId": sid, "old_score": old_score, "new_score": new_score})
            if (old_score is None) != (new_score is None):
                confidence_changes.append({"sourceId": sid, "old_score": old_score, "new_score": new_score})

    return {
        "newly_matched": newly_matched,
        "newly_unmatched": newly_unmatched,
        "score_changes": score_changes,
        "confidence_changes": confidence_changes,
        "new_collisions": new_collisions,
        "removed_collisions": removed_collisions,
        "new_type_warnings": new_type_warnings,
        "changed_approvals": changed_approvals,
        "total_compared": len(all_ids),
    }
