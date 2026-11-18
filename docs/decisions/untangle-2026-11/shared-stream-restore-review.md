# Shared stream restore review as built (2026-11-18)

Section P puts the shared stream extraction (S1) after receipts and rooms. Collection
erasures and history (H2 and H3), idle conversations and file versions (F2) will use it.
Before a second stream writes its first segment, decide which domain owns each ordering
key, storage byte and change notification.

On main `f934871ad` and combined `5db0a6738`, the schema constrains `archive_segments.stream`
to mail. Some mail reads already select that stream. Three others depend on the narrower
schema to supply the same scope implicitly.

| Consumer | What it reads today | What a second stream changes |
| --- | --- | --- |
| Recovery, `owner/src/restore.ts` → `Ledger.restored` and `Files.restored` | Mail uses the largest hot message sequence and the largest key of any segment. Files uses versions in hot entries and history. Its allocator singleton is excluded by `owner/src/export.ts`. | A cold file version can advance the mail allocator while the restored file allocator and feed floor stay below that version. |
| Storage breakdown, `filesDoors.storageUsage` → `Ledger.storedBytes` | Mail counts unique placed message contents and all segment contents. The door assigns mail its share first, then collections, then files. | An independent file-history segment contributes to the mail subtotal. The grand total can still agree. |
| Mail chain pagination, `owner/src/archive.ts` → `Ledger.movedSince` | The pagination retry checks newly created segments past its cursor. For a room without encoded members, every matching segment counts. | Archiving a different stream can restart a mail read whose records stayed in place. |

## Offline evidence

Bundled the production Files and Ledger classes at both commits and called their recovery,
storage and change queries against in-memory SQLite. The fixture populated only the
columns those functions read. Its mixed-stream variant deliberately admitted a file-history
row that the current production schema refuses. The storage door used the real mail-byte
calculation and a supplied total of the two distinct contents.

| Fixture | Restored mail sequence | Restored file version / feed floor | Storage door, mail / files | Mail moved after segment 1 |
| --- | ---: | ---: | ---: | --- |
| Mail fence ending at 12, 96 bytes. Hot file history ends at 5. | 12 | 5 / 5 | 96 / 0 | false |
| Add a file-history fence ending at 900 with 576 distinct bytes. | 900 | 5 / 5 | 672 / 0 | true |

Both commits gave the same results. This models what would happen if only the stream
constraint were widened. The full mixed-stream migration, archive format, restore and
collection of unreferenced bytes remain to be built and tested. Existing mail-only restore
cases retain their value. The allocator case in `owner/test/restore.test.ts` uses the
highest version in hot file history as its reference.

## Acceptance before the first new stream

Keep the current mail-only behavior during extraction. At the first non-mail writer,
restore a generation with independent stream keys and a cold maximum above every hot row.
Assert the next allocated key and the feed cursor in each affected domain. Exercise the
storage breakdown with distinct and shared contents and a concurrent unrelated segment
write during mail pagination. Review purge, compaction and retention consumers against the
same stream ownership. F2 additionally needs reachability for historical file bytes, pending
writes and retained generations before mark-and-sweep can free chunks. F3's timing remains
the founder's separate decision.
