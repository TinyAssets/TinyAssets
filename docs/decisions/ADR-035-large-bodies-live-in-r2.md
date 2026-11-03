# ADR-035: Large Bodies Live in R2; the Owner Object Keeps References

## Status

Accepted (founder, 2026-11-03: where large data lives is an architecture rule, built in and automatic, not a decision he makes).
- **It amends** ADR-032 decision 1: the owner's object holds the ledger's bodies and the collections, but a body past the threshold below lives in R2, with its reference in the object.
- **It extends** ADR-032 decision 5: the files plane's bucket and manifest also hold those bodies.
- **Per ADR-005**, ADR-032's body is not edited. Only its Status line gains "amended by ADR-035".

## Date

2026-11-03

## Context

Each owner's Durable Object is capped at 10 GB of SQLite. The owner-bound guard holds the worst case under half of that. The limits audit (`openspec/changes/clean-sheet-platform/limits-audit.md`) found that its raises left 2.1 MB of margin, and that its best raises did not fit at all. Message bodies, collection row bodies and their history were costed in the object at their full size. So every raise of a byte bound took room from another table.

Facts read on 2026-11-03 from Cloudflare's documentation:
- **Durable Objects** (`durable-objects/platform/limits`, `pricing`):
  - 10 GB per object;
  - 2 MB at most per string, BLOB or row;
  - SQL storage at $0.20 a GB-month, rows written at $1.00 a million, rows read at $0.001 a million;
  - 6 simultaneous outgoing connections per request.
- **R2** (`r2/pricing`, `r2/platform/limits`):
  - storage at $0.015 a GB-month;
  - $4.50 a million puts and $0.36 a million gets;
  - objects up to 5 TiB.
- **SQLite's page**, as the guard and the files manifest model it (`owner/src/manifest.ts`):
  - a 4,096-byte page;
  - a rowid table keeps a record of up to 4,061 bytes in place;
  - past that, the rest of the record takes overflow pages of 4,092 bytes, each read with the row.

R2's read latency is not documented [I: a network round trip from the object, tens of milliseconds; to measure live].

## Decision

1. **The rule, with no switch.** A body of more than `PLACE_BYTES` (4,096) UTF-8 bytes is never stored in the object's SQLite. This covers:
   - a collection row's body;
   - a collection history entry's body;
   - a message's body.

   Each lives in R2 as content of the files plane. Its row keeps an empty body and the content's id; its size is counted on the row. A body of 4,096 bytes or less stays in its row. The files plane already keeps files of up to 4,096 bytes inline, so the object has one threshold. Why 4,096:
   - a body up to it keeps its row within one page, and adds no overflow page to every read of the row;
   - past it, each 4,092 bytes is another page read with the row. Storing it costs 13 times R2's rate, so a put pays for itself within months for a body this size [I: by the prices above, a 4 KiB body kept about 6 months];
   - placing smaller bodies would turn one page read into many R2 round trips, at 6 connections at a time, for no room the guard needs.
2. **Through the files plane's own path.**
   - In its first unit, the write door begins an upload of one chunk, and the chunk's row is held before any byte is sent.
   - The door then puts the bytes to R2 through `FILES`, which verifies the SHA-256.
   - A second unit adopts the upload. It makes the content, and the row names it in the same transaction. Migration 13's triggers count each reference on the content.

   Every write stays a gate unit. Nothing about the content is committed before R2 holds it.
3. **Crash safety.** A crash between the put and the commit leaves an upload that no row names. Housekeeping releases it at its expiry, and the chunk becomes garbage. The sweep deletes the garbage from R2 after its grace. A read of a body that a later write let go still finds it within that grace.
4. **Quota, erasure and export.**
   - The placed bytes count in the files byte quota (`chunk_bytes`). That quota is now the tier's storage figure from the control record: free 25 GB, paid 250 GB. The owner may set it lower, never higher.
   - The erasure deletes every chunk the owner's rows name before it wipes the database. That covers both files and placed bodies.
   - The owner export (slice P) reads the same chunks.
5. **Reads page by bytes.** A pull, a history read or a mailbox read returns at most `READ_PAGE_BYTES` (8 MiB) of bodies after its first item. A door reads the placed bodies from R2 outside any unit, after its unit locates them.
6. **Not every term dominated by a body is placed.** A connection's models list (640 KiB at most) is read inside every egress admission unit, once for each sandbox model call. Placing it would put an R2 round trip on every model call. It stays in its row, bounded by the connection count, and fits the guard.

## Consequences

- The ledger, the collections and their history no longer cost the object their bytes: only their rows. Their byte bounds become storage, counted against the tier.
- With the audit's other structural changes, the audit's raises fit with 774.1 MB of margin, where they had 2.1 MB:
  - collection history counted in bytes;
  - one recipient a message;
  - the mailbox caps within the ceilings;
  - grant scopes counted in bytes.
- A write of a large body takes two units and an R2 put. A read of a large body takes an R2 get. A small body costs what it cost before.
- Owed live:
  - R2 read latency from an owner object;
  - SSE-C on the placed chunks, the same probe the files plane owes.
