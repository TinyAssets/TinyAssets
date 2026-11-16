# Executable gates

Done-check: in `platform/`, `npm run typecheck`, `npm test -w <ws>` per touched
workspace, and `npx vitest run --root ci guards/` for any `platform/` diff;
from the root, touched `pytest` files, `check_shapes.py`,
`ci_structural_guards.py --head HEAD` and `openspec validate clean-sheet-platform`.
Each passes by exit code (`set -o pipefail`). After a red CI job, rerun only
its failing tests.

A receipt binds to the exact head (or its tree-diff). A commit after review
gets a review of that delta alone.
Over 8 release-critical files: look for a carried lineage.
To land, post the verdict as a `COMMENT` PR review on the commit read, first
line `APPROVE` or `BLOCK` (`review.yml`), comment the receipt, and open the body with
`Drain-Review-Verdict: APPROVE`, `Drain-Review-Head: <sha>`, `Drain-Review-Artifact: <its URL>`;
auto-enroll arms the merge. Two counted queue failures on one head disarm
auto-merge (`queue-handoff`): fix the head, or drop the label to retry.

| Gate | Entry point |
|---|---|
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills, mirror, encoding | `scripts/invariants_run.py --pre-commit` |
| Scope + review | `.github/workflows/pr-scope-guard.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
