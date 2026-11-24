# models-json-max: a connection's models are kept one row a model (2026-11-24)

`fix/models-json-max`. Found by #5848's review: `MODELS_JSON_MAX`, the bytes a connection's
models may take as one JSON value, is at 99.2% of its stated reason, 1,024 of the largest
built-in record (650,241 of 655,360 bytes), since #5844 made a connection's models follow the
gateway's catalog (`listing-records.md`). Directions 1 and 4 (every connection follows the
current catalog), Direction 5 (the best shape, not a band-aid), Fact 15 (each limit a recorded
technical ceiling with its reason).

## Research (Fact 14), before the design

### What the 99.2% is, and what a connection holds today

Measured at main `acb256306` (pi-ai 1.6.0, each record as `gateway/src/catalog.ts` `recordOf`
makes it, naming its API):

- **The stored load is not near the bound.** The largest catalog a connection reads is
  OpenRouter's on both its APIs: 417 records (397 `openai-completions`, 20
  `anthropic-messages`), 170,203 bytes as records and 173,122 as the lane's record and score
  pairs (`connection_models`, migration 34): 26% of the 640 KiB. Its records are 205 to 603
  bytes, 440 the median. The next catalogs are Vercel's AI Gateway (305, 108,627 bytes) and
  Bedrock (208).
- **The 99.2% is the bound's stated reason, a product:** `maxModels` (1,024) times the largest
  record any built-in catalog has (624 bytes, Google's, plus separators and the five-byte ids
  the test pads with: 650,241). The connections test holds that product within the bound, so
  a pi-ai bump whose largest record grows by about five bytes fails it, and the only fix the
  JSON shape has is a migration that widens a frozen check.
- **What fails at run time, and when:** a read is refused whole past `maxModels` records, or
  past 640 KiB of pairs (`checkedModels`, `servedJson`); a commit past either is refused. At
  today's records, `maxModels` binds first (1,024 of the largest is 617 KB). OpenRouter's
  catalog went from 401 to 417 records in the month from pi-ai 1.4.0 to 1.6.0, and its
  listing served 477 models on 2026-11-23 (`served-models.md`), 63 of them not an agent's. So
  neither bound is reached soon: the review's "the next read fails" holds for the product the
  test checks, not for the catalog as it stands.

### What bounds a connection's models

- **No platform limit is near 640 KiB.** A Durable Object's SQLite string, BLOB or row is at
  most 2 MB (Cloudflare's Durable Objects limits, as `limits-audit.md` records them, read
  2026-11-03); the change log takes a row image up to `IMAGE_MAX` (1.5 MiB), derived from the
  widest exported row, `connections` with its models at 640 KiB among them. A service binding's
  RPC carries up to 32 MiB (`contract/src/limits.ts` `BATCH_BYTES`); the gateway's `models`
  answer and the commit cross it. The developer docs could not be read again from this
  session (the host is outside its network).
- **The 640 KiB is a design choice**, made at review L5-1 to count a connection's models in
  bytes in the owner's 10 GB (`limits-audit.md`): 50 connections at 640 KiB each, twice over
  (`connections.models`, exported, and `connection_models`, not), 101 MB of the owner's worst
  case.
- **One JSON value cannot give both its bounds ample headroom.** A count at twice the largest
  catalog and a record at twice the largest record is 1,024 × 1,248 bytes, 1.25 MiB; at three
  times each, 2.6 MiB, past a row's 2 MB. A bound on the value's bytes is always a bound on
  the product, so it is either near a record's growth (today) or near the row.

### Shapes considered

- **Widen `MODELS_JSON_MAX`.** A new migration rebuilding a frozen check, again at the next
  bump that grows a record; capped by the row's 2 MB and by `IMAGE_MAX`, which the exported
  `connections.models` sets. Moves the cliff.
- **Store only the fields the owner reads.** It reads all of them: the id, the API (a run's
  base), the name, the limits and thinking map (a run's levels and compaction), the prices
  with their tiers (the reserve, `money.ts`). Nothing to drop.
- **Reference the gateway's catalog by version.** The owner holds no catalog by design
  (#5372: its memory baseline, and a second copy held equal only by the lockfile); every
  reserve and `auto` pick would then call the gateway, or cache the catalog in the isolate,
  which is the same copy. Rejected for the reasons `listing-records.md` gives.
- **One row a model** (as the collections, the mail and the file index keep one row an item):
  each record its own row, keyed by connection and model, bounded by itself; the count by
  `maxModels`, with its reason; the owner's storage by how many records it keeps in all.
  Chosen.

### What one row a model costs and needs

- **A record's bound is the record's.** `checkedRecord` already bounds every field (an id and
  a name of 200, 16 price tiers, seven thinking levels), so a record's own widest is about
  5 KB. 2 KiB, over three times the largest built-in record, is the bound: the connections
  test holds every built-in record within half of it at each pin, and a record past it is
  refused like any record the checker refuses.
- **The storage is the owner's total, not the product.** At `maxConnections` (50) times
  `maxModels`, 51,200 rows, each a page at the guard's worst fill, would take about 290 MB of
  the 364.9 MB left under half, which the slices still to land share (`limits-audit.md`). So
  the owner keeps at most `maxModelsKept`, 8,192 records across its connections: eight
  connections at `maxModels`, nineteen of the largest catalog. The term is then 46.1 MB,
  13.1 MB past the 33.1 MB of migration 34's table it replaces (the guard's worst case 46.48%
  of the 10 GB, 351.7 MB under half). Its reason is the owner's, so it
  is checked where a connection's records change: a commit past it is refused, a read past it
  fails and keeps the records as read.
- **The commit's records, exported** (`connections.models`): only seconds of use (until the
  lane's first read) and none after a restore, which marks every connection failed and keeps
  no blob (`secretsLost`). They go to the same rows; the column stays, as its rebuild is an
  exported table's, whose generations would then need a form to come forward (`FORWARD`):
  left below.
- **The migration** touches no exported table, so it brings every generation forward
  (`FORWARD` null) and names no `connections`; so it copies none of migration 34's pairs, and
  each active connection's row is due at once, as 33 and 34 did.

## Design

1. **`connection_records` holds one row a model** (migration 35, replacing 34's
   `connection_models`, one JSON value a connection): the connection, the model's id, its
   record (at most `MODEL_RECORD_MAX`, 2 KiB) and its score or null, keyed by connection and
   model, so a connection's models list by id. `connection_model_reads` holds what 34's row
   held besides: when the lane reads the connection's models next. Neither is exported, as
   34's was not.
2. **The commit keeps its records as those rows**, score null, and no longer writes
   `connections.models`. The lane's reads replace them. `modelsOf` reads the rows for every
   reader; a connection committed before migration 35 has none until its first read, seconds
   after the deploy, and is read by the records its row kept, as 34's own deploy did.
3. **Three bounds, each its own reason:** a record's bytes (`MODEL_RECORD_MAX`: three times the
   largest built-in record), a connection's count (`maxModels`: twice the largest built-in
   catalog on all its APIs), the owner's count (`maxModelsKept`: its storage). The test that
   held `maxModels` of the largest record within one value's bytes holds each instead.
4. **`MODELS_JSON_MAX` stays the frozen bound of `connections.models`** alone, which no code
   writes now; the owner-bound guard and `IMAGE_MAX` keep counting it at its check.

## Problems found (untangle step 2), with the fix above each

- **One row a model, each bound its own.**
  - `MODELS_JSON_MAX` bounded the product of a count and a record's size, so a record's growth
    failed it while the catalog sat at a quarter of it (`connections.test.ts` "the largest
    built-in catalog").
  - `checkedModels` and `servedJson` each held one JSON value's bytes, the commit's and the
    read's: two copies of the bound, the read's over pairs the commit never saw.
  - The read's pairs were the commit's records plus a score, a second JSON shape of one list.
  - `#served` compared JSON text to learn whether a read changed anything; a kept read now
    marks the connections changed, once a day each.
- **The scan counted a catalog by one API**, though a connection is sealed for every API of its
  source (#5837): OpenRouter's 417 were counted as 397. It now counts a provider's records on
  every API it is read on.
- **A revoke and a restore's fence each listed the tables a connection's reads keep**: both
  call `#dropReads` now, which deletes the models' rows with them.
- **Comments copied numbers a test holds** (`limits.ts`, `connections.ts`: "1,024 of the
  largest built-in record"): each now names the test.
- Kept, the system needs them:
  - A read that leaves no model is a failed read (`listing-records.md`), and so is one past
    `maxModels`, `maxModelsKept` or a record's bound: the records as read are kept, read again
    hourly, never an empty or half connection (Fact 8).

## Left, with reasons

- **`connections.models` stays**, written by no commit since migration 35, and read only for a
  connection committed before it, until its first read, or after its revoke (as today, the
  records it was committed with). Dropping the column rebuilds an exported table with its
  capture triggers, and every generation from before it would need a form to come forward
  (`FORWARD`), which declared refused strands owners' kept generations (the founder's call).
  Owed below.
- **A revoked or failed connection made after migration 35 lists no models**: its rows go with
  its reads at the revoke (and at a restore's fence), as its credential does. Before, it listed
  the records it was committed with, which no call can use.
- **A connection's models list by id**, the rows' key, not in the gateway's order (which
  groups a catalog by API). Every reader but the owner's view reads them as a set (`auto`
  sorts them whole, the egress door finds one by id), so only the view's order changes.
- **Every reader still reads all of a connection's records** (`egressOf` on each sandbox call
  prices a router by every model it may route to). Reading one by id for a call that names no
  router is a narrower query, not this PR's bound.

## Owed

- [ ] untangle/connections-models-column: drop `connections.models` and its frozen
  `MODELS_JSON_MAX` once every owner's connections committed before migration 35 are read or
  reconnected, with the `FORWARD` form its exported rebuild needs (or at the cutover's clean
  start, if no owner keeps a generation by then).
