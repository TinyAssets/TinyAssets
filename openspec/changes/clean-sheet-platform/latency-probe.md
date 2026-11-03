# The latency probe (slice STORE)

This is the live probe that decides where the owner's item index lives. It measures two options from inside an owner-shaped Durable Object:
- **Option A:** index shards, a call to a second named object;
- **Option B:** R2 trees, R2 get, put and list.

Both cross-reviews in the STORE research named this measurement as the one that decides it (`/home/user/ta-work/store_research.md`, "Cross-review"; the independent review's "what would change the recommendation").

- **Code:** `scripts/probes/latencyprobe/`.
- **Workflow:** `.github/workflows/latency-probe.yml`. It is manual, or a push to `probe-latency-run`, which exists only to start a run the founder approved.

It follows the store probe (`store-probe.md`) and the facet probe (`facet-probe.md`) in its safety rules, its door and key, and its cleanup and proof.

## Found

_Results pending: the run is recorded at the end of this file._

## What it decides

| # | Question | Decides |
|---|---|---|
| A | From an owner-shaped object, a call to a second named object (same-script binding, made on demand by that call) that runs a small SQLite write and read: p50, p95 and p99, warm and cold, sequential and 6-parallel. Where the second object is placed relative to the first | The cost per write of index shards: the journal's one cross-object call |
| B | From the same object, through the files Worker's service hop as the owner reaches R2 (and direct, as a lower bound): a create-only put (`If-None-Match: *`), a put over an existing key, a get cold (first read) and warm, and a list. Sizes of 1 KiB, 64 KiB and 1 MiB; sequential and 6-parallel | The cost of R2 trees: a flush's put rounds, a cold path's sequential gets, a listing |

## Reading key

Both reviews set the same threshold: an R2 round trip of about 50 ms at p50.

- **At or below it:** R2 trees with a write buffer in the object (the research's amended D) hold up.
  - A cold path lookup is about 5.6 sequential gets on the measured node_modules tree.
  - A 30-file flush is about 19 rounds of 6 parallel puts.
  - A single edit commits into the object, as today.
- **Far above it:** favour index shards (A), or B with an index cache that lives in the object.

Read from these rows:
- B's get, cold, at 1 KiB and 64 KiB through `FILES`: the node sizes;
- B's put at the same sizes, 6-parallel: a flush's rounds;
- A's warm write and read: the journal's call.

The probe reports numbers. The decision itself goes to the founder, with these derived figures:
- **a cold path lookup:** 5.6 × get cold;
- **a 30-file flush:** 19 × the 6-parallel put round;
- **a single edit:** A's one call, against B's buffered unit.

## Method

Three throwaway Workers and one throwaway bucket, made, driven and deleted by one workflow run:

- **`ta-latencyprobe`** (`owner.js`): the owner-shaped Worker.
  - **Runtime:** the owner Worker's exactly, read from `platform/owner/wrangler.jsonc` by `config.mjs`: the compatibility date, `nodejs_compat`, `disable_ctx_exports` and `limits.subrequests`. No workers.dev URL.
  - **Classes:** two SQLite classes. `Owner` runs and times every case. `Shard` is bound by the one same-script binding `SHARD` with no `script_name`; store probe P3 found production takes this.
  - **R2:** reached the owner's real way, through the `FILES` service binding to the files Worker. It also binds the same bucket directly as `BUCKET`, for the lower bound and the cleanup.
- **`ta-latencyprobe-files`** (`files.js`): the files Worker's stand-in, holding the bucket. Its RPC methods are the bucket calls and nothing more.
- **`ta-latencyprobe-door`** (`door.js`): the only workers.dev URL, guarded by a per-run key. It binds `Owner` by `script_name`, as the doors bind `OwnerDO`.
- **The bucket:** `ta-latencyprobe-<run id>`, made through the R2 API with no location hint. Its location is recorded. A token that cannot make it skips option B, and the summary says so.

**Timing.**
- Each case is one request to one `Owner` object (`<run>-owner`).
- It runs `n` operations (300 by default; 100 for cold shards, each a new object), timed one by one with `Date.now()` around the awaited call.
- Workers clocks advance only across I/O, so each sample is that I/O, in whole milliseconds.
- "6-parallel" runs batches of 6 at once (the outgoing-connection limit), each operation timed on its own.

**Placement.**
- `where` reads Cloudflare's trace page from inside the owner object, the files Worker, and four fresh shards the owner makes. It reads `request.cf.colo` at the door.
- A shard is made where the owner's first call to it lands, as it would be live.

**The cases** (in order; each read and list follows the put that wrote its keys):
1. **A:**
   - ping (a call doing nothing);
   - a write and read on a warm shard, sequential and 6-parallel;
   - the first call to a new shard (made by that call), sequential and 6-parallel.
2. **B, through `FILES`,** for 1 KiB, 64 KiB and 1 MiB, each sequential and 6-parallel:
   - a create-only put of fresh keys;
   - a cold get (the first read of each key);
   - a warm get (one key read again and again).
3. **B, also through `FILES`:**
   - a 1 KiB put over existing keys, where the precondition fails and nothing is written;
   - a list of 1,000 and of 100, sequential;
   - a list of 1,000, 6-parallel.
4. **B direct (no `FILES` hop), 1 KiB:** put, get cold, get warm and list, the lower bound.

**Cleanup**, whatever happened:
1. The driver empties the run's prefix through the owner's direct binding, and checks the bucket holds nothing.
2. `delete.sh` force-deletes each Worker (its Durable Object namespaces go with it), then deletes every bucket named `ta-latencyprobe-*`.
3. It then reads back the Worker, Durable Object namespace and R2 bucket lists, retried for a minute, and fails unless none named `ta-latencyprobe*` is left.

**Cost:** about 3,300 Class A operations ($0.015) and 4,200 Class B ($0.002). Under 0.7 GB is stored, for minutes. A few thousand Durable Object requests and rows. About two cents.

**Local** (`node scripts/probes/latencyprobe/local.mjs`, after `npm ci` in `platform/`; the workflow runs it before deploying):
- it checks the configs against the owner's runtime;
- wrangler bundles each Worker with `--dry-run`;
- it runs the bundles in miniflare and drives every case with `n` of 12;
- it checks the door's key, the percentile function and the cleanup.

It proves the harness, never a reading.

## Results
