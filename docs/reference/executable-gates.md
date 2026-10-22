# Executable gates

Builder done-check: touched tests and `ruff`; after a red CI job, rerun only its
failing tests, then push. `python scripts/linux_oracle.py --ci` reproduces all
of CI in Linux (structural guards, four PR shards, six queue shards, the queue
aggregate; digests match CI `affected-tests` and `select`): for a CI failure that
will not reproduce, never per push.

A receipt binds to the exact head (or its tree-diff). A trivial commit after
review gets a one-round delta review, the reviewer told only that delta.

| Gate | Entry point |
|---|---|
| Structural | `python -m scripts.ci_structural_guards` |
| Affected / queue tests | `scripts/ci_required_tests.py` |
| Rulebook, drift, skills, mirror, encoding | `scripts/invariants_run.py --pre-commit` |
| Scope + review | `.github/workflows/pr-scope-guard.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |
