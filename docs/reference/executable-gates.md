# Executable gates

Done-check, once: in `platform/`, `npm run typecheck`, touched test files,
the `ci/guards` it can break and its mutations; from the root, touched
`pytest` files, `check_shapes.py`, `ci_structural_guards.py`, `openspec
validate clean-sheet-platform`, and `test_hygiene_gate.py` on the body
(`Test-Removal:`). Suites and recorders are CI's: push a draft early, and
name a recorder meant to differ (`Recorders-Differ: rv`). Exit codes decide.

To land, post the verdict as a `COMMENT` review on the commit read, first
line `APPROVE` or `BLOCK` (`review.yml`); auto-merge, armed at open, queues
it. A verdict carries only across the same change or a prose delta; any other
delta is reviewed. Over 8 release-critical files: look for a carried lineage.
Two counted queue failures on one head disarm auto-merge (`queue-handoff`):
fix the head, or drop the label. A rerun `pull_request_target` run (the scope
guard) replays its event's old body: edit the body, never rerun an old run.

| Gate | Entry point |
|---|---|
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills | `scripts/invariants_run.py --pre-commit` |
| Scope, review | `pr-scope-guard.yml`, `review.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
