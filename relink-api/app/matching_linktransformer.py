"""
Method C: wraps the `linktransformer` library (embedding-based matching via
a pretrained sentence-transformer model) as an alternative to Methods A/B/E.

Not yet run end to end against real data. `linktransformer` downloads a
pretrained model from Hugging Face Hub on first use, and its full
dependency chain (torch, faiss, sentence-transformers) is a multi-gigabyte
optional install (see the setup script's --with-linktransformer flag), so
this has been exercised at the install and API-shape level but not with a
live `lt.merge()` call against real data in every environment.

The call signature and output shape below come from reading
linktransformer's own source (`infer.py`) directly, not from a guess:
`merge(df1, df2, on="name", model=...)` reindexes df2 to its nearest match
for each df1 row via FAISS cosine similarity, then does a positional merge
with `suffixes=("_x","_y")`, so both the join column and any same-named ID
column end up suffixed (`name_x`/`name_y`, `__id_x`/`__id_y` here) and a
`score` column (cosine similarity, roughly 0-1) is appended. If Method C is
enabled, test an actual `lt.merge()` call against real data before trusting
its output -- the shape is documented, the numeric behavior on real data
is not yet confirmed.
"""
import pandas as pd

try:
    import linktransformer as lt
    LINKTRANSFORMER_AVAILABLE = True
except ImportError:
    LINKTRANSFORMER_AVAILABLE = False


class LinkTransformerUnavailable(RuntimeError):
    pass


_DEFAULT_MODEL = "all-MiniLM-L6-v2"  # confirmed against linktransformer's own load_model() default


def method_c_linktransformer(source_rows, target_rows, source_id_col, source_match_col,
                              target_id_col, target_match_col, matching_cfg, blocking_floor,
                              model_name=_DEFAULT_MODEL):
    if not LINKTRANSFORMER_AVAILABLE:
        raise LinkTransformerUnavailable(
            "Method C requires the 'linktransformer' package (and Hugging Face Hub network "
            "access to fetch its model). Install it with `pip install linktransformer` and "
            "re-run -- it is intentionally not a hard dependency of the base API."
        )
    if not source_rows or not target_rows:
        return {}

    from .matching import normalize

    df_l = pd.DataFrame([
        {"__id": str(r.get(source_id_col)), "name": normalize(r.get(source_match_col), matching_cfg)}
        for r in source_rows
    ])
    df_r = pd.DataFrame([
        {"__id": str(r.get(target_id_col)), "name": normalize(r.get(target_match_col), matching_cfg)}
        for r in target_rows
    ])

    merged = lt.merge(
        df_l, df_r,
        merge_type="1:1",
        on="name",
        model=model_name,
    )

    results = {}
    for _, row in merged.iterrows():
        score = row.get("score")
        if score is None:
            continue
        score = max(0.0, min(1.0, float(score)))  # cosine similarity can dip slightly negative; clamp to [0,1]
        if score < blocking_floor:
            continue
        results[row["__id_x"]] = {
            "name": row.get("name_y"),
            "targetId": row["__id_y"],
            "score": round(score, 4),
        }
    return results
