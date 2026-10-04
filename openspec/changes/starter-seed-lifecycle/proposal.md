## Why

D10 needs one seed mechanism for new and existing command centers. Waiting for every owner to approve stock-file updates would keep dormant centers on obsolete plumbing indefinitely; overwriting custom files would lose owner choices.

## What Changes

- Define D10's single versioned manifest, per-path receipt and conditional file Undo mechanism in a per-center sidecar SQLite database, with owner/center composite keys for receipt, journal, tombstone, notice and Undo records.
- Supply the transaction API for the **breaking** all-center cutover solely owned by `starter-agent-out-of-plumbing` task 2.3. Add never-installed paths and upgrade seed-identical files automatically; preserve customized or owner-deleted files and visibly offer the new version.
- Deliver new hooks/skills alongside untouched customized instructions; explicitly tell legacy empty/linked/unreadable-AGENTS owners that they previously ran runtime defaults and where guidance now resides.
- Record the same installed hashes during new-center provisioning; never reseed on startup or a conversation turn.
- Keep recovery and Undo limited to file changes, with no legacy renderer or extraction compatibility mode.

## Capabilities

### New Capabilities

- `starter-seed-lifecycle`: autonomous, non-destructive seed installation, upgrades, receipts and file Undo.

### Modified Capabilities

None. Parent `universe-agent-harness` §4.14/D10 delegates its seed machinery here; starter content and D7 learning retirement remain separately owned.

## Impact

Design only. Future implementation touches provisioning and platform-owned seed receipts. No product code, authority changes, D9 import activation changes or prompt content implementation in this revision.

Owner: Codex. Planning split recorded on `spec/starter-agent-out-of-plumbing`; implementation is unclaimed and must use its own `feat/starter-seed-lifecycle` worktree and PR. D10 channels and other parent work are not tasks in this change.
