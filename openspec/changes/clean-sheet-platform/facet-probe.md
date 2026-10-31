# The facet probe (P13)

Live probe P13 of Durable Object facets: Cloudflare's dynamic agents, child objects inside
a parent, reachable only through it and wiped by `ctx.facets.delete`. Today each agent
thread is one pi Session over its own `ThreadStore`, a top-level object in the owner
Worker, hosted by our `ThreadHost`. The design keeps facets as "switch to them if P13
shows separate limits and GA" (design-decision §6 P13; storage-research O1a). This probe
answers the open questions by measurement. Code: `scripts/probes/facetprobe/`. Workflow:
`.github/workflows/facet-probe.yml` (manual only). It follows the store probe
(`store-probe.md`) in its safety rules, cleanup and reading-key method.

## What each question decides

| # | Question | Decides |
|---|---|---|
| Q1 | Does a facet's database count against its parent's 10 GB, or does each have its own? What error, where, and what analytics reports per object | Whether a facet store can replace `ThreadStore` without one owner's threads sharing one 10 GB |
| Q2 | Do facets work under `disable_ctx_exports`? Which class source deploys and runs: `ctx.exports` (the control), a Durable Object binding to an unmigrated class, an unsafe `durable_object_class` binding, Worker Loader's `getDurableObjectClass` | Whether the owner config can keep `disable_ctx_exports` with facets |
| Q3 | What a facet's `env` and `ctx.exports` hold, and what it can reach by itself | Whether a facet is a narrower boundary than a same-script top-level object |
| Q4 | Does a facet's busy loop, a throw, CPU overrun or out-of-memory block or break its parent? | Whether one thread can stall or reset the owner object |
| Q5 | Does delete wipe the facet's database; does a later get give a fresh, empty instance; do analytics show the bytes freed? | The drop and erasure path for a facet store |
| Q6 | Parent → facet RPC p50/p95/p99 over 1,000 calls, against a top-level `ThreadStore` in the same run; are facet calls billed as requests? | The per-call cost of the transport |
| Q7 | Optional, only if Q1 and Q2 pass: can an Agents SDK root agent host a child running agents' `PiHarness` (agents 0.31.0, pi-durable 1.4.0) under a config like ours, and what is the minimum config? | Whether a facet could run a whole thread, not just its store |

## Method

Eight throwaway Workers, all named `ta-facetprobe*`, none with a route or a workers.dev
URL, deployed, driven and deleted by one workflow run:

- `ta-facetprobe` (`parent.js`, `parent.wrangler.jsonc`) has the owner Worker's runtime
  exactly: compat date `2026-10-23`, `nodejs_compat` and `disable_ctx_exports`,
  `limits.subrequests: 1000000`, and `PISTORE` → `ThreadStore` in the same script. It
  adds `SVC` (a service binding to `ta-facetprobe-dummy`), `KV` (a dummy namespace,
  `ta-facetprobe-kv`, when the token may make one) and `LOADER` (Worker Loader). `Parent`
  stands in for `OwnerDO`; `Child` (`child.js`) is the facet class, deliberately not
  migrated.
- `ta-facetprobe-exports` is the control: the same bundle without `disable_ctx_exports`,
  so `ctx.exports` is on. Q1 and Q4 run here, with `ctx.exports.Child` facets, the class
  source the Agents SDK uses.
- `ta-facetprobe-classbind` adds a Durable Object binding `CHILD_CLASS` to `Child`;
  `ta-facetprobe-unsafe` adds an unsafe binding `{ type: "durable_object_class" }`. Each is
  a Q2 class source that production may refuse at deploy.
- `ta-facetprobe-agents` and `ta-facetprobe-agents-exports` (`agents.js`) are Q7's: an
  Agents SDK `Root` with `dynamicAgents`, a `PlainChild` (the SDK's facet path alone) and a
  `PiChild` running `PiHarness` on pi-ai's faux provider, under our runtime and with
  `ctx.exports` on.
- `ta-facetprobe-door` (`door.js`) is the only way in. It holds one Workflow, `Drive`
  (`ta-facetprobe-door`), and binds each copy's root class by `script_name`. `drive.mjs`
  starts one Workflow instance per call through the Cloudflare API and reads its output;
  the step is never retried. `q4` runs inside the instance, so the parent's latency is
  measured from outside it.

`config.mjs` copies the sources into `platform/node_modules/.facetprobe/`, so wrangler
bundles them against `platform/`'s lockfile, and writes every copy's config; `child.js`
goes there a second time as `child.txt`, the text the parent hands Worker Loader, so the
loader's facet runs the same code.

| # | Copy, object | What |
|---|---|---|
| Q2 | every parent copy, `q2` | the deploy's outcome, then in one parent each class source in its own facet: `info()` and a one-row write, or the error |
| Q3 | exports and main, `q3` | a `ctx.exports` facet and a loader facet: `env` and `ctx.exports` keys, then `reach()`: a public fetch, `SVC`, `KV`, `PISTORE`, `ctx.exports.Parent` and `ctx.exports.ThreadStore` (other top-level objects), and a nested facet |
| Q5 | exports and main, `q5` | 3 rows of 1.9 MB, `info()`, `ctx.facets.delete`, the old stub called again, then a fresh `get` and `info()` |
| Q6 | exports and main, `q6` | 20 warm-up then 1,000 sequential pings each: a facet with the parent's id, a facet with its own id (control only), and a top-level `ThreadStore` through `PISTORE`. The object gets one driver call, so analytics can tell where facet calls are billed |
| Q4 | exports, `q4`, alone and before Q1 | per case: 30 baseline pings of the parent, then the facet's case started (the parent does not await it) while the door pings the parent every 100 ms, then the outcome, the parent's instance and whether the facet answers again. Cases: `yield` (10 s of CPU, yielding), `solid` (5 s with no yield, sized from `yield`'s rate), `throw`, `overcpu` (45 s with no yield, past the 30 s default), `oom` (16 MB arrays until out of memory) |
| Q1 | exports, `q1-bc` and `q1-a`, at once | alarm-driven fills of fresh random 1.9 MB rows, 8 rows (15 MB) per write and sync, 15 s per alarm. `q1-bc`: the parent itself to 6 GB, then facet `q1b` to 6 GB (b), then `q1c` to 6 GB (c, on top of b). `q1-a`: facet `q1a` alone until a limit error or 11 GB (a). A limit error ends its job and the next runs; any other error aborts the plan at once. A point is kept every 0.5 GB with the parent's size; the driver reads progress from storage only, every minute |
| Q5 at size | exports, `q1-a` | `deleteFacet` on the filled `q1a`: sizes before and after, the time it took, the parent's size |
| Q7 | both agents copies, `q7` | `PlainChild.hello()`; then `PiChild`: spawn, prompt, prompt again, list, delete, prompt the fresh one |

Workers clocks advance only across I/O, so every latency is an RPC's, in whole
milliseconds, and burns are sized by iterations.

**Order.** Q2; then Q3, Q5 and Q6; Q4 alone, since a broken facet may reset the parent
or its isolate; Q1's two fills at once, in two parents; Q5 at size; Q7 if the gate opens.
The workflow deletes everything right after the questions, before the optional
analytics step, which reads by object and namespace id.

**Local.** `node scripts/probes/facetprobe/local.mjs` needs `npm ci` in `platform/`, and
the workflow runs it before deploying. It bundles every copy with `wrangler deploy
--dry-run`, runs them in miniflare (all but the unsafe copy, whose binding type workerd
lacks), and drives every question through the door's local-only fetch, which starts the
same Workflow. Q1 there writes megabytes against a 100 MB stand-in limit; Q4 skips `oom`
and shortens the burns. It also checks the configs against the owner's, the live
transport and `analytics.mjs` on stand-in APIs, and the reading key on synthetic plans.

What workerd showed locally (a harness check, never a reading):
- Q2: under `disable_ctx_exports` only Worker Loader's class works; the raw class, a
  namespace and a class binding are refused as "not of type DurableObjectClass or
  LoopbackDurableObjectNamespace or LoopbackColoLocalActorNamespace".
- Q3: a `ctx.exports` facet gets the parent's whole `env` and a `ctx.exports` that reaches
  any top-level object of the script; a loader facet gets an empty `env` but the network.
- Q4: a facet's no-yield burn holds up the parent's pings for the burn's length.
- Q5: delete leaves a fresh, empty database; the old stub throws "Facet was deleted."
- Q7: the SDK needs `ctx.exports` ("subAgent() is not supported in this runtime");
  with it a plain child works, and the `PiHarness` child fails "Facets currently cannot
  set alarms."

**Live only.** Every Q1 reading (workerd has no 10 GB limit); Q2's production deploys and
runtime answers; Q4 (no CPU or memory limits locally, no placement); Q6's latencies and
billing; the analytics.

## Reading key

`drive.mjs` holds this key as code (`q1Reading`, `readings`, `q7Gate`); `local.mjs` checks
it on synthetic plans.

**Q1.**
- **separate**: the parent and its facets held more than 10 GB together (b, or b and c)
  with no limit error. A facet's database does not count against its parent's 10 GB.
- **shared**: `q1b` hit a limit error while the parent and `q1b` together were at about
  10 GB. One budget for the parent and all its facets.
- **void**: a non-limit error stopped the fill; the error is the reading.
- **unproven**: neither, by the deadline (`fill_minutes`); the sizes reached are reported.
- (a) separately: a lone facet stopping with a limit error near 10 GB means each facet
  database has its own 10 GB; passing 11 GB means no such limit on a facet.
- A limit error is one whose text matches `LIMIT` in `parent.js` (SQLITE_FULL, "database
  or disk is full", a size or storage limit). Each error's text and every database's size
  at that moment are recorded.

**Q2.** Per copy: the deploy (`accepted`, or `refused` with wrangler's error), then each
source: `works` or its error. "Under `disable_ctx_exports`" lists what works on main,
classbind and unsafe.

**Q3.** The facet's `env` keys against the parent's; each reach attempt `reached` or its
error. A facet that reaches `ctx.exports.Parent` or `ThreadStore` can reach other
top-level objects without its parent.

**Q4.** Per case: the parent is **blocked** when one ping waited at least half the burn
(`solid`, `overcpu`), or more than a second over its baseline (the others); **reset**
when its instance changed. The outcome is what the facet's call returned or threw;
"never settled" means it was lost with the parent's memory.

**Q5.** Wiped: no rows and a smaller database after the delete, and a fresh instance.
The parent's own size before and after shows whether a facet's bytes were ever counted
in it.

**Q6.** The three series' p50/p95/p99. Billing (analytics): requests on the `q6` parent's
id well over its one driver call, or on the own-id facet's id, mean facet calls are billed
as requests; the `ThreadStore` series is the store probe's known-billed control.

**Q7.** The gate opens only when Q1 reads separate and Q2 found a working source. Then,
per copy: the plain child's and the `PiHarness` child's steps, `ok` or the error. The
copy that answers is the minimum config.

## Running it

Its trigger is `workflow_dispatch` only. **The founder approves each run**, because it
deploys to the production Cloudflare account. The inputs are:
- `confirm`: must be `facet-probe`.
- `fill_minutes`: 10 to 150, default 100. Q1's fill stops there.

It uses the repo secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. The token
needs:
- **Account → Workers Scripts: Edit**: upload, DO class migrations, the Workflow and its
  instances, delete, and the Worker, namespace and Workflow lists. The account step reads
  the Workflows API first and stops the run before any deploy if it is refused.
- Account → Workers KV Storage: Edit (optional): the dummy KV namespace. Without it the
  copies deploy with no `KV` binding and the summary says so.
- Account → Billing: Read (optional): names the plan.
- Account → Account Analytics: Read (optional): Q6's billing, and every Durable Object
  storage dataset the schema lists (found by introspection), kept to the run's own objects
  and namespaces; account-wide rows are reported only as their change over the window.

**Cost.** Q1 writes about 15,300 rows, 29 GB at the peak, for at most a few hours: at
$0.20 per GB-month prorated and $1.00 per million rows written, under $0.05. Requests,
CPU and the one Dynamic Worker are within the plan's included usage. Negligible.

**Time.** About 30 minutes plus Q1's fill, which stops at `fill_minutes`: at most about
two and a half hours.

Safety, as the store probe:
- No route, zone or DNS record, and no workers.dev URL at all: nothing on the internet
  reaches a probe object.
- No owner data. No model credential.
- One run at a time (concurrency group `facet-probe`).
- `delete.sh` deletes the Workflow (and its instances), force-deletes every
  `ta-facetprobe*` Worker (so their namespaces and every object's storage, facets
  included, go too) and the dummy KV namespace, before the deploy and again at the end
  whatever happened. It refuses any name outside `ta-facetprobe*`, then reads the
  account's Worker, namespace and Workflow lists back until nothing named
  `ta-facetprobe*` is left, and fails the run if anything remains or a list cannot be read.
- After a run that failed or was cancelled, run it again (its first step deletes
  everything before deploying), or run `delete.sh` by hand with the same token and the
  script names from the workflow's `SCRIPTS`.

The differences from the store probe:
- no workers.dev door: the door is a Workflow started through the API;
- a typed `confirm` input;
- eight scripts, a Workflow and a KV namespace to delete, and a whole-account list check
  by name prefix;
- a refused copy is a recorded Q2 reading; the run goes on with what deployed.

## Results

| Run | Date | Plan | Commit | Fill minutes | Q1 | Q2 under ours | Q4 | Q7 |
|---|---|---|---|---|---|---|---|---|

Not run yet.
