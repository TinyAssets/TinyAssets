---
severity: P3
title: universe_server.py and daemon_server.py are each over 5,000 lines
filed: '2026-10-06'
summary: PLAN.md said the universe-server decomposition was nearly done (972 LOC); on main both server modules are over 5,200 lines, so the split is stalled, not finishing
---

# universe_server.py and daemon_server.py are each over 5,000 lines

**Filed:** 2026-10-06, carried from `PLAN.md` § Open Tensions when PLAN.md was
retired (ADR-005).
**Verified:** 2026-10-06 on `922e36d504` with `wc -l`.
**Severity:** P3. Large modules slow every change near them, but nothing is
wrong for users.

## Source (verbatim)

From `PLAN.md` § Open Tensions at `922e36d504`:

> **God-module decomposition is in-flight, not done.**
> `tinyassets/universe_server.py` is down from 14k peak to 972 LOC live in
> main; remaining cluster extractions sequenced per
> `docs/audits/2026-04-25-universe-server-decomposition.md`.

## Evidence

- `tinyassets/universe_server.py`: 5,224 lines.
- `tinyassets/daemon_server.py`: 5,261 lines. Its docstring still says the R7
  split into `tinyassets/storage/` is in progress.

The 972-line figure was stale; the module has grown back.

## What would resolve it

Either finish moving action logic into `tinyassets/api/` and storage into
`tinyassets/storage/` until each server module is a thin shell, or decide the
current size is acceptable and delete this file.
