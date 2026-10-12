# Executable gates

TinyAssets/TinyAssets has a live merge queue; `strict` is off. PR structural
and affected tests are required through `required-tests`. The queue checks the
combined commit, with conservative selection or ALL; main/schedules cover ALL.
Summed seconds are advisory, not a merge gate. There is no five-minute promise.

Builder done-check: `python scripts/linux_oracle.py --ci`. It fetches `origin/main`,
selects from the current branch and working tree, and runs both the four PR
affected-test shards and six merge-queue required-test shards in the Linux
oracle. The final line prints the PR and queue selection digests; compare the
queue digest with CI's `select` job digest and the PR digest with the
`affected-tests` selection step. The command also runs structural guards.

| Gate | Entry point |
|---|---|
| Structural + repair instructions | `python -m scripts.ci_structural_guards` |
| Affected / queue tests | `affected_tests.py` / `ci_required_tests.py` (scripts/) |
| Rulebook, drift, skills, mirror, encoding | `scripts/invariants_run.py --pre-commit` |
| Scope + review receipt | `.github/workflows/pr-scope-guard.yml` |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` |
| Deployment | `scripts/deployed_sha.py --assert-contains <sha>`; 2 = unknown |

Receipt in PR body:

```
Drain-Review-Verdict: APPROVE
Drain-Review-Head: <40-char sha>
Drain-Review-Artifact: docs/... | https://github.com/...
```

Retired `quality-gates.md` stays deleted; this index replaces it.
