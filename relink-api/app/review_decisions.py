"""Allowed values for the structured decision log. The log itself lives in the
review_decision_log table and is written by routes_extra."""

REJECTION_REASONS = (
    "wrong_entity", "duplicate_target", "insufficient_evidence",
    "conflicting_identifiers", "invalid_source_data", "invalid_target_data",
    "geometry_mismatch", "type_mismatch", "business_rule_exception",
    "unresolved", "needs_investigation",
)

DECISION_TYPES = ("approve", "reject", "defer")
