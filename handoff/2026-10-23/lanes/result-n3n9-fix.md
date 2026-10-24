Pushed the N3/N9 spec fixes to the same branch on [PR #5240](https://github.com/TinyAssets/TinyAssets/pull/5240). Head: `f0e1497ee682ea7785ba7494b45ce115cdb45285`. The worktree is clean.

The specs now retire the old Voice bridge requirements and define one realtime session path, bounded spending reservations with a receipt when a stream stops at its cap, one receipt per model turn, visible source capability gaps, and one storage rule shared by N3 and N9. Speech input keeps a transcript by default; partial frames remain ephemeral.

**Checks:** both OpenSpec changes passed strict validation; the Linux oracle passed 7 targeted broker stream tests; targeted Ruff, diff check, and commit hooks passed. A repository-wide Ruff run found existing errors in untouched files.

Implementation remains undone because this PR is spec only; the N3/N9 task lists carry that work.