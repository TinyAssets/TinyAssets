---
severity: P1
title: Inactive U2 restart replays permissions removed by the owner
filed: '2026-10-06'
summary: 'Founder new-privilege stop: completed owner journals ignore non-execute mode changes, so restart can restore stale group/other access; activation remains OFF.'
---

Claude D209 review (exit 0, ADAPT) found `_permissions` restores journaled mode
on a completed restart while `signature` detects only execute-bit changes.
An engine chmod 0644 -> 0600 therefore becomes 0644 again at restart; directory
group/other restrictions can likewise be lost. AGREE. This adds permissions
removed by the owner and triggers the explicit founder new-privilege stop.
The evidence is source review, not an executed cross-user exposure. No production
data was touched. The mode implementation remains uncommitted in wf-uid2.

Reviewer also found reverse migration of atomically replaced vault/liveness
entries treats the new forward mode/group as original instead of restoring the
specified daemon-only legacy target. AGREE; this is a separate correctness gap.

Handoff: D210 in openspec/changes/per-role-uid-split/delivery-u2.md. Review:
C:/Users/Jonathan/AppData/Local/Temp/u2-d209-review-result.md. Known-inode mode,
UID/GID preservation, sidecar -wal/-shm and existing crash tests pass, but there
is no chmod-without-execute-change or replacement-inode regression yet. No patch
was made after this stop; no new acceptance or deployment is claimed.
