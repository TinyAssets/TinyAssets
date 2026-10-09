---
severity: P3
title: Workspace reconcile-on-read targets /scratch and fails on every run read
filed: '2026-10-09'
summary: '`_reconcile_workspace_on_read` is reached with the data root, so `_ensure_scratch_root` tries to mkdir `/scratch` and logs a PermissionError traceback'
---

# Workspace reconcile-on-read targets `/scratch`

**Filed:** 2026-10-09 · **Verified:** production daemon logs (`tinyassets-logs`), 00:49:22Z and 03:53:37Z

## Finding

Each run read logs `ERROR:tinyassets.runs:workspace startup reconciliation failed`, with
`PermissionError: [Errno 13] Permission denied: '/scratch'` raised from
`runs._ensure_scratch_root` via `_reconcile_workspace_on_read` → `ensure_workspace_reconciled`.
`_ensure_scratch_root(base)` builds `base.parent / "scratch"`, and its docstring expects `base`
to be a universe directory. So some caller passes the data root (`/data`), which turns the
target into `/scratch`. The error is caught and logged, and the reads still succeed. But the
reconciliation it guards never runs on that path, and the traceback is noise in every
investigation.

Seen while debugging PR #4561; it isn't the cause of that bug.

## Fix shape

Find the caller that passes the data root, and pass the path that `ensure_workspace_reconciled`
expects. Or make the function resolve the scratch root from the data root explicitly. Then
check that one run read on production logs no traceback.
