# The latency probe (slice STORE)

This is the live probe that decides where the owner's item index lives. It measures two options from inside an owner-shaped Durable Object:
- **Option A:** index shards, a call to a second named object;
- **Option B:** R2 trees, R2 get, put and list.

Both cross-reviews in the STORE research named this measurement as the one that decides it (the STORE research's cross-review, and the independent review's "what would change the recommendation").

- **Code:** `scripts/probes/latencyprobe/`.
- **Workflow:** `.github/workflows/latency-probe.yml`. It is manual, or a push to `probe-latency-run`, which exists only to start a run the founder approved. Once option B has run, the workflow, the probe's scripts and that branch are removed.

It follows the store probe (`store-probe.md`) and the facet probe (`facet-probe.md`) in its safety rules, its door and key, and its cleanup and proof.

## Found

Run 2 (41640819262, 2026-11-03, results at the end) measured **option A only**.
- The repository's `CLOUDFLARE_API_TOKEN` has no R2 access: the bucket create and the bucket list both answered 403 "Authentication error". So no bucket was made, and option B was not run.
- **Option B needs a token with Account → Workers R2 Storage: Edit**, then one more run.

Run 1 (41640309636) deployed nothing. Its cleanup's proof read that 403 as an unreadable list. Fixed in `delete.sh`: a token that cannot list buckets made none.

- **Placement.** The door was entered at ORD and the owner object ran at ORD. All four shards the owner made by its first call to them also ran at ORD: a shard is placed in the owner's colo.
- **A call doing nothing (ping), sequential:** p50 5 ms, p95 8, p99 10 (n 300).
- **A small SQLite write and read on a warm shard:**
  - sequential: p50 30 ms, p95 38, p99 42 (n 300);
  - 6-parallel: p50 35, p95 46, p99 55 (n 300; 300 calls in 1.9 s).
- **The first call to a new shard** (it makes the object):
  - sequential: p50 285 ms, p95 452, p99 3,768 (n 100; max 4.9 s);
  - 6-parallel: p50 277, p95 349, p99 445 (n 100).
- **Reading.** A shard's write costs about 25 ms more than a ping. That is the shard's own durable commit: its answer waits for the write to be confirmed before it leaves.
  - A journaled write that touches the owner object and one shard therefore pays the shard's commit (about 30 ms at p50) on top of the owner's own units.
  - A new shard costs about 0.3 s on its first write, with a tail of seconds.

Against the reading key: option A's per-write cost is now known (about 30 ms p50, 40 to 55 ms p95). Option B's is still owed. The index decision waits on run 3, with R2.

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

### Run 2 (41640819262, 2026-11-03), option A only

- Commit `c44406706`, started by a push to `probe-latency-run`, which the founder approved.
- Workers deployed at 13:03:05Z. Cases from 13:03:21 to 13:04:18Z. Cleanup at 13:04:22Z.
- **Bucket:** not made. The R2 API answered 403 "Authentication error" (the token has no R2 access). Option B was not run.
- **Placement:** the door entered at ORD; the owner object at ORD (US); four shards made by the owner, all at ORD.

| Case | n | p50 ms | p95 ms | p99 ms | min | max | mean | failed | wall s |
|---|---|---|---|---|---|---|---|---|---|
| A ping, sequential | 300 | 5 | 8 | 10 | 2 | 16 | 5.2 | 0 | 1.6 |
| A write+read, warm, sequential | 300 | 30 | 38 | 42 | 26 | 52 | 30.9 | 0 | 9.3 |
| A write+read, warm, 6-parallel | 300 | 35 | 46 | 55 | 29 | 55 | 36 | 0 | 1.9 |
| A write+read, cold (new shard), sequential | 100 | 285 | 452 | 3768 | 208 | 4905 | 376.4 | 0 | 37.6 |
| A write+read, cold (new shard), 6-parallel | 100 | 277 | 349 | 445 | 205 | 463 | 287.1 | 0 | 5.8 |

**Cleanup**, from the step log:
- `ta-latencyprobe-door`, `ta-latencyprobe` and `ta-latencyprobe-files` each answered "delete HTTP 200", then 404 on a read back.
- The Worker and Durable Object namespace lists then held nothing named `ta-latencyprobe*`.
- R2 was not checked, since the token cannot reach it and so made no bucket.
