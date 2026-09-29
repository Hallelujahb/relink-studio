# Relink Studio

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

Relink Studio links two lists of records that describe the same real world things when the names do not match exactly. "Riverside Distribution Center 4" in one file and "Riverside DC #4" in the other is the classic case. It started as a way to reconcile a facility list against a partner system, but it knows nothing about facilities. Any two files with a name column will do: stores, companies, people, places.

Everything runs on your machine. A Flask backend does the matching and a React frontend is the interface. There is no cloud service and no third party AI. Your files, matches and review decisions stay where Relink runs unless you export them.

## Quick start

You need Python 3.10 or newer (with `venv`) and Node 20 or newer.

```bash
git clone https://github.com/hallelujahb/relink-studio.git
cd relink-studio
./relink.sh
```

That one script creates the Python environment, installs everything, builds the frontend, starts the server and prints a link. Open `http://127.0.0.1:5000`. Press Ctrl-C to stop. Logs go to `relink.log`.

Running it again is fast. Dependencies and the frontend build are only redone when something changed.

| Command | What it does |
| --- | --- |
| `./relink.sh` | Run on this machine only |
| `./relink.sh --lan` | Share on your private network, login required |
| `./relink.sh --setup` | Install and build, then stop |

If port 5000 is taken, set `RELINK_PORT` first, for example `RELINK_PORT=5050 ./relink.sh`.

## Sharing on your network

`./relink.sh --lan` binds to `0.0.0.0` so a teammate can open the LAN link it prints. Logins are required, and debug mode is always off. The first account has to be created from the machine running Relink, and the script prints the exact `curl` command. After that only a lead can create accounts. Failed logins are throttled and sessions can be ended with `POST /api/auth/logout`.

Binding to `0.0.0.0` exposes Relink to every device on the network you are on, including coffee shop wifi and guest networks. Use it on a network you trust or over a VPN. Never forward the port to the internet. There is no TLS or hardening for public access. If you need that, put a real reverse proxy with its own access control in front.

Without logins (the default local mode), Relink refuses requests whose Host header is not `localhost`, `127.0.0.1` or the configured host, and refuses cross origin writes. That keeps other websites open in your browser from talking to it. If you reach it under another name, add it to `RELINK_ALLOWED_HOSTS`.

`GET /health` returns the running mode and whether auth is required.

## Using it

The interface has six tabs and moves left to right.

### 1. Sources and shape

Load a source file and a target file. CSV, Excel and GeoJSON work. CSV files exported from Excel on Windows (cp1252) are read too. You can also import a table from PostgreSQL, ClickHouse or MySQL (see Database import).

For each file, pick:

- **ID column.** Something unique. Every ID must be present, unique, and free of `/`, or the run stops and says which. IDs are treated as text.
- **Match column.** The text that gets compared. This is the most important setting in the tool. Choose the name column, not a type or category column.
- **Hierarchy chain.** Optional nesting such as region, district, site. See "What is enforced" below.

Creating a project checks that the columns you picked exist in the files.

### 2. Hierarchy and methods

**Thresholds.** Auto-approve (default 0.95) accepts a match with no review. The needs-review floor (default 0.80) is the line below which a match counts as low confidence. If your data has many small spelling differences, try lowering auto-approve to about 0.90.

**Matching settings.**

- **Blocking floor** (default 0.60). The minimum name score for a candidate to be kept at all.
- **Keep digits.** Leave on unless digits never matter. Off makes "Site 1" and "Site 2" identical.
- **Strip parenthetical tags.** Drops things like "(HQ)" or "(Old)".
- **Strip suffix words.** Drops trailing words such as "inc", "county", "township", "city". Only trailing words go, and never the last remaining one, so "City Hall" and a bare "County" stay as they are. The list is built in. A `matching.suffix_words` list in the config replaces it.
- **Case sensitive.** Off by default and usually should stay off.

Names are compared after Unicode normalization. Accents on Latin letters are folded (café becomes cafe) and other scripts, such as Amharic, are kept as they are. A name that ends up empty after cleaning never matches anything.

**Methods.** Each is an independent opinion on the best target for a row.

- **A, difflib.** Character level fuzzy matching from the standard library. Best general default. On by default.
- **B, token overlap.** Blocking plus Jaccard token overlap. An approximation of the `recordlinkage` package, not a wrapper around it. On by default.
- **C, semantic embedding.** Uses a local sentence-transformers model, useful when names differ in wording rather than spelling. Optional: `relink-api/venv/bin/pip install -r relink-api/requirements-embedding.txt` (pulls in PyTorch). It compares every source row with every target row and has no blocking, so prefer A and B on big files. Its cosine scores run high even for unrelated short names, so raise the blocking floor when you use it. A model given by name is downloaded once from the Hugging Face hub. For fully offline use, set `RELINK_EMBEDDING_MODEL` to a local folder and `RELINK_EMBEDDING_OFFLINE=1`. Off by default. If the package is missing the run still finishes, with a warning that Method C did not run.
- **D, geometry corroboration.** Not a name scorer. It flags a candidate as suspicious when its centre is more than 25 km from the source record (`matching.geometry_max_km` changes that). It checks the method that actually won the row. It only runs when both files are GeoJSON with WGS84 coordinates. Lat/lon columns in a CSV do not trigger it.
- **E, splink.** A probabilistic Fellegi-Sunter matcher. Optional: `relink-api/venv/bin/pip install -r relink-api/requirements-optional.txt`. Worth it at tens of thousands of rows or more. On small data it often cannot train and falls back to default weights, and the result is flagged as not calibrated. Off by default.

Scores from different methods are on different scales, so the highest number across methods is a rough signal. That is why a row is only auto-approved when the best score clears the threshold and no other method with a real score points at a different target. Disagreement sends the row to review.

You can also add custom methods in the UI. They are saved in the config file only. The backend never runs user supplied scripts.

### 3. Safety and rules

- **Collision guard.** If two source rows land on the same target, both go to review instead of one silently winning. Only matches at or above the needs-review floor count as claims, so a weak stray candidate cannot knock out a good row.
- **Allow many-to-one.** Turns the collision guard off for targets that legitimately have many sources.
- **Auto-downgrade ties.** Two methods with the same score but different targets send the row to review.
- **Type or level validation.** Point it at a column in the target file and the value you expect. A name match that points at a different type, or at a row with no type at all, becomes a "type leak" and needs review.

### 4. Review queue

Every row that was not auto-approved shows up here with a side by side view of what each enabled method found. You can filter by kind (consensus, low confidence, type leak, collision, geometry suspect, unmatched) or by rejected, search, add an audit note, or check a place name against OpenStreetMap through Nominatim.

Approve and reject are stored by the backend. A rejected row stays rejected after a reload. A row with no candidate target cannot be approved, and neither can a chosen method that found nothing. If a project requires the approval hierarchy, approving through the normal or bulk routes is refused, rows must be submitted and then approved by a lead who is not the person who submitted them.

### 5. Approve and finalize

A summary with row counts, bulk actions and the merge button stays visible while you scroll. Bulk approve and reject return an undo token. Undo only reverts rows that have not been changed since, and reports how many it skipped. Merging and staging decisions happen in the browser, so staged decisions that were not committed are lost on reload. The top bar has a fast mode that runs the pipeline and then bulk approves rows that pass the safe check.

Running a project again replaces all its results. If decisions were already made, the API refuses unless the request says `{"force": true}`. A run either replaces the results completely or leaves the old ones untouched.

### 6. Export and audit

Download the linked result as CSV. It includes which method's answer was used and the score. Names that begin with `=`, `+`, `-` or `@` are prefixed with an apostrophe so a spreadsheet does not run them as formulas. The audit log lists every action: runs, approvals, rejections, undos, exports. Export is only authoritative after merging.

### Config files

Every setting on tabs 1 to 3 can be saved with "Download config" in the top bar and restored with "Upload config". Load your source and target files first, then the config. It only sets columns and toggles and never uploads files or data.

## What is enforced today

The interface saves more settings than the backend acts on. Being straight about it:

**Enforced by the backend:** thresholds, all normalization options, methods A to E, collision guard, allow many-to-one, tie downgrade, disagreement between methods, type or level validation, the blocking floor, the approval hierarchy, and exceptions (see below).

**Saved in the config but not applied to matching:** the hierarchy chain and the hierarchy related rules (require hierarchy match, strict hierarchy match, flag low coverage groups), exclude by name pattern, chained and hub and spoke shapes, and output sort order. Treat these as placeholders until they are wired in.

**Exceptions.** Created through `/api/projects/<id>/exceptions` and applied on the next run. Four scopes change results: `excluded_source` and `ignored_record` drop a row, `allowed_type_mismatch` clears a type leak for a row, and `approved_many_to_one` clears a collision on a target. The other scopes are recorded for the audit trail only.

## Blocking, and why you might get fewer matches than expected

Methods A and B only compare a source row with target rows that share the same first three characters after normalization. A typo in the first three letters, or a nickname that changes the first word, means a real match is silently missed rather than scored low. If you get fewer matches than expected, check this first. `GET /api/projects/<id>/diagnostics/blocking` shows how many source rows never share a key with any target row. Method E blocks on the first character and Method C does not block.

## Database import

Import a table from PostgreSQL, ClickHouse or MySQL as a file. Install drivers with `relink-api/venv/bin/pip install -r relink-api/requirements-optional.txt`, and `requirements-mysql.txt` for MySQL (kept separate because that driver is GPL licensed). Rows are pulled into memory with a default cap of 100,000. Connection details, including the password, are used for that one request and never stored or logged. The caller chooses the host, so with logins on these routes need the lead role. PostgreSQL is the tested path. The ClickHouse and MySQL providers have not been run against a live server, so try them on something disposable first.

## API and command line

The frontend uses the core flow: upload, projects, run, jobs, review, bulk, undo, export, audit, geocode and login. The backend has more endpoints than the UI shows, including data profiling, schema drift, blocking diagnostics, run metrics, review sampling, structured decision history, row explanations, exceptions, project deletion and storage cleanup. They are listed in `relink-api/openapi.yaml` and described in `relink-api/README.md`.

Offline tools that work on local files:

```bash
cd relink-api
venv/bin/python -m app.cli_tools profile data.csv
venv/bin/python -m app.cli_tools validate-config config.json
venv/bin/python -m app.cli_tools schema-diff old.json new.json
venv/bin/python -m app.cli_tools check-capabilities
```

## Configuration

Set these as environment variables before `./relink.sh`. An explicit value always beats the mode default.

| Variable | Default | Notes |
| --- | --- | --- |
| `RELINK_MODE` | `local` | `lan` is set by `--lan` |
| `RELINK_HOST` | `127.0.0.1` (`0.0.0.0` in lan) | |
| `RELINK_PORT` | `5000` | |
| `RELINK_REQUIRE_AUTH` | off (on in lan) | |
| `RELINK_ALLOWED_HOSTS` | empty | Extra hostnames accepted in local mode |
| `RELINK_CORS_ORIGINS` | `http://localhost:5173` | Only matters with the Vite dev server |
| `RELINK_MAX_UPLOAD_BYTES` | 50 MB | |
| `RELINK_DATA_DIR`, `RELINK_UPLOAD_DIR` | `relink-api/data`, `relink-api/uploads` | |
| `RELINK_UPLOAD_EXPIRY_DAYS` | off | Sweeps old unreferenced uploads hourly |
| `RELINK_NOMINATIM_USER_AGENT` | placeholder | Set to your name plus an email or URL before real use |
| `RELINK_EMBEDDING_MODEL`, `RELINK_EMBEDDING_OFFLINE` | see Method C | |

Invalid values stop the server at startup with a clear message instead of guessing.

## Docker

```bash
docker build -t relink-api relink-api
docker run -p 127.0.0.1:5000:5000 -v $(pwd)/data:/srv/data -v $(pwd)/uploads:/srv/uploads relink-api
```

The image serves the API only, as root. To get the UI from the same container, mount a built `relink-studio/dist` and set `RELINK_FRONTEND_DIST` to it. Publish on `127.0.0.1` unless you have set up auth and a proxy.

## Known limits

- One shared workspace. Any logged in user can see and change any project. Roles only separate reviewers from leads.
- The frontend is a single large component. It works, but changes there are easy to get wrong.
- The frontend depends on `xlsx` 0.18.5, the last version published to npm, which has known advisories for crafted files. Only open spreadsheets you trust, or switch to the maintained build from cdn.sheetjs.com.
- A run holds both files in memory, and matching is single process. It is meant for files in the tens of thousands of rows, not millions.
- Source IDs cannot contain `/`, and the API URLs use them as path segments.
- The job stream (`.../jobs/<id>/stream`) cannot authenticate from a browser, the UI polls instead.

## Developing

The UI is one file on purpose: `relink_studio.jsx`. `./relink.sh` copies it to `relink-studio/src/App.jsx`, which is generated and git ignored. For hot reload, run the backend with `./relink.sh` and in another terminal:

```bash
cd relink-studio
npm run dev
```

Then open `http://localhost:5173`. It talks to the backend on port 5000.

Tests:

```bash
cd relink-api
venv/bin/python -m pip install pytest
venv/bin/python -m pytest tests -v
```

## Project layout

```
relink-studio/
  relink.sh              install, build and run
  relink_studio.jsx      the whole frontend
  relink-api/            Flask backend
    app/
      config.py          mode, host, port, auth, CORS resolution
      matching.py        methods A, B, D and the agreement logic
      matching_embedding.py   method C (optional)
      matching_splink.py      method E (optional)
      routes.py          core API and job runner
      routes_extra.py    profiling, drift, metrics, sampling, exceptions, cleanup
      routes_db_ingest.py     database import
      auth.py, db.py, parsers.py, connectors.py, cli_tools.py, ...
    tests/
    openapi.yaml
  relink-studio/         Vite project the script builds into
```

## Contributing

Bug reports, new matching methods, UI fixes and docs are all welcome.

- **Bugs.** Say what happened and what you expected. For matching accuracy, include a small anonymized sample of the source and target rows.
- **Pull requests.** Branch off `main`, keep one logical change per PR, and check the pipeline still runs end to end.
- **New method.** Add a function in `relink-api/app/matching.py` and wire it into the job runner in `routes.py`, following the signature of the existing ones.
- **Frontend.** Keep it in the single file unless there is a good reason to split it.
- **Style.** Match the surrounding code and do not reformat unrelated lines.

For big changes or anything that touches the matching logic, open an issue first.

## License

Apache License 2.0. See `LICENSE`.
