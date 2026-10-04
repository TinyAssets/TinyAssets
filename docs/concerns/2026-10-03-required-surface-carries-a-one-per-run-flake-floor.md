---
severity: P2
title: The required test surface carries a ~one-flake-per-run floor on main
filed: '2026-10-03'
summary: 'Three consecutive main-push `required-tests` runs each failed exactly ONE test, and all three were DIFFERENT (`test_dev_hygiene`, `test_open_receivers`, `test_mcp_probe` latency) on unchanged code. None is quarantined. So `main-red`''s attempt-1 re-run is load-bearing rather than a nicety, a share of the merge queue''s `failed_checks` drops are flakes not regressions, and a lone failure under a lone entry is weak evidence of causation'
---

# The required test surface carries a ~one-flake-per-run floor on main

**Filed:** 2026-10-03
**Verified:** 2026-10-03, from the `junit-required-tests` artifact of three
consecutive `push` runs of Tests on `main` (37093191928, 37088463393,
37087028562)
**Severity:** P2 — nothing is unprotected; what is wrong is that a red
`required-tests` on main is not by itself evidence of a regression

Each of those three runs failed **exactly one** test, and all three were
different tests, on code that had not changed in the relevant areas:

| run | the one failure |
|---|---|
| 37093191928 | `tests/test_dev_hygiene.py::test_apply_removes_a_merged_worktree_and_leaves_the_kept_one` |
| 37088463393 | `tests/test_open_receivers.py::test_the_sender_cannot_read_the_owners_workflow_step_or_run` |
| 37087028562 | `tests/test_mcp_probe.py::test_latency_subcommand_reports_elapsed_ms` |

A fourth, `tests/test_mcp_probe.py::test_latency_raw_includes_response`, failed
in merge-group run 37100054097. None of the four is in
`.github/known-failing-tests.txt`.

## Why this is filed rather than quarantined

A `flaky` ledger entry would make these verdict-neutral and stop the noise, and
that is the wrong trade here: `MAX_QUARANTINE` ratchets the ledger down
deliberately, and a flake that is hidden stops being fixed. The first two are
already being fixed — `test_dev_hygiene` by #4347 and `test_open_receivers`
likely by #4345, both queued as of filing. The `test_mcp_probe` latency cases
are new and have no owner yet; they are the reason this file exists.

## What it changes about reading CI

1. **`main-red`'s attempt-1 re-run is load-bearing**, not a nicety. At roughly
   one flake per run, a policy that reverted on the first red would revert
   constantly and almost always the wrong change.
2. **Some merge-queue `failed_checks` drops are flakes.** The lean-CI note
   measures 39 of them in four days and attributes them to repo-wide ratchets;
   this floor is a second cause, and it ejects entries that broke nothing.
3. **A lone failure under a lone queue entry is weak evidence of causation.**
   `scripts/miss_attr.py` reports such cases separately for exactly this reason
   rather than counting them as escapes
   (`docs/design-notes/2026-10-02-affected-only-merge-gate.md`).

## Resolving it

Attribute the two `test_mcp_probe` latency cases: both assert an exact
`latency_ms` against a sequenced fake clock, which is the shape that fails under
a slow or preempted runner. If that is the cause, pinning the clock rather than
the elapsed value fixes them without a ledger entry. Delete this file when the
four names above either pass consistently or carry owners.
