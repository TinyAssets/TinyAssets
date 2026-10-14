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

This branch starts with the independently buildable local runner specified in
§4.17. It is not connected to production, does not complete C12, and must not
mark S17 converged. The owner-box C6 cutover remains a prerequisite for the
canonical-store migration and production export. The complete convergence must
remove the old canonical paths and satisfy the net-line and review gates.

## Impact

Registry row: S17. Floor: user data migration and export credential custody.
No new credentials or spending. No deployment until exact-head review, tests,
verified backup and live acceptance are available. No alternative host-path
store or second public export route.
