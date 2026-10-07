---
severity: note
title: Run lineage edits_since_parent has no live writer
filed: '2026-10-06'
summary: only the retired update_node/rollback_node actions write node_edit_audit, so a live patch_branch edit never shows in a later run's lineage
---

# Run lineage `edits_since_parent` has no live writer

`runs.py` builds a run's `edits_since_parent` from `node_edit_audit` rows.
The only writers are `_ext_branch_update_node` (`api/branches.py`) and
`_action_rollback_node` (`api/evaluation.py`). Both were reachable only
through the fat `extensions` wrapper, which PR #4550 deletes.

The live edit path, `write_graph target=branch patch` -> `patch_branch`,
writes no audit row. A run after a live edit therefore records
`edits_since_parent == []`.

Found while rebuilding `test_run_lineage_surfaces_edits_since_parent` on
`patch_branch`: it fails with `[]`. That test was left deleted rather than
pinned to the gap.

Resolve by either having `patch_branch` record audit rows for the nodes it
changes, or retiring `edits_since_parent` along with the dead writers.
