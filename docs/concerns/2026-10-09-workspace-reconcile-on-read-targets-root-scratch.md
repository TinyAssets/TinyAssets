---
severity: P3
title: Workspace reconcile-on-read targets /scratch and fails on run listing
filed: '2026-10-09'
summary: '`_reconcile_workspace_on_read` is reached with a top-level path (likely the data root), so `_ensure_scratch_root` tries to mkdir `/scratch` and logs a PermissionError traceback on `list_runs`'
---

# Workspace reconcile-on-read targets `/scratch`

**Filed:** 2026-10-09 · **Verified:** production daemon logs (`tinyassets-logs`), 00:49:22Z and 03:53:37Z

## Finding

Run listing (`list_runs`, the only caller of `_reconcile_workspace_on_read`) logged `ERROR:tinyassets.runs:workspace startup reconciliation failed` at both timestamps above, with
`PermissionError: [Errno 13] Permission denied: '/scratch'` raised from
`runs._ensure_scratch_root` via `_reconcile_workspace_on_read` → `ensure_workspace_reconciled`.
`_ensure_scratch_root(base)` builds `base.parent / "scratch"`, and its docstring expects `base`
to be a universe directory. The `/scratch` target means `base.parent` was `/`: `base` was a top-level path, most likely the
data root `/data`. Neither that path nor the caller passing it is verified yet. The error is caught and logged, and the listing still succeeds. But the
reconciliation it guards never runs on that path, and the traceback is noise in
investigations.

Seen while debugging PR #4561; it isn't the cause of that bug.

## Fix shape

Find the caller that passes the top-level path, and pass the path that `ensure_workspace_reconciled`
expects. Or make the function resolve the scratch root from the data root explicitly. Then
check that one run listing on production logs no traceback.
