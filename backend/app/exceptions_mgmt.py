"""
Exception and policy management. An exception is an explicit, audited record
that one safety rule should not apply to one scope. It never switches a rule
off globally.

The run applies these scopes: excluded_source, ignored_record,
allowed_type_mismatch and approved_many_to_one. The rest are recorded for the
audit trail only.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

EXCEPTION_SCOPES = (
    "known_duplicate_entities", "approved_many_to_one", "allowed_type_mismatch",
    "accepted_geometry_difference", "source_normalization_exception",
    "ignored_record", "excluded_source", "trusted_identifier", "business_rule",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Exception_:  # trailing underscore: "Exception" is a builtin
    scope: str
    reason: str
    creator: str
    affected_source_id: Optional[str] = None
    affected_target_id: Optional[str] = None
    affected_fields: list = field(default_factory=list)
    active: bool = True
    created_at: str = field(default_factory=_now)
    review_date: Optional[str] = None
    expires_at: Optional[str] = None
    history: list = field(default_factory=list)

    def validate(self):
        errors = []
        if self.scope not in EXCEPTION_SCOPES:
            errors.append(f"scope must be one of {EXCEPTION_SCOPES}")
        if not self.reason:
            errors.append("reason is required")
        if not self.creator:
            errors.append("creator is required")
        return errors

    def is_expired(self, as_of: Optional[str] = None) -> bool:
        if not self.expires_at:
            return False
        limit = datetime.fromisoformat(self.expires_at)
        now = datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        if limit.tzinfo is None:
            limit = limit.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return limit < now

    def is_effective(self, as_of: Optional[str] = None) -> bool:
        return self.active and not self.is_expired(as_of)

    def deactivate(self, actor: str, reason: str = ""):
        self.active = False
        self.history.append({"action": "deactivated", "actor": actor, "reason": reason, "at": _now()})


class ExceptionRegistry:
    def __init__(self):
        self._exceptions: list[Exception_] = []

    def add(self, exc: Exception_) -> Exception_:
        errors = exc.validate()
        if errors:
            raise ValueError("Invalid exception: " + "; ".join(errors))
        self._exceptions.append(exc)
        return exc

    def active_for_source(self, source_id: str) -> list:
        return [e for e in self._exceptions if e.affected_source_id == source_id and e.is_effective()]

    def active_for_scope(self, scope: str) -> list:
        return [e for e in self._exceptions if e.scope == scope and e.is_effective()]

    def all(self) -> list:
        return list(self._exceptions)

    def applies_to(self, source_id: str, scope: str) -> Optional[Exception_]:
        """The first active, unexpired exception for this row and this rule, or None."""
        for e in self._exceptions:
            if e.affected_source_id == source_id and e.scope == scope and e.is_effective():
                return e
        return None
