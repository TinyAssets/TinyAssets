Opened draft [PR #5245](https://github.com/TinyAssets/TinyAssets/pull/5245).

- Head: `3533cc18353c11eb4c26a9733311d4d4b68db07c`
- Implemented open/allowlist/ask at the single egress check, protected policy writes, credential-scope provenance, standing-entry approvals, receipts, and connection closure. Required regression included.
- Targeted Linux runs: **254, 113, and 14 passed**. Ruff, JS syntax, OpenSpec validation, and commit hooks passed.
- Worktree clean. No production access or secrets used.

Merge awaits C6 close-out. Independent floor review, deployment, live acceptance, and as-built spec sync remain pending under this lane’s constraints.