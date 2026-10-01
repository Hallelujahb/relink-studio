# Security Policy

## Supported versions

Only the latest commit on `main` gets fixes.

## Reporting a vulnerability

Please **don't open a public issue** for security problems.

Use GitHub's private reporting instead: go to the **Security** tab of this repo and click **Report a vulnerability**. If that's not available, email hallelujahEzra@gmail.com.

Helpful things to include: what you found, how to reproduce it, and what you think the impact is. This is a small project run by volunteers, so expect a first reply within about a week.

## What's in scope

- Auth, roles and session handling
- The Host/Origin checks in local mode
- File upload and parsing, path handling in the shared library and cleanup routes
- Database import (connection handling, SQL identifier safety)

## What's out of scope

- LAN mode has no TLS and no hardening for the public internet. Exposing the port to the internet is unsupported by design. Use a trusted network or a VPN, or put a reverse proxy with its own access control in front.
- The frontend's `xlsx` 0.18.5 dependency is a known trade-off, documented in the README. Open spreadsheets you trust.
