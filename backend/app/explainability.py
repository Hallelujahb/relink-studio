"""
Explains one review row: which method scored what, against which normalized
strings, and which thresholds it did or did not clear. It reuses normalize()
and _blocking_key() from app.matching, so it cannot drift from the matcher.
"""
from .matching import _blocking_key, normalize


def explain_review_row(row, source_value, target_values_by_id, matching_cfg, thresholds, safety):
    """
    row: {"sourceId", "kind", "approved", "chosenMethod", "collisionPartner",
          "methods": {letter: {"name", "targetId", "score"} or None}}
    source_value: the raw source match-column value for this row.
    target_values_by_id: {targetId: raw target match-column value} for the
    targets that appear in row["methods"].
    """
    auto_approve = thresholds.get("auto_approve", 0.95)
    needs_review = thresholds.get("needs_review", 0.80)

    source_norm = normalize(source_value, matching_cfg)
    source_block_key = _blocking_key(source_norm)

    per_method = {}
    for letter, m in row["methods"].items():
        if not m or m.get("score") is None:
            per_method[letter] = {"ran": False, "reason": "no candidate above the blocking floor for this method"}
            continue
        target_val = target_values_by_id.get(m["targetId"])
        target_norm = normalize(target_val, matching_cfg) if target_val is not None else None
        per_method[letter] = {
            "ran": True,
            "score": m["score"],
            "target_id": m["targetId"],
            "target_name": m["name"],
            "source_normalized": source_norm,
            "target_normalized": target_norm,
            "exact_normalized_match": target_norm is not None and source_norm == target_norm,
            "blocking_key": source_block_key,
            "cleared_auto_approve_threshold": m["score"] >= auto_approve,
            "cleared_needs_review_floor": m["score"] >= needs_review,
        }

    reasons = []
    if row["kind"] == "unmatched":
        reasons.append("No enabled method returned a candidate above the configured blocking floor.")
    if row["kind"] == "collision":
        reasons.append(
            f"The best-scoring target is also claimed by source row {row.get('collisionPartner')}; "
            "the collision guard blocked auto-approval until this is resolved."
        )
    if row["kind"] == "type_leak":
        reasons.append("The best-scoring candidate's type did not match the target type this project expects.")
    if row["kind"] == "low_confidence":
        reasons.append(f"The best score was below the needs-review floor ({needs_review}).")
    if row["approved"] and row["kind"] == "consensus":
        best = per_method.get(row.get("chosenMethod"))
        if best and best.get("ran"):
            reasons.append(f"Method {row['chosenMethod']} scored {best['score']}, at or above the auto-approve threshold ({auto_approve}).")
    if not reasons:
        reasons.append("This row was not auto-approved and has no specific automatic reason. It is a plain review case.")

    return {
        "sourceId": row["sourceId"],
        "kind": row["kind"],
        "approved": row["approved"],
        "chosenMethod": row.get("chosenMethod"),
        "thresholds": {"auto_approve": auto_approve, "needs_review": needs_review},
        "blocking": {"source_blocking_key": source_block_key},
        "methods": per_method,
        "reasons": reasons,
    }
