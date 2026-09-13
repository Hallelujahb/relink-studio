# Relink Studio

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

Relink Studio is a small tool built for linking two lists of records that should refer to the same real-world things, even when the names don't match exactly. It was built out of a need for reconciling a facility list against a partner system's list of the same locations, where every side used a different naming convention and doing that reconciliation by hand got old fast. It doesn't know anything about any particular domain though. It works on any two files where one column in each file names the thing being linked (a store, a company, a person, a place, whatever), and it will happily run on data that has nothing to do with facilities at all.

Everything runs locally and self-hosted, with no cloud or third-party AI service involved. There's a small Flask backend and a React frontend; by default the two only talk to each other over `localhost`, and an explicit opt-in LAN mode (see below) lets them talk over your own private network instead. Nothing gets uploaded anywhere outside machines you control.

## Why this exists

If you've ever tried to join two spreadsheets on a name column, you know the problem. One file says "Riverside Distribution Center 4" and the other says "Riverside DC #4". One file has a clean hierarchy (region, district, site), the other has whatever the original data entry person felt like typing that day. A plain SQL join or `VLOOKUP` gets you maybe 60% of the way there, and then you're stuck doing the rest by hand.

Relink Studio tries to close that gap using a few different fuzzy matching approaches at once, shows you where they agree and disagree, and lets you review the cases it isn't confident about instead of guessing silently and hoping for the best.

## What's actually in the box

- A Flask backend (`relink-api/`) that does the real matching work: normalizing strings, blocking candidates so it isn't comparing every row against every other row, scoring, and combining multiple methods into one decision per row.
- A React frontend, either as a standalone file (`relink_studio.jsx`) you can drop into your own Vite project, or scaffolded automatically by the setup script into its own `relink-studio/` folder.
- A setup script (`setup_relink_studio.sh`) that sets up a Python virtual environment, installs backend dependencies, optionally installs the heavier embedding-based method, scaffolds the frontend if it doesn't already exist, and starts both servers.
- A couple of small test datasets under `test-dataset/` so you can try the pipeline out before pointing it at real data.

## Getting it running

```bash
git clone https://github.com/hallelujahb/relink-studio.git
cd relink-studio
chmod +x setup_relink_studio.sh
./setup_relink_studio.sh
```

That starts the backend on `http://localhost:5000` and the frontend on `http://localhost:5173`. Open the frontend URL in a browser and you're in.

If you want the embeddings-based matching method (Method C, more on that below) installed on first run, use:

```bash
./setup_relink_studio.sh --with-linktransformer
```

Fair warning, that pulls in torch, faiss, and sentence-transformers, which together are several gigabytes. It's entirely optional and the tool works fine without it for most use cases.

## Running locally vs. on your LAN

Relink is local-first: everything runs on your own machine, and it stays that way whether you use it alone or share it with a couple of coworkers on the same office network. There's no cloud component and no external AI service involved anywhere in the pipeline -- matching runs entirely with the local methods described below, and nothing about a project (your files, matches, or review decisions) ever leaves the machine Relink is running on unless you export a file yourself.

The backend has two modes, set with `RELINK_MODE` (or the flags on `run_relink.sh` below):

- **`local`** (default): binds to `127.0.0.1` only. Nothing else on your network can reach it. This is the original, unchanged behavior.
- **`lan`**: binds to `0.0.0.0` so other devices on the *same private network* can reach it (e.g. a teammate reviewing matches from their own laptop), requires authentication by default, and never runs with Flask's debug mode on.

### Local startup (single machine)

```bash
./run_relink.sh --local
```

Equivalent to the existing `./setup_relink_studio.sh` flow -- use whichever you already have set up. The backend prints its URL, port, mode, whether auth is required, and its data/upload directories on startup.

### Private-LAN startup (shared on your own network)

```bash
./run_relink.sh --lan
```

This binds the backend to `0.0.0.0` and turns on authentication by default (register a user with `POST /api/auth/register` and log in via `POST /api/auth/login` -- see `relink-api/README.md`). The startup banner prints both the local URL and the LAN URL your teammates should use.

**Firewall warning:** binding to `0.0.0.0` makes Relink reachable by *every* device on whatever network you're connected to, not just the teammates you intend to share it with -- coffee-shop wifi, a hotel network, or an untrusted office guest network all count. Only use `--lan` on a network you trust (home, a locked-down office LAN, or over a VPN), and check that your machine's firewall isn't more permissive than you expect.

**Do not expose Relink directly to the public internet.** Don't forward its port through your router, don't put it on a public IP, and don't tunnel it through a service that makes it internet-reachable. There's no built-in TLS, rate limiting, or hardening against internet-scale abuse -- LAN mode is for a trusted private network only, not for remote/public access. If you need that, put a real reverse proxy with TLS and its own access control in front of it yourself.

Every setting (`RELINK_MODE`, `RELINK_HOST`, `RELINK_PORT`, `RELINK_REQUIRE_AUTH`, `RELINK_CORS_ORIGINS`, data/upload paths, and more) can still be set individually via environment variable, and an explicit value always overrides the mode's default -- see `relink-api/app/config.py` for the full list. `GET /health` returns `{"status": "ok", "mode": "...", "auth_required": true|false}` if you want to check a running instance's configuration.

## The five tabs, and what each setting actually does
### 1. Sources & shape

Upload your source file and your target file here. CSV, Excel, and GeoJSON all work.

For each file you pick:

- **ID column**: whatever uniquely identifies each row. This is what shows up in the final output, so it should be something stable, not a name that might change.
- **Match column**: the actual text that gets compared between the two files. This is the single most important setting in the whole tool. If your file has a "facility_name" column and a "facility_type" column, you want match column to be the name, not the type.
- **Hierarchy chain**: if your data has a natural nesting (region, then district, then site, or country, then state, then city), add those columns here in order, broadest first. This isn't required, but it makes matching both faster and more accurate, because candidates get grouped by hierarchy level before name comparison even starts. Without it, every row in your source file could theoretically be compared against every row in your target file, which gets slow and noisy fast on anything but tiny datasets.

Linking shape covers three cases:

- **Single link**: one source file, one target file. This is what almost everyone wants, and it's the default.
- **Chained**: for when you need to match through an intermediate file, like matching a site list to a district list, and separately matching that district list to a region list.
- **Hub and spoke**: for when the same source column needs to be matched against several different target files independently, rather than through a chain.

If you're just getting started, ignore chained and hub and spoke. Single link covers the overwhelming majority of real use cases.

### 2. Hierarchy & methods

This is where you tell the tool how confident it needs to be before it trusts a match, and which matching methods to actually run.

**Confidence thresholds:**

- **Auto-approve at**: any match scoring at or above this gets accepted automatically, no human review needed. Default is 0.95, which is fairly strict. If your data has a lot of minor spelling variation, you might want to lower this to something like 0.90, otherwise almost nothing clears the bar and everything ends up in the review queue.
- **Needs-review floor**: anything scoring below this is treated as no match at all, not even worth reviewing. Anything between this and the auto-approve threshold goes into the review queue for a human to look at. Default is 0.80.

**Advanced matching settings:**

- **Hierarchy blocking floor**: how similar two hierarchy labels need to be (fuzzy, not exact) before candidates in that group even get compared on name. Default 0.60.
- **Keep digits in normalization**: leave this on unless you're sure your names never use digits meaningfully. If you turn it off, "Site 1" and "Site 2" become indistinguishable to the matcher, since both just become "site".
- **Strip parenthetical tags**: removes things like "(HQ)", "(Branch)", or "(Old)" before comparing names. Usually a good idea, on by default.
- **Strip trailing words**: a comma-separated list of words to drop before comparing, for things like "office" or "branch" that show up as noise at the end of names. Leave blank if you don't need it.
- **Case-sensitive matching**: off by default, and it should probably stay off. Capitalization tends to vary between systems even when everything else about the name is identical.

**Matching methods**, each one an independent opinion on whether two rows match:

- **Method A, difflib**: character-level fuzzy matching using Python's built-in `difflib`. No extra dependencies, and it's the best general-purpose default for spelling variants and small formatting differences. On by default.
- **Method B, recordlinkage**: a second, differently tuned distance measure, meant to corroborate what Method A finds rather than replace it. Also on by default.
- **Method C, linktransformer**: semantic matching using sentence embeddings. Good for cases where two names mean the same thing but don't share much spelling, though in practice it tends to be weaker than A or B on close spelling variants and can occasionally collapse two genuinely different records onto the same wrong neighbor. Best treated as a secondary signal rather than something to rely on by itself. Off by default, and needs the optional install mentioned above.
- **Method D, geometry corroboration**: not a name scorer at all. If both files have real latitude/longitude or polygon geometry, this flags a candidate as suspicious when its coordinates sit unexpectedly far from the source record's location, even if the name matched perfectly. Useful as a sanity check, especially for place-based data where two very differently located things can coincidentally have similar names.
- **Method E, splink**: a probabilistic matcher based on the Fellegi-Sunter model, which learns how much weight each field should carry from the actual data rather than using a fixed formula. Worth turning on once linking tens of thousands of rows or more. For smaller datasets, A and B are simpler and just as effective, so this stays off by default.

### 3. Safety & rules

This tab is mostly about catching the ways automated matching can go quietly wrong.

- **Collision guard**: if two different source rows both land on the same target row, that's usually a sign something's off, not a genuine double match. With this on, both get downgraded to needs-review instead of one silently winning a coin flip.
- **Allow many-to-one**: turn this on only when you actually expect many source rows to legitimately share one target, like several sub-facilities reporting up to the same parent office. Without it, collision guard treats every shared target as suspicious, which is the right default but the wrong behavior for genuinely one-to-many data.
- **Type / level validation**: if your target file mixes different kinds of rows together (say, site-level rows and district-level rows in the same file), point this at the column that tells them apart and give it the value you actually want to match against. Anything that matches by name but points at the wrong type gets downgraded automatically.
- **Additional matching rules**: auto-downgrade ties sends a row to review if two candidates score identically but point at different targets, rather than picking one arbitrarily. Require hierarchy match rejects a candidate outright if its hierarchy level never lined up, even with a great name score. Strict hierarchy match switches hierarchy comparison from fuzzy to exact string, which is best left off, since hierarchy labels tend to have the same spelling inconsistency problems as the names being matched. Flag low-coverage groups warns when one hierarchy group has noticeably fewer matches than its siblings, which is often a naming mismatch rather than a real gap in the data.
- **Exclude by name pattern**: a regular expression for filtering out target rows that shouldn't be matched against at all, even if their type looks right. Useful for things like warehouses or storage depots that get lumped in with facility lists but aren't actually facilities.

### 4. Review & approve

Every row that didn't clear the auto-approve threshold shows up here, one row at a time, with a side-by-side comparison of what each enabled method found. The method cards shown are whichever ones are actually turned on in Hierarchy & methods, nothing is hardcoded to a fixed set. Each row can be approved or rejected directly, given an audit note, or checked against OpenStreetMap for a second opinion on place names. A row can be resolved two ways: approve or reject it directly and it writes to the backend immediately, or stage a decision (approved or rejected) that sits locally until "Merge and finalize" is pressed, which is useful for clearing a large backlog without committing to each one individually. The summary panel with row counts, bulk actions, and the merge button stays visible while scrolling through rows, so those controls are never out of reach. Once everything is decided, merging locks in the final answer set.

### 5. Export & audit

Download the final linked result as a CSV, and browse the audit log of every action taken during the session (approvals, rejections, undo operations, exports). The export includes which method's answer was used for each row and its confidence score, so the reasoning behind each match is visible rather than a black box. This tab only produces the authoritative export once results have been merged, since exporting beforehand would mean downloading a partial or undecided set of matches.

## Config files

Every setting on tabs 1 through 3 can be saved to a JSON file with the "Download config" button in the top bar, and loaded back with "Upload config". This is worth doing once settings that work well for a particular kind of data have been found, since it means not having to click through every threshold and toggle again next time. Upload the source and target files first, then upload the config, since the config only sets column selections and toggles, it doesn't upload files on anyone's behalf.

## A note on blocking

Both Method A and Method B use a blocking key to avoid comparing every source row against every target row, which would be painfully slow on anything beyond a few hundred rows. The blocking key is just the first three characters of the normalized name. This means two names have to agree on their first three letters (after normalization) before they're ever compared at all, no matter how similar the rest of the name is. A typo in the first three letters of a name, or a nickname that changes the first word entirely, can cause a real match to be silently missed rather than merely scored low. It's worth knowing about if you're getting fewer matches than you expect, since the cause is sometimes blocking, not the similarity score itself.

## Known limitations

- Method C (linktransformer) is wired into the backend but has not been fully verified end to end, since it needs to download a model from Hugging Face and that isn't available in every environment. It should work, but test it before relying on it for anything important.
- Method E (splink) is an optional dependency, not installed by default. Install it with `pip install -r relink-api/requirements-optional.txt` inside the backend's virtual environment if you want it.
- Geometry corroboration (Method D) only kicks in when both your source and target files are GeoJSON with actual geometry in them. Plain latitude and longitude columns in a CSV won't trigger it, since the tool needs real geometry objects to compute distances from.
- The Nominatim lookup button in the review queue is rate limited to one request per second, per OpenStreetMap's usage policy. Set the `RELINK_NOMINATIM_USER_AGENT` environment variable to real contact information (your name plus an email or project URL) before relying on it for anything beyond occasional testing.

## Project layout

```
relink-studio/
  relink-api/          Flask backend
    app/
      config.py         RELINK_MODE/HOST/PORT/REQUIRE_AUTH/CORS resolution
      matching.py       The actual matching algorithms
      routes.py         API endpoints, job orchestration, export
      ...
    requirements.txt
  relink_studio.jsx     Standalone frontend, drop into your own Vite project if you'd rather not use the setup script
  setup_relink_studio.sh
  run_relink.sh         Start the backend in --local or --lan mode
  test-dataset/         Small datasets for trying the pipeline before using real data
```

## Contributing

Contributions are welcome, whether that's a bug report, a new matching method, a UI fix, or better docs.

- **Bugs and feature requests**: open an issue describing what happened versus what was expected. For matching accuracy issues, include a small anonymized sample of the source and target rows involved, since matching behavior is hard to debug from a description alone.
- **Pull requests**: fork the repo, branch off `main`, and keep changes focused. One logical change per PR is easier to review than a large mixed one. Run the backend locally and confirm the affected pipeline still runs end to end before opening the PR.
- **Adding a matching method**: methods live in `relink-api/app/matching.py` as standalone functions and get wired into the job runner in `routes.py`. Following the existing method signatures (source rows, target rows, id/match columns, matching config in, a per-row result dict out) makes a new method drop in cleanly alongside A through E.
- **Frontend changes**: the UI is a single file by design, `relink_studio.jsx` (or `src/App.jsx` once scaffolded), rather than split into many small components. Keep that pattern unless there's a good reason to break it out.
- **Code style**: match whatever's already in the surrounding file rather than reformatting unrelated code in the same PR. Unrelated formatting changes make diffs harder to review.

If a change is large or touches the matching logic, opening an issue first to discuss the approach before writing the code is appreciated. It saves rework on both sides.

## License

Licensed under the Apache License, Version 2.0. See the `LICENSE` file for the full text. In short, you can use, modify, and distribute this freely, including commercially, as long as you keep the license and copyright notice and note any changes you made to the original files.
