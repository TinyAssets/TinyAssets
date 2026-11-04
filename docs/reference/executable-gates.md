# Executable gates

Builder done-check: touched tests and `ruff` (in `platform/`: `npm ci`,
`npm run typecheck`, then `npm test -w <workspace>`); after a red CI job, rerun
only its failing tests, then push. `python scripts/linux_oracle.py --ci`
reproduces the old stack's CI in Linux (structural guards, four PR shards, six queue shards, the queue
aggregate; digests match CI `affected-tests` and `select`): for a CI failure that
will not reproduce, never per push.

A review is a PR review of the commit read, first line `APPROVE` or `BLOCK`;
it carries only by `carries()`, else a one-round review of the delta.

| Gate | Entry point |
|---|---|
| Structural | `python -m scripts.ci_structural_guards` |
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills, mirror, encoding | `scripts/invariants_run.py --pre-commit` |
| Scope, review | `pr-scope-guard.yml`, `review.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
