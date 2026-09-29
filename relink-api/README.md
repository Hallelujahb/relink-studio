# Relink Studio API

Flask and SQLite backend for Relink Studio. The full contract is in `openapi.yaml`. Most people should start everything with `./relink.sh` from the repo root, see the main README.

## Running it directly

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python run.py        # http://127.0.0.1:5000
```

Or install it as a package with `pip install .` and run `relink-studio-api`. Environment variables are listed in the root README and resolved in `app/config.py`. `GET /health` is always unauthenticated. The server is waitress unless `RELINK_DEBUG=1` in local mode, which uses Flask's dev server.

## What is in it

- **Storage.** SQLite in `RELINK_DATA_DIR`: files, projects, jobs, review rows, an undo log, audit log, users, sessions, schema snapshots, exceptions and a structured decision log. Older databases pick up new columns on startup. Jobs that were running when the process died are marked as errors.
- **Uploads.** csv, xlsx, xls, geojson and json. Empty and oversized files are rejected, trailing blank Excel rows are dropped, duplicate headers are refused, and non-UTF-8 CSVs fall back to cp1252. `DELETE /api/upload/<id>` returns 409 while a project still uses the file.
- **Projects.** Creating one validates that the columns named in the config exist. `DELETE /api/projects/<id>` removes the project and everything stored for it.
- **Matching.** Methods A, B and D are always available. C and E are optional installs and report clearly when missing. A run happens in a background thread and is polled at `.../jobs/<id>`. Only one run per project at a time, and results are replaced in a single transaction. This is fine for one process, not for horizontal scaling.
- **Review.** Paginated, filterable queue. Approve and reject are separate stored states (`decision`). Approving needs a candidate target. Bulk approve and reject return an undo token, and undo skips rows changed since.
- **Auth.** Username and password, bearer tokens (stored only as SHA-256 hashes) that expire after 12 hours, roles `reviewer` and `lead`, login throttling, logout. With auth required, every `/api` route needs a token except login and register. The first account can be created only from the local machine, after that only a lead can create accounts. With `safety.require_approval_hierarchy`, reviewers call `.../submit`, a different lead calls `.../approve`, and the direct approve routes are refused.
- **Audit.** Entries record the logged in user. Where a request body names a reviewer, creator or actor, that name is only used when nobody is logged in.
- **Geocoding.** `POST /api/geocode` is single row and rate limited to one request per second. Set `RELINK_NOMINATIM_USER_AGENT` to real contact information, because Nominatim rejects generic user agents with 403.
- **Database import.** `/api/db/test-connection`, `/api/db/schema`, `/api/db/ingest` for PostgreSQL, ClickHouse and MySQL. Lead only when logins are on.
- **Diagnostics and QC.** Profiling, schema drift, blocking diagnostics, run metrics, precision and recall (only with ground truth supplied), sampling strategies, row explanations. Method D is left out of score statistics.
- **Exceptions.** Applied on the next run for four scopes, recorded only for the rest.
- **Housekeeping.** `/api/admin/storage`, `/api/admin/cleanup/preview`, `/api/admin/cleanup`. Lead only when logins are on. Cleanup only deletes files inside the upload directory that no project references, needs `confirm=true`, and removes the matching file records.

## Known gaps

The config accepts hierarchy chains, exclude patterns, chained and hub shapes and sort order, but matching does not act on them. Custom methods from the UI are never executed. Every user sees every project.

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest tests -v
```

They cover normalization, methods A, B, D and the agreement logic, parsers, config and LAN mode, auth, the review, undo, export and audit flow, and regression tests for the problems fixed in the hardening pass (`test_hardening.py`, `test_hardening_extra.py`).
