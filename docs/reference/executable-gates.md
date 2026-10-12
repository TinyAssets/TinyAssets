# Executable gates

Builder done-check: `python scripts/linux_oracle.py --ci`. It fetches
`origin/main`, selects the branch and working tree, then runs structural guards,
four PR affected-test shards, six queue shards, and the queue aggregate in Linux.
Compare its PR digest with CI `affected-tests` and its queue digest with CI
`select` for the same diff. A queue group with other PRs has a different diff.

| Gate | Entry point |
|---|---|
| Structural | `python -m scripts.ci_structural_guards` |
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills, mirror, encoding | `scripts/invariants_run.py --pre-commit` |
| Scope + review | `.github/workflows/pr-scope-guard.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
