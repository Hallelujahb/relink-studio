"""
Method E: wraps the `splink` library (probabilistic record linkage) as an
alternative to Methods A/B. Splink is an optional dependency -- importing
this module raises ImportError with a clear message if it's not installed,
which routes.py catches and surfaces as a real API error rather than a
silent no-op.

Honesty about calibration: Splink's comparison-level m/u probabilities are
only meaningful once trained (via `estimate_u_using_random_sampling` and/or
expectation-maximisation). Training needs a reasonable amount of data to
converge; on small projects it can fail outright (division by too few
pairs) or produce unstable estimates. This module attempts training and
falls back to Splink's untrained default comparison weights when training
fails, but flags the result as `"calibrated": false` in that case so the
caller (and the review UI, eventually) can show that Method E's scores on
this run are a rough heuristic, not a trained probability. Splink is
mainly worth turning on at larger scale, where there's enough data for
training to actually converge.
"""
import pandas as pd

try:
    from splink import Linker, SettingsCreator, DuckDBAPI, block_on
    import splink.comparison_library as cl
    SPLINK_AVAILABLE = True
except ImportError:
    SPLINK_AVAILABLE = False


class SplinkUnavailable(RuntimeError):
    pass


def method_e_splink(source_rows, target_rows, source_id_col, source_match_col,
                     target_id_col, target_match_col, matching_cfg, blocking_floor):
    if not SPLINK_AVAILABLE:
        raise SplinkUnavailable(
            "Method E requires the 'splink' package. Install it with `pip install splink` "
            "and re-run -- it is intentionally not a hard dependency of the base API."
        )
    if not source_rows or not target_rows:
        return {}, {"calibrated": False, "reason": "empty input"}

    from .matching import normalize  # local import avoids a cycle at module load time

    df_l = pd.DataFrame([
        {"unique_id": str(r.get(source_id_col)), "name": normalize(r.get(source_match_col), matching_cfg)}
        for r in source_rows
    ])
    df_r = pd.DataFrame([
        {"unique_id": str(r.get(target_id_col)), "name": normalize(r.get(target_match_col), matching_cfg)}
        for r in target_rows
    ])

    settings = SettingsCreator(
        link_type="link_only",
        probability_two_random_records_match=max(1e-4, min(0.5, 1.0 / max(len(target_rows), 2))),
        comparisons=[cl.JaroWinklerAtThresholds("name", score_threshold_or_thresholds=[0.9, 0.7])],
        blocking_rules_to_generate_predictions=[block_on("substr(name,1,1)")],
        retain_matching_columns=True,
    )

    linker = Linker([df_l, df_r], settings, db_api=DuckDBAPI(), input_table_aliases=["l", "r"])

    calibrated = False
    try:
        linker.training.estimate_u_using_random_sampling(max_pairs=2e5)
        linker.training.estimate_parameters_using_expectation_maximisation(
            block_on("substr(name,1,1)")
        )
        calibrated = True
    except Exception:
        # Small/sparse datasets frequently can't converge. Fall back to
        # Splink's untrained default comparison weights rather than failing
        # the whole method -- still produces a real (if rougher) ranking.
        calibrated = False

    predictions = linker.inference.predict(threshold_match_probability=max(blocking_floor, 0.01))
    df = predictions.as_pandas_dataframe()

    results = {}
    if not df.empty:
        best_per_source = df.sort_values("match_probability", ascending=False).drop_duplicates("unique_id_l")
        target_name_by_id = {str(r.get(target_id_col)): r.get(target_match_col) for r in target_rows}
        for _, row in best_per_source.iterrows():
            results[row["unique_id_l"]] = {
                "name": target_name_by_id.get(row["unique_id_r"]),
                "targetId": row["unique_id_r"],
                "score": round(float(row["match_probability"]), 4),
            }

    return results, {"calibrated": calibrated}
