---
severity: P2
title: Legacy provider-child bytes need offline cleanup
filed: '2026-10-05'
summary: Old provider-child files remain charged but hidden after PR 4472; an offline archive cleanup is prepared and not yet run.
---

# Legacy provider-child storage

[PR #4472 review](https://github.com/TinyAssets/TinyAssets/pull/4472) identified
old `.runtime/provider-child` bytes charged to owners but hidden by the new
disposable mount. This is not an accounting exemption: persistent files remain
charged until relocated. Existing native sessions and credentials must survive.

`scripts/cleanup_provider_child.py` defaults to a read-only inventory. It moves
only one explicitly selected home's old subtree to a private, same-filesystem
archive outside **all** universe storage. It never deletes files. Design and
refusal matrix: `docs/design-notes/2026-10-05-provider-child-cleanup.md`.

Operator procedure (not executed in this lane):

1. Stop all writers/provider launches and other cleanup processes. Identify the
   actual universe storage root and create a private 0700 archive outside every
   charged storage root on the same filesystem. Protect its parent from writers.
2. Inventory each selected home:
   `python scripts/cleanup_provider_child.py --storage-root /storage/universes --universe SELECTED_ID --archive-root /storage/legacy-archive --dry-run`.
3. Review the exact source, archive, files and logical bytes. Re-run with
   `--apply --offline` only in the stopped service window. Preserve JSON output
   as the source-to-archive mapping. Never move `.runtime` as a whole.
4. Re-run to verify no-op; restart service and verify storage reporting plus a
   real owner's provider session. Keep the archive for recovery; do not purge
   it as part of this procedure. Restore only offline into an absent source.

Close after recorded operational cleanup and real-user proof. No production
cleanup, archive purge, deployment or live proof has been performed here.
