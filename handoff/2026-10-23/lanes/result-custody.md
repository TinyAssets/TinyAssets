Committed on `conv/source-custody`.

**HEAD:** `d89677c3b13e368090c51a340bc00122c3719b77`

Implemented broker custody, tmpfs leases, serialized refresh writeback, legacy migration, and cancellation before disconnect.

Files by role:

- **Daemon/shared:** source loader, schema, four CLI records.
- **Broker:** `server.py`, `vault.py`, new `subscription_custody.py`.
- **Box harness:** `subscription_runtime.py`, new `subscription_lease.py`, `subscription_connect.py`.
- **Recovery:** `restore_recovery.py`.
- **Page:** unchanged.
- **Box image:** no package or Dockerfile changes.

[Exact file inventory and verification](C:/Users/Jonathan/Projects/wf-source-custody/openspec/changes/box-subscription-sources/design.md:18).

Passed: 55 targeted Linux tests, 65 recovery tests, vault regressions, ruff, OpenSpec validation and commit hooks. Spec synced; required trailers verified; worktree clean.

B/C files untouched. No production access, deployment, push or independent review.