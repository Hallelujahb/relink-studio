"""
Matching engine for Relink Studio.

Implements:
  - Method A: fuzzy string matching (difflib, stdlib -- mirrors the
    "difflib" engine label the frontend already shows for Method A)
  - Method B: a lightweight blocking + token-overlap linker, labeled
    honestly as an approximation of the `recordlinkage` package rather
    than a wrapper around it (that's a heavier dependency; swapping in
    the real library later is a drop-in replacement for `method_b()`)
  - Method D: geometry corroboration (centroid-distance scoring),
    previously just a frontend toggle with no backend
  - agreement(): combines whichever methods ran into one review row,
    applying thresholds and safety rules from the project config

Method E (splink) is implemented as an optional dependency in
`matching_splink.py` -- installed and checked against the same test
fixtures used elsewhere in this project (see README).

Method C (linktransformer) is implemented as an optional dependency in
`matching_linktransformer.py`, but has not been run end to end: it needs
to download a pretrained sentence-transformer model from Hugging Face Hub
on first use, so the actual download/inference path has not been
exercised in every environment. Enabling either method in a project
config that doesn't have the corresponding package installed raises a
real, labeled API error rather than returning a fabricated score.
"""
import math
import re
from difflib import SequenceMatcher


NOT_IMPLEMENTED_METHODS = set()  # kept for backwards import compatibility; both C and E now have real (optional) implementations


# --------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------- #

_PAREN_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_DIGIT_RE = re.compile(r"\d+")
_DEFAULT_SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "company",
    "corp", "corporation", "county", "township", "borough", "city",
    "town", "village",
}


def normalize(value, matching_cfg):
    if value is None:
        return ""
    s = str(value)

    if matching_cfg.get("strip_parentheticals", True):
        s = _PAREN_RE.sub(" ", s)

    if not matching_cfg.get("case_sensitive", False):
        s = s.lower()

    if not matching_cfg.get("keep_digits", True):
        s = _DIGIT_RE.sub(" ", s)

    s = _NON_ALNUM_RE.sub(" ", s.lower()) if not matching_cfg.get("case_sensitive", False) else s
    s = re.sub(r"\s+", " ", s).strip()

    if matching_cfg.get("strip_suffix_words", True):
        words = [w for w in s.split(" ") if w not in _DEFAULT_SUFFIX_WORDS]
        s = " ".join(words)

    return s


def _blocking_key(norm_value):
    """First 3 chars of the normalized value. Keeps method A/B from doing
    a full O(n*m) comparison on large files; only candidates sharing a
    blocking key are scored against each other."""
    return norm_value[:3] if norm_value else ""


def _build_blocks(rows, id_col, match_col, matching_cfg):
    blocks = {}
    for row in rows:
        norm = normalize(row.get(match_col), matching_cfg)
        key = _blocking_key(norm)
        blocks.setdefault(key, []).append({"id": row.get(id_col), "name": row.get(match_col), "norm": norm, "raw": row})
    return blocks


# --------------------------------------------------------------------- #
# Method A: fuzzy (difflib)
# --------------------------------------------------------------------- #

def method_a_fuzzy(source_rows, target_rows, source_id_col, source_match_col,
                    target_id_col, target_match_col, matching_cfg, blocking_floor):
    target_blocks = _build_blocks(target_rows, target_id_col, target_match_col, matching_cfg)
    results = {}

    for row in source_rows:
        src_id = row.get(source_id_col)
        src_norm = normalize(row.get(source_match_col), matching_cfg)
        key = _blocking_key(src_norm)
        candidates = target_blocks.get(key, [])

        best = None
        for cand in candidates:
            score = SequenceMatcher(None, src_norm, cand["norm"]).ratio()
            if score < blocking_floor:
                continue
            if best is None or score > best["score"]:
                best = {"name": cand["raw"].get(target_match_col), "targetId": cand["id"], "score": round(score, 4)}

        results[src_id] = best or {"name": None, "targetId": None, "score": None}

    return results


# --------------------------------------------------------------------- #
# Method B: blocking + token-overlap (recordlinkage-style approximation)
# --------------------------------------------------------------------- #

def _tokens(norm_value):
    return set(t for t in norm_value.split(" ") if t)


def method_b_token_overlap(source_rows, target_rows, source_id_col, source_match_col,
                            target_id_col, target_match_col, matching_cfg, blocking_floor):
    target_blocks = _build_blocks(target_rows, target_id_col, target_match_col, matching_cfg)
    results = {}

    for row in source_rows:
        src_id = row.get(source_id_col)
        src_norm = normalize(row.get(source_match_col), matching_cfg)
        src_tokens = _tokens(src_norm)
        key = _blocking_key(src_norm)
        candidates = target_blocks.get(key, [])

        best = None
        for cand in candidates:
            cand_tokens = _tokens(cand["norm"])
            union = src_tokens | cand_tokens
            score = (len(src_tokens & cand_tokens) / len(union)) if union else 0.0
            if score < blocking_floor:
                continue
            if best is None or score > best["score"]:
                best = {"name": cand["raw"].get(target_match_col), "targetId": cand["id"], "score": round(score, 4)}

        results[src_id] = best or {"name": None, "targetId": None, "score": None}

    return results


# --------------------------------------------------------------------- #
# Method D: geometry corroboration
# --------------------------------------------------------------------- #

def _ring_centroid(ring):
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return sum(lons) / len(lons), sum(lats) / len(lats)


def _centroid(geometry):
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        return coords[0], coords[1]
    if gtype == "Polygon" and coords:
        return _ring_centroid(coords[0])
    if gtype == "MultiPolygon" and coords:
        return _ring_centroid(coords[0][0])
    return None


def _haversine_km(p1, p2):
    lon1, lat1, lon2, lat2 = map(math.radians, [p1[0], p1[1], p2[0], p2[1]])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def method_d_geometry_corroboration(source_geometry_by_index, target_geometry_by_index,
                                     candidate_target_index_by_source_index, max_km=25):
    """Not a name-scorer. For each source row that Method A/B proposed a
    candidate target for, flag it as geometry-suspect if the candidate's
    centroid sits farther than `max_km` from the source's centroid.
    Returns {source_index: {"suspect": bool, "distance_km": float|None}}.
    """
    flags = {}
    for src_idx, tgt_idx in candidate_target_index_by_source_index.items():
        src_geom = source_geometry_by_index[src_idx] if src_idx < len(source_geometry_by_index) else None
        tgt_geom = target_geometry_by_index[tgt_idx] if tgt_idx is not None and tgt_idx < len(target_geometry_by_index) else None
        src_c, tgt_c = _centroid(src_geom), _centroid(tgt_geom)
        if src_c is None or tgt_c is None:
            flags[src_idx] = {"suspect": False, "distance_km": None}
            continue
        dist = _haversine_km(src_c, tgt_c)
        flags[src_idx] = {"suspect": dist > max_km, "distance_km": round(dist, 2)}
    return flags


# --------------------------------------------------------------------- #
# Agreement: combine method outputs into one review row per source record
# --------------------------------------------------------------------- #

def combine_methods(source_rows, source_id_col, source_name_col, method_results,
                     target_rows, target_id_col, target_type_col, target_type_expected,
                     config):
    """
    method_results: {"A": {src_id: {name,targetId,score}}, "B": {...}, ...}
    Returns a list of review rows shaped like the frontend expects:
      { sourceId, sourceName, kind, approved, chosenMethod, collisionPartner,
        methods: { A: {...}, B: {...}, C: {...} } }
    """
    thresholds = config.get("thresholds", {})
    safety = config.get("safety", {})
    auto_approve = thresholds.get("auto_approve", 0.92)
    needs_review = thresholds.get("needs_review", 0.75)

    target_type_by_id = {}
    if target_type_col:
        for t in target_rows:
            target_type_by_id[t.get(target_id_col)] = t.get(target_type_col)

    claims = {}  # target_id -> [source_id, ...], for collision_guard
    rows = []

    for src in source_rows:
        src_id = src.get(source_id_col)
        src_name = src.get(source_name_col)

        methods = {}
        for letter in ("A", "B", "C", "D", "E"):
            m = method_results.get(letter, {}).get(src_id)
            methods[letter] = m if m else {"name": None, "targetId": None, "score": None}

        scored = [m for m in methods.values() if m["score"] is not None]
        best = max(scored, key=lambda m: m["score"]) if scored else None

        kind = "consensus"
        approved = False

        if best is None:
            kind = "unmatched"
        else:
            if best["score"] >= auto_approve:
                approved = True
            elif best["score"] < needs_review:
                kind = "low_confidence"

            if target_type_col and target_type_expected:
                actual_type = target_type_by_id.get(best["targetId"])
                if actual_type is not None and str(actual_type) != str(target_type_expected):
                    kind = "type_leak"
                    approved = False

            if safety.get("require_hierarchy_match") and src.get("hierarchy") and best.get("targetId"):
                # Left as a hook: hierarchy comparison needs the caller's
                # hierarchy column names, wired in at the route layer.
                pass

            if best["targetId"]:
                claims.setdefault(best["targetId"], []).append(src_id)

        rows.append({
            "sourceId": src_id,
            "sourceName": src_name,
            "kind": kind,
            "approved": approved,
            "chosenMethod": None,
            "collisionPartner": None,
            "methods": methods,
        })

    if safety.get("collision_guard"):
        row_by_id = {r["sourceId"]: r for r in rows}
        for target_id, claimants in claims.items():
            if len(claimants) > 1:
                for i, sid in enumerate(claimants):
                    other = claimants[(i + 1) % len(claimants)]
                    r = row_by_id[sid]
                    r["kind"] = "collision"
                    r["approved"] = False
                    r["collisionPartner"] = other

    if safety.get("auto_downgrade_ties"):
        for r in rows:
            # A tie is only ambiguous if two methods are tied on SCORE but
            # point at DIFFERENT targets -- that's genuinely "can't tell
            # which candidate is right". Two methods tied because they
            # independently agree on the same target is consensus, the
            # best possible outcome, and must never be downgraded.
            scored = [(k, m["score"], m["targetId"]) for k, m in r["methods"].items() if m["score"] is not None]
            if len(scored) >= 2:
                top = sorted(scored, key=lambda kv: kv[1], reverse=True)
                if abs(top[0][1] - top[1][1]) < 1e-6 and top[0][2] != top[1][2]:
                    r["approved"] = False

    return rows
