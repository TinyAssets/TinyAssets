# Executable gates

Done-check: in `platform/`, `npm run typecheck`, `npm test -w <ws>` per touched
workspace, and `npx vitest run --root ci guards/` for a `platform/` diff;
from the root, touched `pytest` files, `check_shapes.py`,
`python -m scripts.ci_structural_guards`, `openspec validate clean-sheet-platform`,
and `test_hygiene_gate.py` on the body (each cut test: `Test-Removal:`).
Check exit codes with pipefail. After a red CI job, rerun only
its failing tests.

A receipt binds to its head (or tree-diff); a later commit gets a review of its delta.
Over 8 release-critical files: look for a carried lineage.
To land, post the verdict as a `COMMENT` review on the commit read, first
line `APPROVE` or `BLOCK` (`review.yml`), comment the receipt, and open the body:
`Drain-Review-Verdict: APPROVE`, `Drain-Review-Head: <sha>`, `Drain-Review-Artifact: <URL>`;
auto-enroll arms the merge. Two counted queue failures on one head disarm
auto-merge (`queue-handoff`): fix the head, or drop the label.

| Gate | Entry point |
|---|---|
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills | `scripts/invariants_run.py --pre-commit` |
| Scope + review | `.github/workflows/pr-scope-guard.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
