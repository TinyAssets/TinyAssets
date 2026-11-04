# ADR-036: Mail Is Never Let Go; Acked Mail Moves to Archive Segments

## Status

Accepted (founder's rules, ruled by the coordinator 2026-11-04: one storage number, the only wall a user meets; nothing of the owner's is lost silently). Supersedes ADR-035 decision 5's retention of acked mail. Per ADR-005, ADR-035's body is not edited: only its Status line gains "decision 5 superseded for mail by ADR-036".

## Date

2026-11-04

## Context

- ADR-035 decision 5 lets retention make room at the tier's storage figure: the oldest placed collection history first, then the oldest acked messages with placed bodies, deleted one at a time and silently.
- The ledger also evicted the oldest acked message, silently, at `maxMessages` (50,000), a count the owner never sees.
- The founder's rules: the storage number is the one wall, and a full storage says so ("your storage is full: free some space or upgrade"); nothing the owner keeps is lost without the owner choosing it.

## Decision

1. **At the tier's storage, new mail is refused `storage_full`,** with the one wall's words and the upgrade path. Acked mail is never let go automatically: the owner deletes what it chooses (an agent's deletion purges that agent's mail, archived included). ADR-035 decision 5's collection-history retention stays until phase 2 decides it.
2. **Acked mail moves to archive segments instead of being evicted** (`owner/src/segments.ts`, `history_research.md` §4 phase 1):
   - a segment is files-plane content (ADR-035's placement and sweep, P's SSE-C under the owner's data key), its records in the export's own line grammar with a trailing index;
   - the owner's object keeps one fence row a segment, never one an item; ids carry their seq, so an archived message is found by its id;
   - the `archive` lane moves acked mail in two units from half the hot ceiling, and a post at the hot ceiling is `storage_busy` (503, `Retry-After`), retryable and never a delete;
   - a sealed segment holds at least 8 MiB of counted storage, so the fence rows are at most a tier's storage over 8 MiB, and a tier record may give at most about 0.5 TB (`TIER_STORAGE_MAX`); a larger tier needs larger segments first.
3. **Mail counts at its real bytes** in the one number, hot and archived: a hot message's rows as export lines, its body among them; an archived one, its segment's bytes.
4. **Unread mail has no count per mailbox.** One owner-wide ceiling on hot unacked mail (`maxUnacked`, the hot ceiling less one segment's records) is a technical ceiling of the object's 10 GB; past it a post is `ledger_full`, which says too much mail is unread and that reading and acking makes room. It never offers an upgrade.

## Consequences

- No mail is lost at a count or at the storage figure; a full storage refuses new mail, as consumer mail does.
- Export version 3 ships the segments as they are; a reader expands their rows as base lines.
- Unread mail stays hot; archiving it is a later phase with its own research. Collection history's `maxHistoryCharge` pruning, the ledger's newest-50,000 receipts and `CONVERSATIONS_KEPT`'s forgotten resume ids are silent losses owed to later phases (`tasks.md`).
