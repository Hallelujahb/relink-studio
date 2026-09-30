# Relink Studio

**Match two lists of records that describe the same real-world things, even when the names don't line up, and review every doubtful match with confidence.**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

"Riverside Distribution Center 4" in one file and "Riverside DC #4" in the other. Two systems, one reality, no shared key. Reconciling them by hand is slow and error prone, and a pure black-box matcher is hard to trust.

Relink Studio gives you both speed and control. Several independent matching methods each propose an answer, the tool auto-approves only what is clearly safe, and everything else lands in a side-by-side review queue where a person makes the call. Every action is recorded in an audit log, and the final result exports as a clean CSV.

It works on any two files with a name column: facilities, stores, companies, people, places. It also handles non-Latin scripts such as Amharic, Arabic and Chinese without mangling them.

## Why Relink Studio

- **Local-first and private.** A Flask backend does the matching and a React frontend is the interface. No cloud service, no third-party AI. Your files, matches and decisions stay on your machine unless you export them.
- **Several opinions, not one score.** Fuzzy character matching, token overlap, optional semantic embeddings, optional probabilistic matching (Splink) and geometry corroboration each vote. A row is auto-approved only when the best score clears your threshold and no other method points somewhere else.
- **Built-in safety rails.** A collision guard stops two sources from silently claiming one target. Type validation catches matches that land on the wrong kind of record. Ties and disagreements go to a human.
- **A review queue people can work through.** Filter by kind (consensus, low confidence, type leak, collision, geometry suspect, unmatched), search, add notes, bulk approve or reject with undo, and check a place name against OpenStreetMap.
- **Accountable by design.** Decisions persist, every action is audited, and an optional submit-then-lead-approves hierarchy enforces four-eyes review.
- **One command to run.** `./relink.sh` sets everything up and starts the app.

## Quick start

You need Python 3.10 or newer (with `venv`) and Node 20 or newer.

```bash
git clone https://github.com/hallelujahb/relink-studio.git
cd relink-studio
./relink.sh
```

The script creates the Python environment, installs dependencies, builds the frontend, starts the server and prints a link. Open `http://127.0.0.1:5000`. Press Ctrl-C to stop. Logs go to `relink.log`. Re-running is fast, since dependencies and the build are only redone when something changed.

| Command | What it does |
| --- | --- |
| `./relink.sh` | Run on this machine only |
| `./relink.sh --lan` | Share on your private network, login required |
| `./relink.sh --setup` | Install and build, then stop |

If port 5000 is taken, set `RELINK_PORT`, for example `RELINK_PORT=5050 ./relink.sh`.

## How it works

The interface moves left to right through six tabs.

1. **Sources and shape.** Load a source and a target file (CSV, Excel or GeoJSON, or import a table from PostgreSQL, ClickHouse or MySQL). Pick an ID column (unique, present, no `/`) and a match column, which is the most important setting in the tool: choose the name column, not a category.
2. **Hierarchy and methods.** Set thresholds and text cleaning options, and choose which methods run.
3. **Safety and rules.** Configure the collision guard, tie handling and type validation.
4. **Review queue.** Work through every row that was not auto-approved, with each method's answer side by side.
5. **Approve and finalize.** Bulk actions with undo, a live summary, and a fast mode that runs the pipeline and approves rows that pass the safe check.
6. **Export and audit.** Download the linked result as CSV and inspect the full audit log.

Any setting on tabs 1 to 3 can be saved with **Download config** and restored with **Upload config**, so a teammate can reproduce your setup. Load the files first, then the config.

### Matching methods

Each method is an independent opinion on the best target for a row.

| Method | Approach | Default |
| --- | --- | --- |
| **A, difflib** | Character-level fuzzy matching. The best general default. | On |
| **B, token overlap** | Blocking plus Jaccard token overlap, an approximation of the `recordlinkage` package. | On |
| **C, semantic embedding** | Local sentence-transformers model. Useful when names differ in wording rather than spelling. | Off |
| **D, geometry corroboration** | Flags a candidate whose centre is more than 25 km from the source. Runs when both files are GeoJSON in WGS84. | Off |
| **E, Splink** | Probabilistic Fellegi-Sunter matching. Worth it at tens of thousands of rows or more. | Off |

Install the optional ones with:

```bash
relink-api/venv/bin/pip install -r relink-api/requirements-embedding.txt   # Method C (pulls in PyTorch)
relink-api/venv/bin/pip install -r relink-api/requirements-optional.txt    # Method E, PostgreSQL, ClickHouse
```

Notes on the optional methods:

- **Method C** compares every source row with every target row, so prefer A and B on big files. Its cosine scores run high for short names, so raise the blocking floor when you use it. A model given by name is downloaded once from the Hugging Face hub. For fully offline use, set `RELINK_EMBEDDING_MODEL` to a local folder and `RELINK_EMBEDDING_OFFLINE=1`. If the package is missing, the run still finishes with a warning.
- **Method E** may not have enough data to train on small files. It then falls back to default weights and flags the result as not calibrated.

Scores from different methods live on different scales, which is why disagreement between methods sends a row to review instead of letting the highest number win.

### Thresholds and text cleaning

- **Auto-approve** (default 0.95) accepts a match with no review. If your data has many small spelling differences, try about 0.90.
- **Needs-review floor** (default 0.80) marks low-confidence matches.
- **Blocking floor** (default 0.60) is the minimum name score for a candidate to be kept.
- **Normalization** folds Latin accents (café becomes cafe), keeps other scripts as they are, and can strip parenthetical tags like "(HQ)", trailing suffix words such as "inc" or "county", and digits. A name that is empty after cleaning never matches anything.

### Blocking

Methods A and B only compare a source row with target rows that share the same first three characters after normalization. This keeps matching fast, and it means a typo in the first three letters can hide a real match. If you see fewer matches than expected, check `GET /api/projects/<id>/diagnostics/blocking`, which shows how many source rows never share a key with any target row.

## Sharing on your network

`./relink.sh --lan` binds to `0.0.0.0` so a teammate can open the LAN link it prints. Logins are required and debug mode is always off. The first account must be created from the machine running Relink (the script prints the exact `curl` command), and after that only a lead can create accounts. Failed logins are throttled and sessions can be ended with `POST /api/auth/logout`.

LAN mode is meant for a network you trust or a VPN. It has no TLS or hardening for public access, so never forward the port to the internet. If you need that, put a reverse proxy with its own access control in front.

In the default local mode, Relink refuses requests whose Host header is not `localhost`, `127.0.0.1` or the configured host, and refuses cross-origin writes, so other sites open in your browser cannot talk to it. Add extra names to `RELINK_ALLOWED_HOSTS`. `GET /health` reports the running mode and whether auth is required.

## Configuration

Set these as environment variables before `./relink.sh`. An explicit value always beats the mode default, and invalid values stop the server at startup with a clear message.

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
| `RELINK_LIBRARY_DIR` | unset | Shared folder teammates can import files from |
| `RELINK_UPLOAD_EXPIRY_DAYS` | off | Sweeps old unreferenced uploads hourly |
| `RELINK_NOMINATIM_USER_AGENT` | placeholder | Set to your name plus an email or URL before real use |
| `RELINK_EMBEDDING_MODEL`, `RELINK_EMBEDDING_OFFLINE` | see Method C | |

## Database import

Import a table from PostgreSQL, ClickHouse or MySQL as a file. Install drivers with `requirements-optional.txt`, plus `requirements-mysql.txt` for MySQL (kept separate because that driver is GPL licensed). Rows are pulled into memory with a default cap of 100,000. Connection details, including the password, are used for that one request and never stored or logged. With logins on, these routes need the lead role. PostgreSQL is the tested path, so try ClickHouse and MySQL on something disposable first.

## API and command line

The frontend uses the core flow: upload, projects, run, jobs, review, bulk, undo, export, audit, geocode and login. The backend also exposes data profiling, schema drift, blocking diagnostics, run metrics, precision and recall against ground truth, review sampling, structured decision history, row explanations, exceptions, project deletion and storage cleanup. See `relink-api/openapi.yaml` and `relink-api/README.md`.

Offline tools that work on local files:

```bash
cd relink-api
venv/bin/python -m app.cli_tools profile data.csv
venv/bin/python -m app.cli_tools validate-config config.json
venv/bin/python -m app.cli_tools schema-diff old.json new.json
venv/bin/python -m app.cli_tools check-capabilities
```

## Docker

```bash
docker build -t relink-api relink-api
docker run -p 127.0.0.1:5000:5000 -v $(pwd)/data:/srv/data -v $(pwd)/uploads:/srv/uploads relink-api
```

The image serves the API. To serve the UI from the same container, mount a built `relink-studio/dist` and set `RELINK_FRONTEND_DIST`. Publish on `127.0.0.1` unless you have set up auth and a proxy.

## Status and scope

Relink Studio is built for files in the tens of thousands of rows, with matching in a single process and both files held in memory.

Enforced by the backend today: thresholds, all normalization options, methods A to E, collision guard, many-to-one allowance, tie downgrade, method disagreement, type validation, blocking floor, the approval hierarchy, and exceptions.

On the roadmap: the hierarchy chain and its related rules, exclude-by-name-pattern, chained and hub-and-spoke shapes, and output sort order are saved in configs already and will be wired into matching next.

Good to know:

- Everyone shares one workspace. Roles separate reviewers from leads.
- Source IDs cannot contain `/`, since API URLs use them as path segments.
- The frontend uses `xlsx` 0.18.5, the last version on npm. Open spreadsheets you trust, or switch to the maintained build from cdn.sheetjs.com.
- The job stream cannot authenticate from a browser, so the UI polls instead.

## Developing

The UI lives in one file on purpose: `relink_studio.jsx`. `./relink.sh` copies it to `relink-studio/src/App.jsx`, which is generated and git-ignored. For hot reload, run the backend with `./relink.sh` and in another terminal:

```bash
cd relink-studio
npm run dev      # http://localhost:5173, talks to the backend on port 5000
```

Tests:

```bash
cd relink-api
venv/bin/python -m pip install pytest
venv/bin/python -m pytest tests -v
```

```
relink-studio/
  relink.sh                install, build and run
  relink_studio.jsx        the whole frontend
  relink-api/              Flask backend
    app/                   matching, routes, auth, db, parsers, connectors, tools
    tests/
    openapi.yaml
  relink-studio/           Vite project the script builds into
```

## Contributing

Bug reports, new matching methods, UI fixes and docs are all welcome. Everyone who has helped is listed in [CONTRIBUTORS.md](./CONTRIBUTORS.md).

- **Bugs.** Say what happened and what you expected. For matching accuracy, include a small anonymized sample of source and target rows.
- **Pull requests.** Branch off `main`, keep one logical change per PR, and check the pipeline still runs end to end.
- **New method.** Add a function in `relink-api/app/matching.py` and wire it into the job runner in `routes.py`, following the signature of the existing ones.
- **Frontend.** Keep it in the single file unless there is a good reason to split it.
- **Style.** Match the surrounding code and do not reformat unrelated lines.

For big changes or anything touching the matching logic, open an issue first.

## License

Apache License 2.0. See `LICENSE`.
