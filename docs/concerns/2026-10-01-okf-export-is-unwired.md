---
severity: P2
title: Custody export exists in code but no user can reach it
filed: '2026-10-01'
summary: '`tinyassets/wiki/okf_export.py` (`export_universe_okf_bundle`) is the only data-export code, and nothing calls it — no MCP action, engine tool, effector, script or app route. PLAN Rule 4 says custody must stay exportable; today it is exportable only to someone running Python against the repo. The gap is wiring, not deletion.'
---

# Custody export exists in code but no user can reach it

**Filed:** 2026-10-01
**Verified:** 2026-10-01, Windows, origin/main `a7c63eb7`: `git grep -n "okf_export\|export_universe_okf_bundle"` outside `tests/`
finds only the module itself, `tests/test_universe_file_reads_are_bounded.py` (`TURN_PATH`, added by #4185 as a
bounded-read guard), PLAN.md, specs and docs. A gpt-6-astra refute on #4213 searched MCP dispatch, effectors,
engine tools, scripts, deploy and packaging and found no caller either.
**Severity:** P2

## What is true

- `export_universe_okf_bundle(universe_id, target_dir)` writes a curated one-way OKF v0.1 bundle from a
  universe's `wiki/pages/` (as-built: `openspec/specs/knowledge-retrieval-and-memory/spec.md`, the two OKF export
  requirements).
- No user surface calls it. The dark-code sweep (#4213) found it test-only. It was kept because of PLAN Rule 4
  ("custody must stay exportable", now ADR-018): deleting it would drop the principle silently.
- `docs/reviews/2026-09-24-capability-gap-audit.md` C4 ("export your own data and workflows") is the user-facing
  form of the same gap.

## Resolution

Wire an export a user can trigger through the user surface (for example, a `read_graph`/`read_page` export that
returns or stores the bundle), prove it live, and delete this file. If the founder decides export is rebuilt
differently, delete the module and update ADR-017's Consequences in the same change.
