---
severity: P1
title: Inactive U2 replacement metadata retains broker group after rollback
filed: '2026-10-06'
summary: 'D211 fixes stale mode replay, but the repeated D210 ownership-provenance finding survives a replacement followed by forward restart; U2 is handed off under AGENTS rule 7.'
---

D211 at 5ea4a3e572 fixes current-mode replay: forward/reverse/resume keep or narrow
current mode, including an owner chmod 0644 -> 0600 after journal recording.
Linux selection passes 140 tests with zero skips. Installed production-image
owner-substep proof passes under the exact seven startup capabilities.

Unresolved: after initial forward migration, atomic replacement creates a new
vault/liveness inode at 1001:1102. A forward restart records those current IDs
as its original ownership. Reverse then restores 1001:1102 instead of legacy
1001:1001. Direct replacement-to-reverse works, but its newly saved provenance
also fails a later direction cycle. Modes remain capped; no demonstrated
cross-user exposure or production incident is claimed.

Claude's D211 review completed with exit 0 and ADAPT. AGREE: this repeats D210
finding 2, so AGENTS rule 7 requires a handoff without another implementation
patch. Durable evidence and remaining per-item scope: D213 in
openspec/changes/per-role-uid-split/delivery-u2.md. Full reviewer output:
C:/Users/Jonathan/AppData/Local/Temp/u2-d211-review-result.md.

Both PRs remain draft and not merge-ready. Startup activation stays OFF;
production data was never mounted or changed.
