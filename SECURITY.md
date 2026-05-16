# Security policy

## Scope

Transcriptor is a **local-first** application. It binds to `127.0.0.1` by
default and does not expose itself to the internet. The "attack surface"
relevant to this project is therefore narrow:

- **HF token** persisted on disk at `~/.cache/transcriptor/config.json`.
- File uploads written to `uploads/` (re-named with a UUID prefix to avoid
    path traversal).
- The SSE / HTTP endpoints, if a user chooses to expose them on the LAN.

## What we do today

- The token file is written with mode `0600` (owner-read only).
- Uploaded filenames are sanitised via `Path(...).name`.
- The Docker image runs as a non-root user (`uid 1000`).
- Outbound calls to `huggingface.co` use explicit short timeouts so the UI
    can't hang on a slow or unreachable HF.
- Dependencies are pinned by minimum version with caps on major bumps.

## What we do *not* do

- No authentication: anyone with network access to the host can call the API.
    If you bind to `0.0.0.0`, you are responsible for putting a reverse proxy
    with auth in front of it.
- No CSRF protection: see above — same-origin local app, no cross-site issue.
- No rate limiting: a local user can't really DoS themselves.

## Reporting a vulnerability

If you find a security issue, **do not open a public GitHub issue**. Send a
description to the maintainer email listed on the GitHub profile, with:

- Steps to reproduce
- Impact assessment
- Optional fix proposal

You should expect an initial response within 7 days.
