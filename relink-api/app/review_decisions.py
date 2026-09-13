"""
Structured review decisions (section 10). Each decision is a full record,
not just a boolean flip, and history is append-only so undo means
"apply the previous recorded state", never "delete the record".
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

REJECTION_REASONS = (
    "wrong_entity", "duplicate_target", "insufficient_evidence",
    "conflicting_identifiers", "invalid_source_data", "invalid_target_data",
    "geometry_mismatch", "type_mismatch", "business_rule_exception",
    "unresolved", "needs_investigation",
)

DECISION_TYPES = ("approve", "reject", "defer")


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReviewDecision:
    decision_type: str
    reviewer: str
    source_id: str
    target_id: Optional[str]
    confidence_at_decision: Optional[float]
    config_version: Optional[int]
    reason: Optional[str] = None
    note: Optional[str] = None
    is_bulk: bool = False
    batch_id: Optional[str] = None
    timestamp: str = field(default_factory=_now)
    previous_decision_id: Optional[str] = None

    def validate(self):
        errors = []
        if self.decision_type not in DECISION_TYPES:
            errors.append(f"decision_type must be one of {DECISION_TYPES}")
        if self.decision_type == "reject" and self.reason and self.reason not in REJECTION_REASONS:
            errors.append(f"reason must be one of {REJECTION_REASONS}")
        if not self.reviewer:
            errors.append("reviewer is required")
        if not self.source_id:
            errors.append("source_id is required")
        return errors


class DecisionLog:
    """Append-only per-project decision history, keyed by source_id, with
    the most recent decision for a row always at the end of its list."""

    def __init__(self):
        self._by_source: dict[str, list[ReviewDecision]] = {}

    def record(self, decision: ReviewDecision) -> ReviewDecision:
        errors = decision.validate()
        if errors:
            raise ValueError("Invalid decision: " + "; ".join(errors))
        history = self._by_source.setdefault(decision.source_id, [])
        if history:
            decision.previous_decision_id = f"{decision.source_id}#{len(history) - 1}"
        history.append(decision)
        return decision

    def history_for(self, source_id: str) -> list:
        return list(self._by_source.get(source_id, []))

    def current(self, source_id: str) -> Optional[ReviewDecision]:
        history = self._by_source.get(source_id)
        return history[-1] if history else None

    def undo(self, source_id: str) -> Optional[ReviewDecision]:
        """Records a new decision that restores the previous state.
        Returns the restored decision, or None if there's nothing to undo
        (a single decision with no prior state)."""
        history = self._by_source.get(source_id, [])
        if len(history) < 2:
            return None
        previous = history[-2]
        restored = ReviewDecision(
            decision_type=previous.decision_type,
            reviewer=previous.reviewer,
            source_id=source_id,
            target_id=previous.target_id,
            confidence_at_decision=previous.confidence_at_decision,
            config_version=previous.config_version,
            reason=previous.reason,
            note=f"(restored via undo) {previous.note or ''}".strip(),
        )
        return self.record(restored)
