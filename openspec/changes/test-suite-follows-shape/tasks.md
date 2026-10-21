## 1. Mark and resolve
- [x] 1.1 `verifies` marker registered in pytest config; `scripts/test_shape.py` resolves ids against active change rows and main spec requirements.
- [x] 1.2 Tag the suite that survives the C6 prune. Tests the rule makes eligible are deleted, not tagged.

## 2. Enforce
- [x] 2.1 Hygiene gate: a new or changed test without a resolvable id fails; superseding or deleting a row without removing or re-pointing its tests fails.
- [x] 2.2 Coverage-subset duplicate report from the CI coverage run.

## 3. Prune by itself
- [x] 3.1 Scheduled workflow opens one prune PR for orphaned and duplicate tests, with the deletions and `Test-Removal` lines.
