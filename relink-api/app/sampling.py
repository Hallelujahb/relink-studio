"""
Sampling for quality control. With a seed, a random sample is reproducible:
same seed and same rows always give the same sample. Method D is ignored when
scoring rows, since its value is a closeness check and not a match score.
"""
import random


def _scores(row):
    return [m["score"] for k, m in row.get("methods", {}).items() if k != "D" and m and m.get("score") is not None]


def _best_score(row):
    scores = _scores(row)
    return max(scores) if scores else None


def random_sample(rows: list, n: int, seed: int = None) -> list:
    pool = list(rows)
    random.Random(seed).shuffle(pool)
    return pool[:n]


def deterministic_sample(rows: list, n: int) -> list:
    """Every Kth row ordered by sourceId, so the same n on the same data returns the same rows."""
    ordered = sorted(rows, key=lambda r: str(r["sourceId"]))
    if n <= 0 or not ordered:
        return []
    return ordered[::max(1, len(ordered) // n)][:n]


def lowest_confidence_sample(rows: list, n: int) -> list:
    scored = [(r, _best_score(r)) for r in rows if _best_score(r) is not None]
    scored.sort(key=lambda rs: rs[1])
    return [r for r, _ in scored[:n]]


def highest_confidence_sample(rows: list, n: int) -> list:
    scored = [(r, _best_score(r)) for r in rows if _best_score(r) is not None]
    scored.sort(key=lambda rs: rs[1], reverse=True)
    return [r for r, _ in scored[:n]]


def largest_score_gap_sample(rows: list, n: int) -> list:
    """Rows where the top two method scores are furthest apart, so the methods disagree most."""
    gapped = []
    for r in rows:
        s = sorted(_scores(r), reverse=True)
        if len(s) >= 2:
            gapped.append((r, s[0] - s[1]))
    gapped.sort(key=lambda rg: rg[1], reverse=True)
    return [r for r, _ in gapped[:n]]


def by_kind_sample(rows: list, kind: str, n: int = None) -> list:
    """Rows of one kind (collision, unmatched, type_leak, geometry_suspect...), optionally capped."""
    matched = [r for r in rows if r.get("kind") == kind]
    return matched[:n] if n else matched
