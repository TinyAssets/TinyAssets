Pushed the final N1 spec edits to [PR #5228](https://github.com/TinyAssets/TinyAssets/pull/5228). **Head SHA:** `00c1c734619e6aabae50758ba103c47e3ad61661`.

The spec now defines a per-installation breaker after three failed attempts in 60 seconds, owner-visible degraded state, and reconciliation on wake. It also scopes retirement and snapshot views to the calling owner, and makes the Meta, Slack overlap, and connected-email fixtures gates for their respective channels.

**Checks passed:** OpenSpec validation, Ruff, `git diff --check`, commit hooks, and the targeted Linux oracle test (38 passed). Channel fixtures, implementation, spec sync, and live proof remain open because this PR is spec-only.