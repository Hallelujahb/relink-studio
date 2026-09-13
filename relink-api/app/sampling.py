"""
Sampling and quality-control workflows (section 9). All sampling is local
and, when a seed is given, fully reproducible -- same seed + same input
rows always yields the same sample.
"""
import random


def _best_score(row):
    scored = [m["score"] for m in row.get("methods", {}).values() if m and m.get("score") is not None]
    return max(scored) if scored else None


def random_sample(rows: list, n: int, seed: int = None) -> list:
    rng = random.Random(seed)
    pool = list(rows)
    rng.shuffle(pool)
    return pool[:n]


def deterministic_sample(rows: list, n: int) -> list:
    """Every-Kth-row sample, ordered by sourceId, so re-running with the
    same n on the same data always returns the same rows."""
    ordered = sorted(rows, key=lambda r: str(r["sourceId"]))
    if n <= 0 or not ordered:
        return []
    step = max(1, len(ordered) // n)
    return ordered[::step][:n]


def lowest_confidence_sample(rows: list, n: int) -> list:
    scored = [(r, _best_score(r)) for r in rows if _best_score(r) is not None]
    scored.sort(key=lambda rs: rs[1])
    return [r for r, _ in scored[:n]]


def highest_confidence_sample(rows: list, n: int) -> list:
    scored = [(r, _best_score(r)) for r in rows if _best_score(r) is not None]
    scored.sort(key=lambda rs: rs[1], reverse=True)
    return [r for r, _ in scored[:n]]


def largest_score_gap_sample(rows: list, n: int) -> list:
    """Rows where the top two method scores disagree the most -- these are
    the cases where methods most strongly disagree with each other."""
    gapped = []
    for r in rows:
        scores = sorted(
            (m["score"] for m in r.get("methods", {}).values() if m and m.get("score") is not None),
            reverse=True,
        )
        if len(scores) >= 2:
            gapped.append((r, scores[0] - scores[1]))
    gapped.sort(key=lambda rg: rg[1], reverse=True)
    return [r for r, _ in gapped[:n]]


def by_kind_sample(rows: list, kind: str, n: int = None) -> list:
    """Covers collision / unmatched / type_leak / geometry_suspect samples
    -- all are just 'rows of this kind', optionally capped."""
    matched = [r for r in rows if r.get("kind") == kind]
    return matched[:n] if n else matched


def missing_identifier_sample(rows: list, n: int = None) -> list:
    matched = [r for r in rows if not r.get("sourceId")]
    return matched[:n] if n else matched
