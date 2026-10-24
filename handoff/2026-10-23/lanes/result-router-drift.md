HEAD: `dcd8b057b2216740f0a39b87c8e5208c13e8e7dc`

- `e360d53f7a1`: router failures return 503, `owner_box_unavailable`, and `Retry-After: 5`.
- `dcd8b057b22`: generic schema checking replaces per-table logic in affected flow/session/cache stores; ephemeral drift rebuilds, durable preference drift refuses without deleting data.

Failing-first regressions confirmed. Final Linux oracle: **158 passed across eight targeted files**. Ruff and commit hooks passed. Worktree clean.

No push, deployment, or production verification performed; this lane was kept local as requested.