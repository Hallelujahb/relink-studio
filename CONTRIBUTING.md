# Contributing to Relink Studio

Thanks for wanting to help. Bug reports, docs fixes, UI tweaks and new matching methods are all welcome.

## Before you start

- **Small fix or docs change?** Just open a PR.
- **Big change, or anything touching the matching logic?** Please open an issue first so we can talk it through before you spend time on it.
- Be kind. See the [Code of Conduct](./CODE_OF_CONDUCT.md).

## Setting up

You need Python 3.10+ and Node 20+.

    git clone https://github.com/hallelujahb/relink-studio.git
    cd relink-studio
    ./relink.sh

For hot reload on the frontend, keep `./relink.sh` running and in another terminal:

    cd relink-studio
    npm run dev

## Running the tests

    cd relink-api
    venv/bin/python -m pip install pytest
    venv/bin/python -m pytest tests -v

Please make sure they pass before you open a PR, and add a test if you fix a bug or change matching behaviour.

## Sending a pull request

1. Branch off `main`.
2. Keep it to one logical change per PR.
3. Check the pipeline still runs end to end (upload, run, review, export).
4. Match the surrounding code style and don't reformat lines you didn't change.
5. Say what you changed and why in the PR description.

## Where things live

- **New matching method:** add a function in `relink-api/app/matching.py` and wire it into the job runner in `relink-api/app/routes.py`, following the signature of the existing ones.
- **Frontend:** it lives in one file on purpose, `relink_studio.jsx`. Please keep it that way unless there's a good reason. `relink-studio/src/App.jsx` is generated, so don't edit it.

## Reporting bugs

Say what happened and what you expected. For matching accuracy problems, include a small **anonymized** sample of source and target rows. Please don't paste real data you aren't allowed to share.

Security problems go through [SECURITY.md](./SECURITY.md), not public issues.

## Getting listed

Add yourself to [CONTRIBUTORS.md](./CONTRIBUTORS.md) in your PR. By contributing, you agree your work is licensed under the Apache License 2.0.
