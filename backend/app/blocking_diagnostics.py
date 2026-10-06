"""
Candidate-generation and blocking diagnostics.

Reuses the existing blocking logic in app.matching (normalize,
_build_blocks) rather than reimplementing it, so diagnostics always
reflect exactly what the real matcher does.
"""
from .matching import normalize, _build_blocks, _blocking_keys, _candidates

_HUGE_BLOCK_THRESHOLD = 200          # candidate pairs from one block gets expensive
_LOW_COVERAGE_PCT_THRESHOLD = 50.0    # % of source rows landing in a block with >=1 target


def analyze_blocking(source_rows, target_rows, source_id_col, source_match_col,
                      target_id_col, target_match_col, matching_cfg):
    target_blocks = _build_blocks(target_rows, target_id_col, target_match_col, matching_cfg)

    source_norms = [normalize(row.get(source_match_col), matching_cfg) for row in source_rows]
    source_key_sets = [_blocking_keys(n) for n in source_norms]

    block_sizes = {k: len(v) for k, v in target_blocks.items()}
    empty_key_sources = sum(1 for ks in source_key_sets if not ks)
    excluded_sources = sum(1 for ks in source_key_sets if not (ks & target_blocks.keys()))

    # Distinct (source, target) pairs, since a record can share several keys.
    candidate_pairs = sum(len(_candidates(n, target_blocks)) for n in source_norms)
    largest_blocks = []
    for key, size in block_sizes.items():
        matches_from_key = sum(1 for ks in source_key_sets if key in ks)
        largest_blocks.append({"key": key, "target_count": size, "source_count": matches_from_key})
    largest_blocks.sort(key=lambda b: b["target_count"] * max(b["source_count"], 1), reverse=True)

    warnings = []
    huge = [b for b in largest_blocks if b["target_count"] * max(b["source_count"], 1) > _HUGE_BLOCK_THRESHOLD]
    if huge:
        warnings.append(f"{len(huge)} blocking key(s) produce more than {_HUGE_BLOCK_THRESHOLD} candidate pairs; matching may be slow")

    if source_rows:
        excluded_pct = 100.0 * excluded_sources / len(source_rows)
        if excluded_pct > (100 - _LOW_COVERAGE_PCT_THRESHOLD):
            warnings.append(
                f"{excluded_sources} of {len(source_rows)} source rows ({excluded_pct:.1f}%) share no "
                "blocking key with any target row and will never be compared -- recall may be low"
            )
    if empty_key_sources:
        warnings.append(f"{empty_key_sources} source row(s) normalized to an empty string and cannot be blocked at all")

    empty_target_blocks_used_by_none = sum(1 for k in block_sizes if not any(k in ks for ks in source_key_sets))

    return {
        "total_target_blocks": len(target_blocks),
        "total_candidate_pairs": candidate_pairs,
        "raw_pair_count_without_blocking": len(source_rows) * len(target_rows),
        "reduction_factor": (
            round(1 - candidate_pairs / (len(source_rows) * len(target_rows)), 4)
            if source_rows and target_rows else None
        ),
        "largest_blocks": largest_blocks[:10],
        "empty_blocks_unused_by_source": empty_target_blocks_used_by_none,
        "source_rows_excluded_by_blocking": excluded_sources,
        "source_rows_with_empty_blocking_key": empty_key_sources,
        "warnings": warnings,
    }
