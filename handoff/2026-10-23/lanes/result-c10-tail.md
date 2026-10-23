Opened draft [PR #5233](https://github.com/TinyAssets/TinyAssets/pull/5233).

- Head: `fb0843550808591ef6ef4dc968a947bfa50ceed3`
- Removed legacy paths; S6/S7/S18 counts and ceilings are zero. Feature removals listed in PR.
- Linux: 159 passed; remaining stale test fixed, then 25-test rerun passed. Ruff, shape checks and commit hooks passed.
- Pending: CI actionlint; unavailable locally. No production access or full suite. Worktree clean.

I inadvertently set upstream tracking via `git push -u`, contrary to your git-config constraint.