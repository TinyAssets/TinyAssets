## Why

C12 in `shape-convergence/tasks.md` and registry S17 require canonical OKF
knowledge files, disposable indexes, and one complete export (ADR-017/018).
The current bundle only seeds content, daemon and episodic databases still
own knowledge, and `wiki/okf_export.py` has no user-facing caller.

## What Changes

- Move knowledge ownership into owner-box OKF files; rebuild indexes from them.
- Migrate existing knowledge once after a verified snapshot, with a complete
  source census and lossless comparison before retiring canonical duplicates.
- Supply the one owner export door, private preview, credential exclusions and
  shared import/export manifest specified in universe-agent-harness §4.17.
- Include a dependency-free local runner in that exported folder.

## Delivery boundary

This branch implements the local runner, protected transfer/import door and
canonical-only production access for all seven knowledge consumers. Existing
owners cannot use those consumers until their namespaces are migrated and C7
enters the owner memory scope. Merge and deployment are blocked on the coordinated
C6 cutover prerequisites, verified backup and lossless migration (3.1), C7 scope
entry and C6 quota proof (3.2c), plus exact-head tests and independent review.
This draft does not complete C12 or mark S17 converged. Legacy source retirement
and live acceptance remain required.

## Impact

Registry row: S17. Floor: user data migration and export credential custody.
No new credentials or spending. No deployment until exact-head review, tests,
verified backup and live acceptance are available. No alternative host-path
store or second public export route.
