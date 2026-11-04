# ADR-035: Large Bodies Live in R2; the Owner Object Keeps References

## Status

Accepted (founder, 2026-11-03: where large data lives is an architecture rule, built in and automatic, not a decision he makes). Decision 5 superseded for mail by ADR-036 (2026-11-04): acked mail is never let go at the quota.
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
- **Workers** (`workers/platform/limits`): an isolate's 128 MB of memory.
- **SQLite's page**, as the guard and the files manifest model it (`owner/src/manifest.ts`):
  - a 4,096-byte page;
  - a rowid table keeps a record of up to 4,061 bytes in place (`TABLE_KEPT`);
  - past that, the rest of the record takes overflow pages of 4,092 bytes, each read with the row.

R2's read latency is not documented [I: a network round trip from the object, tens of milliseconds; to measure live].

## Decision

1. **The rule, with no switch.** A body of more than `PLACE_BYTES` (2,048) UTF-8 bytes is never stored in the object's SQLite. This covers:
   - a collection row's body;
   - a collection history entry's body;
   - a message's body.

   Each lives in R2 as content of the files plane. Its row keeps an empty body and the content's id; its size is counted on the row. A body of 2,048 bytes or less stays in its row. Why 2,048:
   - the whole record must stay in the 4,061 bytes a rowid table keeps in place, or every read of the row reads overflow pages too. The body gets half of that room. The row's own names, ids and numbers take the other half: a collection row's collection, row id, writer, creator, etag, content id and files scope come to about 1,700 bytes at the names a caller usually picks;
   - past it, keeping the body in SQLite costs 13 times R2's storage rate. An R2 put and get cost what about a year of SQLite storage costs a 2 KiB body, so a body past the threshold pays for its placement;
   - placing smaller bodies would turn one page read into many R2 round trips, at 6 connections at a time, for no room the guard needs.

   The files plane keeps files of up to 4,096 bytes inline. Its rows hold only the file's bytes and a few ids, so its own threshold stays as it was.
2. **Through the files plane's own path.**
   - In its first unit, the write door begins a placement upload, and its chunks' rows are held before any byte is sent. A body is at most `BODY_BYTES_MAX` (8 MiB), two 4 MiB chunks, one `FILES` call. That is half of the platform's one request-body ceiling, 16 MiB (`EGRESS_BODY_MAX`; the API's and the owner surface's bodies and the gateway's are the same), so a body with its JSON escapes fits the tool call that carries it. The ceiling is the isolate's 128 MB: a body's text (at most twice its bytes as UTF-16), its parsed copy and its serialized copy come to 80 MiB at worst. Streaming would lift it, but a tool call's arguments are parsed whole as JSON at the door, so it is not taken.
   - The door puts the bytes to R2 through `FILES`, which verifies each SHA-256.
   - A second unit adopts the upload. It makes the content, and the row names it in the same transaction. Migration 14's triggers count each reference on the content, and each reference in its writer's files scope.

   Every write stays a gate unit. Nothing about the content is committed before R2 holds it. A post checks its room and its resend before the put, so a refused or repeated post sends no byte.
3. **Crash safety.** A crash between the put and the commit leaves an upload that no row names. A placement upload expires in `PLACE_TTL_S` (10 minutes), not the 24 hours a file upload has, and housekeeping then releases it. Its chunks become garbage, and the sweep deletes them from R2 after its grace. A read of a body that a later write let go still finds it within that grace.
4. **Charged to its writer.** A placed body is charged to the files scope of the writer whose row names it:
   - the owner's own writes to the owner;
   - an outside client's to that client, so the owner's `client_share` and `owner_reserve` hold for collections and mail as they do for files;
   - a visiting owner's to a scope of its own (`client:` and a hash of the visitor), never the owner's.
5. **Quota and retention.**
   - The placed bytes count in the files byte quota (`chunk_bytes`). That quota is the tier's storage figure from the control record: free 25 GB, paid 250 GB. The owner may set it lower, never higher. The collections' and the ledger's own byte caps are gone: the tier's storage is their bound.
   - At the quota, retention makes room before anything is refused, as the files plane's own history does: the oldest placed collection history entries, then the oldest acked messages with placed bodies, of the scope that is full, one at a time.
6. **Rows counted as SQLite lays them out.** The collections' rows and history are bounded by what they take in the object, not by a count:
   - each row is charged its record and its five index entries (slice M's `collection_changes` among them), laid out as the guard lays them (`ROW_CHARGE`), its collection's seq row with it;
   - each history entry likewise (`HISTORY_CHARGE`), an erased key's last entry among them. The oldest entries are pruned first, never refused, and a pruned key goes on past its newest pruned revision, so no revision and no cursor repeats;
   - triggers keep both charges, so no write path misses one;
   - a pruned delete's or clear's entry raises `erasure_floor`, and slice M's change feed answers a reader whose cursor is below it `reset: true`, so it reads its rows again rather than keep a row it was never told is gone.

   The guard costs every term at the widest name its validator allows, and a test checks that SQLite makes no index the guard does not count.
7. **Reads.**
   - A get, a pull, a history read, a mailbox read, a mail chain, a digest (slice M) and a conflict answer the stored body through the same path. A door reads the placed bodies from R2 outside any unit, after its unit locates them.
   - A mail to one of the owner's own agents (slice ID) is bounded by the body bound alone. Its run reads the body from R2 as it starts. A run that waits for its conversation keeps only the mail's reference in its input row (the run's `mail`), and reads the mail again when its turn comes. The mail is held until the run settles, so its body stays.
   - A mail run's reply past the threshold is placed in R2 before the settle's unit, read ahead from the same batch (`Runs.answering`), and the settle's unit posts it by reference. If another batch of the run lands between the two units, the settle's unit refuses this one (`events_raced`, 503) and the runner posts it again. A crash before the settle leaves only an orphan upload, which expires and is swept; the runner's batch is posted again, and the reply with it. No reply is lost.
   - A delete's or clear's history entry (slice M) carries no body, so nothing of it is placed. Its change and private writer are counted in the history's charge with its `collection_erasures` entry, as a row's change is with its `collection_changes` entry.
   - A pull, a history read or a mailbox read returns at most `READ_PAGE_BYTES` (8 MiB) of bodies after its first item. A history read past one page answers `more` and a `before_revision` cursor.
8. **Erasure leaves no orphan.**
   - Once the erasure's mark stands, every upload and every chunk put is refused.
   - The erasure waits out the puts already in flight, and `PUT_GRACE_MS` (a day) past its mark.
   - It then deletes every chunk under the owner's namespace by prefix (`FILES.deleteNamespace`). It does so again after it wipes the database, so a put the wait missed is deleted too.
   - The grace is a day, held by the erasure's own Lifecycle job, which wakes it when the day is up, so nothing rests on an in-memory wait or a five-minute estimate: no request into the object outlives an egress stream's day. The owner stays marked, every door refusing, for that day. A tombstone keeps no namespace for a sweep after the wipe: every alarm is Lifecycle's (the alarm guard), and a wiped object has none.
   - The owner export (slice P) reads the same chunks.
9. **Not every term dominated by a body is placed.** A connection's models list (640 KiB at most) is read inside every egress admission unit, once for each sandbox model call. Placing it would put an R2 round trip on every model call. It stays in its row, bounded by the connection count, and fits the guard.

## Consequences

- The ledger, the collections and their history no longer cost the object their bytes: only their rows. Their bodies are storage, counted against the tier and the writer's share.
- With the audit's other structural changes, the worst case at the validators' widest names is 4,449,862,167.75 bytes, 44.50% of the 10 GB: 550.1 MB under half, where the raises had 2.1 MB (slice ID's agent identities and mail authority, slice M's change orders and clock, and slice W's doorbell subscriptions, included). The other changes:
  - collection rows and history counted in laid-out bytes;
  - one recipient a message;
  - the mailboxes' caps within the ceilings;
  - grant scopes counted in bytes.
- A write of a large body takes two units and an R2 put. A read of a large body takes an R2 get. A small body costs what it cost before.
- Owed live:
  - R2 read latency from an owner object;
  - the longest a `FILES` put takes in flight, against `PUT_GRACE_MS`'s day;
  - SSE-C on the placed chunks, the same probe the files plane owes;
  - an isolate's memory with a 16 MiB request body in flight (slice L's memory probe), the one ceiling for the API, the owner surface, the egress door and the gateway.
