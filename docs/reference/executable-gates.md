# Executable gates

Done-check: in `platform/`, `npm run typecheck`, touched files' tests and the
`ci/guards/` they can break; from the root, touched `pytest` files,
`check_shapes.py`, `ci_structural_guards.py`, `openspec validate
clean-sheet-platform`, and `test_hygiene_gate.py` on the body (each cut test:
`Test-Removal:`). Each passes by exit code (pipefail). After a red CI job,
rerun only its failing tests. After merging main, a change to a file
`platform/cutover.json` names runs `guards/cutover.test.ts`, fixing entries
it broke.

To land, post the verdict as a `COMMENT` review on the commit read, first
line `APPROVE` or `BLOCK`; auto-merge, armed at open, queues it. A verdict
carries across only the same change or a prose delta. Over 8 release-critical
files: look for a carried lineage. Two counted queue failures on one head
disarm auto-merge (`queue-handoff`): fix the head or drop the label.
Rerunning a `pull_request_target` run (scope guard) replays its old body:
edit the body instead.

| Gate | Entry point |
|---|---|
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills | `scripts/invariants_run.py --pre-commit` |
| Scope, review | `pr-scope-guard.yml`, `review.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
