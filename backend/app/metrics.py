"""
Match quality metrics, kept in two tiers:

  run_metrics()         always computable from a run's review rows (counts, rates, scores)
  evaluation_metrics()  precision, recall and F1, which need ground truth you supply.
                        Without it the result says so instead of inventing a number.

Method D is a corroboration check whose "score" is a closeness value, so it is
left out of every score statistic here.
"""
from collections import Counter


def _candidate_methods(row):
    return [m for k, m in row.get("methods", {}).items() if k != "D" and m and m.get("score") is not None]


def run_metrics(rows: list, source_count: int, target_count: int, duration_seconds: float = None) -> dict:
    kinds = Counter(r["kind"] for r in rows)
    approved = sum(1 for r in rows if r["approved"])
    rejected = sum(1 for r in rows if r.get("decision") == "rejected")
    scores = sorted(m["score"] for r in rows for m in _candidate_methods(r))

    def pct(n):
        return round(100.0 * n / len(rows), 2) if rows else 0.0

    metrics = {
        "tier": "observed",
        "total_source_records": source_count,
        "total_target_records": target_count,
        "rows_evaluated": len(rows),
        "approved_count": approved,
        "rejected_count": rejected,
        "unmatched_count": kinds.get("unmatched", 0),
        "collision_count": kinds.get("collision", 0),
        "type_leak_count": kinds.get("type_leak", 0),
        "geometry_suspect_count": kinds.get("geometry_suspect", 0),
        "low_confidence_count": kinds.get("low_confidence", 0),
        "consensus_count": kinds.get("consensus", 0),
        "approval_rate_pct": pct(approved),
        "unmatched_rate_pct": pct(kinds.get("unmatched", 0)),
        "average_score": round(sum(scores) / len(scores), 4) if scores else None,
        "score_min": scores[0] if scores else None,
        "score_max": scores[-1] if scores else None,
        "score_median": scores[len(scores) // 2] if scores else None,
    }
    if duration_seconds is not None:
        metrics["processing_duration_seconds"] = duration_seconds
        metrics["records_per_second"] = round(source_count / duration_seconds, 2) if duration_seconds > 0 else None
    return metrics


def evaluation_metrics(rows: list, ground_truth: dict = None) -> dict:
    """ground_truth: {sourceId: expected_target_id, or None when it should stay unmatched}.

    A wrong approved link counts as a false positive and also as a missed true
    link (false negative). Only approved rows count as predictions."""
    if not ground_truth:
        return {
            "tier": "unavailable",
            "reason": "No ground truth provided; precision/recall/F1 cannot be computed without labeled data.",
        }

    tp = fp = fn = tn = 0
    for r in rows:
        expected = ground_truth.get(r["sourceId"])
        predicted = None
        scored = _candidate_methods(r)
        if scored and r["approved"]:
            predicted = max(scored, key=lambda m: m["score"])["targetId"]

        if expected is None and predicted is None:
            tn += 1
        elif expected is None:
            fp += 1
        elif predicted == expected:
            tp += 1
        elif predicted is None:
            fn += 1
        else:
            fp += 1
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = None
    if precision is not None and recall is not None:
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "tier": "ground_truth",
        "true_positives": tp, "false_positives": fp, "false_negatives": fn, "true_negatives": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1_score": round(f1, 4) if f1 is not None else None,
        "ground_truth_labels_used": len(ground_truth),
    }
