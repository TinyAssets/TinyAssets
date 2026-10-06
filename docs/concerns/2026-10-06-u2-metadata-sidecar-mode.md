---
severity: P1
title: Unactivated U2 metadata migration assigns directory permissions to sidecar files
filed: '2026-10-06'
summary: 'Founder stop: the uncommitted metadata phase would add setgid and execute bits to regular sidecar files; activation remains OFF and no production data was touched.'
---

Claude's D206 cross-family review returned ADAPT. In the uncommitted
`deploy/role_metadata_migration.py`, `target()` applies SIDECAR_DIRECTORY_MODE
to every entry below `.universe-sidecars`, including regular files. That would
assign 1001:1100 mode 2710, adding setgid/group execute; reverse then preserves
the newly introduced executable bit. The review found no cross-user exposure.

U2 treats this permission widening as the founder's new-privilege stop rule.
Implementation stopped without patching the finding. The module is not installed
in the Dockerfile or called by startup. No production volume was mounted.
Its passing tests did not cover this regular-sidecar-file case and are not
acceptance of the permissions inventory.

A second correctness finding remains: both owner and metadata journals reject
changed configuration even after a stable generation. A new center or work
entry therefore blocks restart rather than admitting a fresh validated generation.
This fails closed. The new full-volume coordinator is also incomplete and
untested; do not activate or deploy any of this work.

Handoff: inspect the worktree at `wf-uid2` and D208 in
`openspec/changes/per-role-uid-split/delivery-u2.md`. The review receipt is
`C:/Users/Jonathan/AppData/Local/Temp/u2-metadata-review-result.md` (exit 0).
