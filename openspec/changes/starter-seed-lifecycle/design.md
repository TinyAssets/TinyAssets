## Context

This is the sole implementation contract for parent `universe-agent-harness` §4.14/D10 seed provisioning and upgrades. `starter-agent-out-of-plumbing` supplies the bundle content and consumes this mechanism; D7 owns extraction retirement. Current `read_operating_instructions` recreates missing `AGENTS.md` and substitutes defaults for empty/unreadable files. The starter slice removes that per-turn behavior.

## Goals / Non-Goals

Automatically deliver current seeds without changing owner customizations or making an absent owner a deployment dependency. Use the same receipt for every center. Do not build a second package system, alter D9 quarantine, migrate lessons, or preserve legacy runtime modes.

## Decisions

### One manifest and one receipt

Publish immutable `starter-agent-v1` with version, normalized relative paths, SHA-256 hashes and exact known predecessor seed hashes (including historical stock `AGENTS.md`). Only platform-published templates qualify; never infer stock content by fuzzy matching or use the founder's private files. Reject escaping paths and symlink/reparse traversal.

Store one owner/center-scoped receipt outside the agent-writable tree: bundle version, transaction ID, per-path prior state/hash, installed hash (or explicit never-installed state), outcome, and references to prior bytes needed for Undo. New-center provisioning writes this receipt in the same transaction as files. It is bookkeeping, never injected policy or an authority grant. Deletion tombstones persist across later versions and Undo.

### Autonomous per-path default

| Observed path | Automatic action |
|---|---|
| Absent, proven never installed | Exclusive create with the new bundle bytes. |
| Regular file exactly matches its last installed hash or a published predecessor seed hash | Atomically replace with the new version, conditional on the observed hash still matching. This includes stock `AGENTS.md` and stock skills. |
| Modified or unknown existing bytes, including empty files | Preserve byte-for-byte; one visible version-keyed note offers a candidate/diff. Unknown files are conservatively treated as customized. |
| Linked, unreadable or otherwise unverifiable path | Preserve without traversal; report the issue and offer the candidate. Never treat it as missing. |
| Absent after recorded installation, or a known historical seeded path absent before receipts existed | Preserve the deletion and record a tombstone; offer an explicit reinstall. Legacy missing `AGENTS.md` is conservatively an owner deletion. |

Fresh provisioning proves never-installed status. A legacy manifest records which paths previous provisioning could seed: newly introduced skills may be exclusively created, while absence of previously seeded instructions is not guessed away. Owner edits, emptying, removal, and explicit file Undo are customization choices. Future versions never reverse those choices automatically.

Receipt-recorded owner choices and deletion tombstones take precedence over stock-hash matching. In particular, Undo can restore bytes that happen to match a published predecessor; those bytes remain an explicit owner choice, not permission for the next upgrade to reapply what was undone. An owner can explicitly adopt a later offered version to resume automatic stock upgrades for that path.

Only applying a candidate over customized content or reinstalling an owner-deleted file needs the owner's choice, bound to the current hash/absence. That optional choice never gates runtime activation. Existing customized skills win by normal index precedence; the new content remains available as an inert candidate outside the indexed tree. An absent or unusable custom route can weaken that owner's chosen behavior, but cannot keep them on the old renderer or silently load stock instructions instead.

### One cutover, file-only Undo

There is no prepared/active/declined owner-acceptance state machine and no per-center legacy-renderer flag. At the coordinated release boundary, every center uses the new renderer and generic skill index, including dormant centers and those with collisions or no response. Before its first new-renderer turn, run or resume its automatic file transaction. A dormant center may defer file IO until wake; it never serves another old-renderer turn. The old renderer is removed in that release, not after a count of owner responses falls to zero.

This release boundary is the hard cutoff; a separate grace-period date would retain compatibility unnecessarily. Deployment records its actual UTC cutoff time and SHA. D6 reachability and starter proof are technical preconditions; waiting for human review is not one. No default grants authority or alters protected rules.

Lock per center; stage a journal outside indexed content; apply safe exclusive creates/compare-and-swap writes; expose completed files, receipt and index at a turn boundary. Coordinate concurrent writes with the existing file-write boundary, rechecking path identity/hash without following links. A race converts that path to preserved-customized and does not hold other paths or activation. Recovery resumes the same transaction idempotently before serving, without replaying a completed write or duplicating the note. Real storage failure is reported and retried as a technical failure, never converted to permission waiting or a fallback renderer.

The visible notice lists automatic upgrades/additions, preserved paths and the offered version. Its one-click Undo reverses only transaction writes whose current bytes still equal the installed hash: restore prior bytes for replacements, remove only unchanged newly added files. Changed/deleted files remain untouched and are reported. Undo records the owner's customization and stays on the new runtime. It never re-enables the old renderer or extraction. Existing private content used for Undo stays owner-scoped.

## Migration Plan

1. Land this single D10 mechanism and verify new-center receipts before consuming it in the starter release; no implicit per-turn seeding. Resuming a versioned release transaction before a dormant center's first turn is distinct from recreating missing files on each turn.
2. Publish the starter slice's immutable bundle and predecessor manifest; snapshot existing stock/custom/deleted cases safely.
3. In the coordinated release, perform safe automatic updates and activate all centers under the new renderer. Emit the visible note even when no path can be changed. No owner response is required.
4. Verify retry/Undo and a dormant center's first wake. Later versions use the same rules and receipts; no second D10 rollout implementation.

## Risks / Trade-offs

- Missing legacy receipts obscure deletions: preserve absence of historically seeded paths; add only proven new paths.
- Concurrent editing or crashes could lose data: conditional writes, durable journal and a data-loss mutation matrix cover replacement, create, recovery and Undo.
- Preserved custom guidance may be obsolete: visibly offer current content; owner instructions take precedence and runtime cutover still completes.

## Open Questions

None requiring an owner gate. Exact receipt storage is an implementation choice within existing platform-owned storage and owner/center isolation; no agent-writable receipts or external service are permitted.
