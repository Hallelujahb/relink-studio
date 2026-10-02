"""
Matching engine for Relink Studio.

  Method A  fuzzy character matching (difflib, standard library)
  Method B  blocking plus token overlap, an approximation of the
            `recordlinkage` package and not a wrapper around it
  Method D  geometry corroboration, a sanity check on a proposed match
  combine_methods()  merges the methods into one review row per source record

Method C (embeddings) lives in matching_embedding.py and Method E (splink) in
matching_splink.py. Both are optional installs.

Scores from different methods are on different scales (a difflib ratio, a
Jaccard overlap, a cosine similarity, a probability), so the best score across
methods is a rough signal, not a calibrated one. That is why disagreement
between methods sends a row to review instead of letting the highest number win.
"""
import math
import re
import unicodedata
from difflib import SequenceMatcher

_PAREN_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[^\w\s]|_")
_DIGIT_RE = re.compile(r"\d+")
_DEFAULT_SUFFIX_WORDS = (
    "inc", "incorporated", "llc", "ltd", "limited", "co", "company",
    "corp", "corporation", "county", "township", "borough", "city",
    "town", "village",
)


def _fold_latin(s):
    """Drops accents from Latin letters (cafe for café) and leaves every other
    script alone, so Amharic, Arabic or Chinese text is not destroyed."""
    out = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        if ord(decomposed[0]) < 128:
            out.append("".join(c for c in decomposed if ord(c) < 128))
        else:
            out.append(ch)
    return "".join(out)


def normalize(value, matching_cfg):
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))

    if matching_cfg.get("strip_parentheticals", True):
        s = _PAREN_RE.sub(" ", s)

    s = _fold_latin(s)
    if not matching_cfg.get("case_sensitive", False):
        s = s.casefold()
    if not matching_cfg.get("keep_digits", True):
        s = _DIGIT_RE.sub(" ", s)

    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if matching_cfg.get("strip_suffix_words", True):
        suffixes = {w.casefold() for w in (matching_cfg.get("suffix_words") or _DEFAULT_SUFFIX_WORDS)}
        words = s.split(" ")
        # Only trailing words, and never the last remaining one, so "City Hall"
        # and a bare "County" keep their meaning.
        while len(words) > 1 and words[-1].casefold() in suffixes:
            words.pop()
        s = " ".join(words)

    return s


def _blocking_key(norm_value):
    """First 3 characters of the normalized value. Only candidates sharing a key are compared."""
    return norm_value[:3] if norm_value else ""


def _build_blocks(rows, id_col, match_col, matching_cfg):
    blocks = {}
    for row in rows:
        norm = normalize(row.get(match_col), matching_cfg)
        if not norm:
            continue  # a name that normalizes to nothing must never match anything
        blocks.setdefault(_blocking_key(norm), []).append(
            {"id": row.get(id_col), "name": row.get(match_col), "norm": norm, "raw": row}
        )
    return blocks


def _no_match():
    return {"name": None, "targetId": None, "score": None}


def method_a_fuzzy(source_rows, target_rows, source_id_col, source_match_col,
                   target_id_col, target_match_col, matching_cfg, blocking_floor):
    target_blocks = _build_blocks(target_rows, target_id_col, target_match_col, matching_cfg)
    results = {}
    for row in source_rows:
        src_id = row.get(source_id_col)
        src_norm = normalize(row.get(source_match_col), matching_cfg)
        best = None
        if src_norm:
            for cand in target_blocks.get(_blocking_key(src_norm), []):
                score = SequenceMatcher(None, src_norm, cand["norm"]).ratio()
                if score < blocking_floor:
                    continue
                if best is None or score > best["score"]:
                    best = {"name": cand["raw"].get(target_match_col), "targetId": cand["id"], "score": round(score, 4)}
        results[src_id] = best or _no_match()
    return results


def _tokens(norm_value):
    return {t for t in norm_value.split(" ") if t}


def method_b_token_overlap(source_rows, target_rows, source_id_col, source_match_col,
                           target_id_col, target_match_col, matching_cfg, blocking_floor):
    target_blocks = _build_blocks(target_rows, target_id_col, target_match_col, matching_cfg)
    results = {}
    for row in source_rows:
        src_id = row.get(source_id_col)
        src_norm = normalize(row.get(source_match_col), matching_cfg)
        src_tokens = _tokens(src_norm)
        best = None
        if src_norm:
            for cand in target_blocks.get(_blocking_key(src_norm), []):
                cand_tokens = _tokens(cand["norm"])
                union = src_tokens | cand_tokens
                score = len(src_tokens & cand_tokens) / len(union) if union else 0.0
                if score < blocking_floor:
                    continue
                if best is None or score > best["score"]:
                    best = {"name": cand["raw"].get(target_match_col), "targetId": cand["id"], "score": round(score, 4)}
        results[src_id] = best or _no_match()
    return results


# Method D ---------------------------------------------------------------- #

def _ring_points(ring):
    pts = list(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _centroid(geometry):
    """Mean of the outline vertices, a rough centre good enough for a distance
    check. Returns None for anything it cannot read or that is not lon/lat."""
    if not geometry:
        return None
    gtype, coords = geometry.get("type"), geometry.get("coordinates")
    try:
        if gtype == "Point":
            pts = [coords]
        elif gtype in ("MultiPoint", "LineString"):
            pts = list(coords)
        elif gtype == "MultiLineString":
            pts = [p for line in coords for p in line]
        elif gtype == "Polygon":
            pts = _ring_points(coords[0])
        elif gtype == "MultiPolygon":
            pts = [p for poly in coords for p in _ring_points(poly[0])]
        else:
            return None
        if not pts:
            return None
        lons = [float(p[0]) for p in pts]
        lats = [float(p[1]) for p in pts]
    except (TypeError, ValueError, IndexError):
        return None
    if any(abs(x) > 180 for x in lons) or any(abs(y) > 90 for y in lats):
        return None  # projected coordinates, not WGS84
    return sum(lons) / len(lons), sum(lats) / len(lats)


def _haversine_km(p1, p2):
    lon1, lat1, lon2, lat2 = map(math.radians, [p1[0], p1[1], p2[0], p2[1]])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def method_d_geometry_corroboration(source_geometry_by_index, target_geometry_by_index,
                                    candidate_target_index_by_source_index, max_km=25):
    """Not a name scorer. Flags a source row as suspect when the proposed
    target sits farther than max_km away.
    Returns {source_index: {"suspect": bool, "distance_km": float | None}}."""
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


# Agreement --------------------------------------------------------------- #

def _type_key(value):
    return str(value).strip().casefold()


def combine_methods(source_rows, source_id_col, source_name_col, method_results,
                    target_rows, target_id_col, target_type_col, target_type_expected, config):
    """
    method_results: {"A": {src_id: {name, targetId, score}}, "B": {...}, ...}
    Returns review rows: {sourceId, sourceName, kind, approved, chosenMethod,
    collisionPartner, methods: {A..E}}.

    A row is auto-approved only when the best score clears the threshold and no
    other method with a real score points somewhere else.
    """
    thresholds = config.get("thresholds", {})
    safety = config.get("safety", {})
    auto_approve = thresholds.get("auto_approve", 0.95)
    needs_review = thresholds.get("needs_review", 0.80)
    allow_many_to_one = bool(config.get("target", {}).get("allow_many_to_one"))

    target_type_by_id = {}
    if target_type_col:
        for t in target_rows:
            target_type_by_id[t.get(target_id_col)] = t.get(target_type_col)

    claims = {}
    rows = []

    for src in source_rows:
        src_id = src.get(source_id_col)
        methods = {}
        for letter in ("A", "B", "C", "D", "E"):
            m = method_results.get(letter, {}).get(src_id)
            methods[letter] = m if m else _no_match()

        scored = {k: m for k, m in methods.items() if m["score"] is not None and k != "D"}
        best = max(scored.values(), key=lambda m: m["score"]) if scored else None

        kind, approved, chosen_method = "consensus", False, None

        if best is None:
            kind = "unmatched"
        else:
            chosen_method = next(k for k, m in scored.items() if m is best)
            if best["score"] >= auto_approve:
                approved = True
            elif best["score"] < needs_review:
                kind = "low_confidence"

            strong_targets = {m["targetId"] for m in scored.values()
                              if m["score"] >= needs_review and m["targetId"] is not None}
            if len(strong_targets) > 1:
                approved = False  # methods point at different targets, a person has to choose

            if target_type_col and target_type_expected:
                actual = target_type_by_id.get(best["targetId"])
                if actual is None or _type_key(actual) != _type_key(target_type_expected):
                    kind = "type_leak"
                    approved = False

            if best["targetId"] is not None and kind != "type_leak" and best["score"] >= needs_review:
                claims.setdefault(best["targetId"], []).append(src_id)

        rows.append({
            "sourceId": src_id,
            "sourceName": src.get(source_name_col),
            "kind": kind,
            "approved": approved,
            "chosenMethod": chosen_method,
            "collisionPartner": None,
            "methods": methods,
        })

    if safety.get("collision_guard") and not allow_many_to_one:
        by_id = {r["sourceId"]: r for r in rows}
        for claimants in claims.values():
            if len(claimants) > 1:
                for i, sid in enumerate(claimants):
                    r = by_id[sid]
                    r["kind"] = "collision"
                    r["approved"] = False
                    r["collisionPartner"] = claimants[(i + 1) % len(claimants)]

    if safety.get("auto_downgrade_ties"):
        for r in rows:
            # Same score, different targets: nothing says which one is right.
            # Two methods agreeing on one target is consensus and stays approved.
            s = sorted(((m["score"], m["targetId"]) for k, m in r["methods"].items()
                        if m["score"] is not None and k != "D"), reverse=True)
            if len(s) >= 2 and abs(s[0][0] - s[1][0]) < 1e-6 and s[0][1] != s[1][1]:
                r["approved"] = False

    return rows
