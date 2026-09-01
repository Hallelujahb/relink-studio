# Relink Studio API

A Flask + SQLite backend implementing all 9 sections of the original
gap-list doc, built from scratch against the config/review-row shapes
already used by `relink_studio.jsx`. There was no existing Flask/CLI tool
in this conversation to wrap, so this is a fresh implementation.

## Run it

```bash
pip install -r requirements.txt
python run.py          # http://0.0.0.0:5000
```

Or, installed as a package:

```bash
pip install .
relink-studio-api
```

Or with Docker:

```bash
docker build -t relink-api .
docker run -p 5000:5000 -v $(pwd)/data:/srv/data -v $(pwd)/uploads:/srv/uploads relink-api
```

Env vars: `RELINK_CORS_ORIGINS` (comma-separated, default
`http://localhost:5173`), `RELINK_MAX_UPLOAD_BYTES` (default 50MB),
`RELINK_REQUIRE_AUTH=1` (enforce a bearer token on every route),
`RELINK_UPLOAD_EXPIRY_DAYS` (enable background cleanup of old
unreferenced uploads), `RELINK_FRONTEND_DIST=/path/to/dist` (serve a
built frontend from the same process), `RELINK_NOMINATIM_USER_AGENT`
(set this to real contact info before relying on `/api/geocode`).

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

29 tests: matching-engine unit tests (normalization, methods A/B/D,
threshold/type-leak/collision-guard/tie-downgrade logic in the agreement
combiner), parser unit tests (including a regression test for the xlsx
trailing-empty-rows bug), and API-level integration tests covering the
full upload -> project -> run -> review -> bulk -> undo -> export -> audit
flow, plus auth and the approval hierarchy. All 29 pass as of this
writing -- rerun them yourself, don't take that on faith.

## Section-by-section status

**1. JSON API** -- done. All endpoints below `/api`; see `openapi.yaml`
for the full contract (19 paths).

**2. Persistence** -- done. SQLite (`app/db.py`): `files`, `projects`,
`jobs`, `review_rows`, `decisions` (real undo log, not a single
snapshot), `audit_log`, `users`, `sessions`.

**3. Matching methods:**
- Method A (fuzzy, difflib) and Method D (geometry corroboration,
  haversine centroid distance) -- done and tested.
- Method B -- a lightweight token-overlap linker, labeled honestly as an
  approximation of `recordlinkage`, not a wrapper of the actual package.
- Method E (Splink) -- actually installed and tested against fixtures.
  Falls back to Splink's untrained default weights (flagged
  `calibrated: false`) when EM training can't converge, which is common
  on small datasets -- this was observed for real during testing, not
  hypothesized.
- Method C (linktransformer) -- implementation is based on reading the
  library's actual source (`infer.py`) to confirm the real call signature
  and output columns, not a guess. Could NOT be executed end-to-end in
  this sandbox: installing its full dependency chain (torch, faiss,
  sentence-transformers) exceeded available disk space, and it needs
  Hugging Face Hub network access this sandbox doesn't have. Test it for
  real before trusting its output.
- Nominatim lookup (`POST /api/geocode`) -- rate-limited to 1 req/sec,
  single-row only. Verified reachable from this sandbox, but Nominatim
  itself returned 403 Forbidden on the test call (their bot/abuse
  protection, not a network block) -- set `RELINK_NOMINATIM_USER_AGENT`
  to real contact info, which may resolve it.

**4. Async jobs** -- background thread per `/run` call, `GET
.../jobs/{id}` for polling, `GET .../jobs/{id}/stream` for Server-Sent
Events. Not a real task queue (RQ/Celery) -- fine for one process, not
for horizontal scaling.

**5. Multi-user / auth** -- `app/auth.py`: username/password
(werkzeug-hashed), bearer tokens with expiry, two roles (`reviewer`,
`lead`). Off by default (`RELINK_REQUIRE_AUTH=0`) so the API still works
unauthenticated, matching the original test flow -- verified both paths
still pass. Real per-user audit attribution (not "anonymous" once
logged in). Approval hierarchy: `POST .../review/{id}/submit` (any
authenticated user) then `POST .../review/{id}/approve` (lead role
only), gated by `config.safety.require_approval_hierarchy`.

**6. File-handling hardening** -- extension whitelist before disk write,
empty-file rejection, `MAX_CONTENT_LENGTH` for oversized uploads,
structured JSON error responses for 400/404/405/413/500 (no more raw
Werkzeug HTML pages or print statements), opt-in background expiry sweep
for unreferenced uploads (`RELINK_UPLOAD_EXPIRY_DAYS`), `DELETE
/api/upload/{id}` (blocked with 409 while a project still references it).

**7. Packaging & deployment** -- `pyproject.toml` (installable,
`relink-studio-api` console script), pinned `requirements.txt`, split
`requirements-optional.txt` for the heavy Method C/E deps, `Dockerfile`,
and `RELINK_FRONTEND_DIST` so one process can serve a built frontend
alongside the API (SPA catch-all route included).

**8. Testing** -- done, see above.

**9. Documentation** -- this README, plus `openapi.yaml` (OpenAPI 3.0,
all 19 paths, request/response schemas including `ReviewRow` and
`ProjectConfig`).

## A real bug this pass caught and fixed

Early in this build, making auth's `actor_name()` the *default*
audit-log actor broke the background matching job: it silently
overwrote a successful `"done"` job status with `"error"`, because
`actor_name()` reads Flask's `request` object, which doesn't exist
inside a background thread. Fixed by capturing the triggering user's
name in the request handler (where `request` IS valid) before spawning
the thread, and passing it in explicitly. Caught by re-running the full
curl flow after making the change -- not by inspection -- which is
exactly why that flow is now also a permanent pytest test
(`test_full_review_flow`, `test_auth_register_login_and_audit_attribution`).

## API contract note (carried over from the first pass)

The frontend's `buildConfigObject()` doesn't carry a file identifier --
`POST /api/projects` expects `{ config, source_file_id, target_file_id }`,
with the file IDs coming from `POST /api/upload`. Wiring the frontend to
actually call this API instead of its current local-only flow is
separate work.
