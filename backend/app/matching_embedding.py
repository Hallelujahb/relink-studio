"""
Method C: semantic matching with a local sentence-embedding model.

Optional dependency (`pip install -r relink-api/requirements-embedding.txt`).
Importing this module never fails; calling method_c_embedding() without the
package raises EmbeddingUnavailable, which routes.py records as a labeled
warning instead of fabricating scores.

Privacy note: the model runs locally, but the *first* load of a model given by
name (default below) downloads its weights from the Hugging Face hub. For a
fully offline setup, point RELINK_EMBEDDING_MODEL at a local model directory
and set RELINK_EMBEDDING_OFFLINE=1.

Unlike Methods A/B this does not use first-3-character blocking (that would
defeat the point of semantic matching); it scores every source row against
every target row in chunks, so cost is O(sources x targets). Prefer A/B for
very large files.
"""
import os

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    EMBEDDING_AVAILABLE = False

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_CHUNK = 512
_model_cache = {}


class EmbeddingUnavailable(RuntimeError):
    pass


def _load_encoder():
    name = os.environ.get("RELINK_EMBEDDING_MODEL", DEFAULT_MODEL)
    if name not in _model_cache:
        kwargs = {"local_files_only": True} if os.environ.get("RELINK_EMBEDDING_OFFLINE") == "1" else {}
        _model_cache[name] = SentenceTransformer(name, **kwargs)
    model = _model_cache[name]
    return lambda texts: model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def method_c_embedding(source_rows, target_rows, source_id_col, source_match_col,
                       target_id_col, target_match_col, matching_cfg, blocking_floor, encoder=None):
    """Returns ({source_id: {name, targetId, score}}, meta). `encoder` (texts ->
    2D array of L2-normalised vectors) is injectable for tests."""
    if encoder is None:
        if not EMBEDDING_AVAILABLE:
            raise EmbeddingUnavailable(
                "Method C requires the 'sentence-transformers' package. Install it with "
                "`pip install -r relink-api/requirements-embedding.txt` and re-run."
            )
        try:
            encoder = _load_encoder()
        except Exception as e:  # e.g. model not cached and offline
            raise EmbeddingUnavailable(f"Could not load the embedding model: {e}")
    if not source_rows or not target_rows:
        return {}, {"model": None, "reason": "empty input"}

    import numpy as np
    from .matching import normalize

    src_text = [normalize(r.get(source_match_col), matching_cfg) for r in source_rows]
    tgt_text = [normalize(r.get(target_match_col), matching_cfg) for r in target_rows]
    tgt_vecs = np.asarray(encoder(tgt_text), dtype="float32")

    results = {}
    for start in range(0, len(source_rows), _CHUNK):
        chunk = source_rows[start:start + _CHUNK]
        src_vecs = np.asarray(encoder(src_text[start:start + _CHUNK]), dtype="float32")
        sims = src_vecs @ tgt_vecs.T
        best_idx = sims.argmax(axis=1)
        for i, row in enumerate(chunk):
            score = float(np.clip(sims[i, best_idx[i]], 0.0, 1.0))
            src_id = row.get(source_id_col)
            if not src_text[start + i] or score < blocking_floor:
                results[src_id] = {"name": None, "targetId": None, "score": None}
                continue
            t = target_rows[int(best_idx[i])]
            results[src_id] = {"name": t.get(target_match_col), "targetId": t.get(target_id_col), "score": round(score, 4)}
    return results, {"model": os.environ.get("RELINK_EMBEDDING_MODEL", DEFAULT_MODEL)}
