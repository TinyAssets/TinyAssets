# The store probe (slice 0c, batch 1)

Live probes P1, P2, P3, P4 and P7 of the storage design (a′): one pi Session per thread,
its bytes in a per-thread `ThreadStore` Durable Object in the owner Worker's own script.
Code: `scripts/probes/storeprobe/`. Workflow: `.github/workflows/store-probe.yml` (manual
only). It follows the cap probe (`cap-probe.md`): the same safety rules, cleanup and
reading-key method.

## Found

Run 1 (2026-10-30, results at the end) answered P1, P2, P3, P4 and P7. What it set for
S1-S3: `limits.subrequests` 1,000,000; `OPEN_MAX` 64; `contextRetentionMs` and
`documentRetentionMs` kept at 60 s; one `RpcTarget` session per open Session, released
when the thread host closes it. No pre-warm: a new thread's store opens about 0.3 s cold,
once.

Still owed, live: **P8**, `deleteAll()` on a populated store frees its billed bytes within
a billing day (design-decision §6), which every drop's wipe and the owner's erasure
(S4-S5) rely on; and **P5**, a store reset mid-run.

- **P3.** Production accepts the `PISTORE` → `ThreadStore` binding with no `script_name`
  under `disable_ctx_exports`, and it reaches only `ThreadStore` (`ctx.exports` is
  `undefined` in both classes; the store's `env` is `["PISTORE"]`). Co-location stands.
- **P1.** Store RPCs are counted, on a separate per-invocation counter from fetch
  subrequests ("Too many API requests by single Worker invocation"):
  - P1c main, pinned at 10,000, stopped 10,100 store calls in one alarm at exactly 10,000.
    The control stopped fetches at 100 on the low copy, where 150 store calls all passed,
    so the two counters differ.
  - Raised: Paid accepts `limits.subrequests: 1,000,000`, and it lifts the store-call
    count too (10,100 completed).
  - The P1 runs, at 10,000, passed: 32 threads made 32,697 store calls in 10 minutes with
    no error, so the starting alarm does not carry every call; the count resets as the
    work moves across events. About 1.7 store calls per thread per second while
    streaming (1 s partial commits); the largest 30 s window was 1,886 calls at 32
    threads; 1 thread made 1,032 calls in 10 minutes, 8 threads 8,209.
  - P1a and P1b are void: they ran on the low copy, whose 100 governs fetches, not store
    calls.
- **P2** (door, owner and stores all in one colo). Owner → store ping, n = 1000: p50 2 ms,
  p95 3 ms, p99 4 ms per-call stub; p50 1 ms, p95 2 ms, p99 3 ms over a session. Commit
  while 4 threads stream: p50 24 ms, p95 57 ms, p99 177 ms. A new thread's first message:
  566 ms in all (store open 294 ms cold, `Harness.open` 19 ms, `root` 60 ms, first admit
  47 ms, first settle 177 ms). Store open: 54 ms on a warm reopen, 599 ms after a reset.
- **P4.** 128 Sessions held, each about 316K characters of context (a store of about
  446 KB), with no break: `exceededMemoryErrors` and `exceededCpuErrors` 0. Open p50
  138-151 ms and turn p50 269-281 ms, flat from 8 to 128. Every checkpoint came within a
  minute, so none is void. Workers reads no memory, so 128 is a lower bound, not a break.
- **P7.** 200 per-call stub calls billed 202 store requests; 200 calls over one session
  billed 2. The held session kept its store in memory (180 s active); the unheld store
  was evicted. A session is about 100 times fewer billed requests while a thread is
  active, and pins its store's duration until released.

## What each probe decides

| # | Question | Decides |
|---|---|---|
| P3 | Do wrangler and production take a same-script namespace binding (`PISTORE` → `ThreadStore`, no `script_name`) under `disable_ctx_exports`? Does that binding reach only `ThreadStore`? | Co-locating `ThreadStore` in the owner Worker. If refused, the fallback is a separate store Worker with a pi-version handshake |
| P2 | Owner → `ThreadStore` RPC latency (p50/p95/p99); the store's colo against the owner's; a new thread's first-message latency | Per-commit overhead; whether new threads need a pre-warm |
| P1 | Do store RPCs count as the owner's subrequests? How many does a run spend at 1, 8 and 32 streaming threads? Which event is charged: the alarm that started the work, or each heartbeat? Does Paid accept a raised `limits.subrequests`? | The owner's `limits.subrequests`; whether `OPEN_MAX` is bounded by subrequests |
| P4 | Memory of N open pi Sessions with realistic contexts in one isolate; where it breaks against 128 MB; cold-open latency | `OPEN_MAX`, idle close, `contextRetentionMs` |
| P7 | Per-call store requests against one `RpcTarget` session from `open()`; whether a held session keeps the store in memory | The RPC shape |

## Method

Four throwaway Workers, deployed, driven and deleted by one workflow run:

- `ta-storeprobe` (`owner.js`, `owner.wrangler.jsonc`) stands in for the owner Worker. It
  has the owner's runtime exactly:
  - compat date `2026-10-23`, `nodejs_compat` and `disable_ctx_exports`;
  - no workers.dev URL and no route;
  - `limits.subrequests: 10000`.

  It holds two SQLite classes:
  - `Owner`, the stand-in for `OwnerDO`;
  - `ThreadStore`, bound by the script's one namespace binding, `PISTORE`, with no
    `script_name`.

  Each `ThreadStore` runs pi-durable 1.4.0's own `SqliteStorage`
  (`openDurableObjectSqliteStorage`, prefix `pi_`) behind a holder fence. It serves pi's
  `Storage` methods over RPC, and returns a request error tagged as
  `{requestError}`. On the owner side, `RemoteStorage` is a thin proxy:
  - `mintId` is local, seeded by `open`;
  - a request error is thrown again as `StorageRequestError`;
  - an aborted context rejects.

  Real pi `Harness` Sessions open over it, on pi-ai's faux provider, with the owner's
  progress settings (partial commits every second). No model credential exists anywhere.
- `ta-storeprobe-low` and `ta-storeprobe-raised` are the same bundle and config with
  `limits.subrequests` set to 100 (where P1's attribution cases discriminate) and to the
  `raised_limit` input (default 1,000,000).
- `ta-storeprobe-door` (`door.js`) is the only workers.dev URL. It binds each copy's
  `Owner` class by `script_name`, as the doors bind `OwnerDO`. Every request needs the
  per-run `x-probe-key`, and every route but `/health` needs the run id. It forwards a
  short list of `Owner` methods and adds `request.cf.colo`.

`config.mjs` copies `owner.js` into `platform/node_modules/.storeprobe/`, so wrangler
bundles it against `platform/`'s lockfile (pi-durable, pi-ai and chord at the owner's
pins). It also writes the three owner configs and the door config.

Each case runs in its own `Owner` object. Every record carries the run id and the object's
id, and the driver refuses records from another run. A measured case (P1b, P1c, the P1
runs) is never read while it should still be running. A read is an incoming event, and
which event a call is charged to is what P1 measures. The object logs any read that
reaches it while it runs, and the key voids that reading.

| # | Copy | Where | What |
|---|---|---|---|
| P3 | main | the deploy, then one DO RPC | the deploy's outcome. Then, through `PISTORE`: `whoami()` on a store; `idFromString(<the owner's own id>)`; `getByName(<the owner's own name>)`; and the owner-only methods `ownerOnly()` and `read()` on a store stub. Both classes' `ctx.exports` and `env` names |
| P1 control | low | one DO RPC | 150 sequential `fetch` calls to the trace page. A fetch is a known subrequest, so this shows whether the low copy's 100 applied at all |
| P1a | low | one DO RPC | 150 × `store.ping()` (stub), or `openSession()` then 150 × `session.ping()` (session) |
| P1b | low | an arming RPC, a start alarm, heartbeat alarms every 20 s | arm spends j = 8. The start alarm spends k = 4, then starts a loop it does not await. Phase 1: 45 calls per window for 4 windows (180). Phase 2: wait for the next heartbeat, then a burst of 150. Each heartbeat alarm spends h = 4 |
| P1c | main, raised | one alarm | 10,100 × `store.ping()` in a row |
| P1 runs | main | a start alarm, then heartbeat alarms every 30 s (PiHarness's wake shape) | 1, 8 or 32 Sessions on fresh stores. Each answers faux inputs one after another until `run_minutes` (default 10) has passed. Each answer streams about 30 s at 20 tokens a second, with partial commits every second. Then every conversation is aborted and closed. Every store call is counted and timed, by method. Each heartbeat records the running total. A Session that fails stops its thread |
| P2 | main | one alarm, run alone | trace colos of the owner and of each new store, plus the door's `request.cf.colo`. 1,000 pings over a stub and over a session. 8 new threads timed step by step: `storeOpen` (cold start + pi migration + seed), `Harness.open`, `root`, `resume` (synchronous), first admission, settle. Each step's store calls are counted. The same store reopened warm, and after `reset()` (`ctx.abort()`: a fresh instance with no migration). 4 threads streaming 20 s for commit latency |
| P4 | main | seeding RPCs, then one alarm, run alone | seeding, 8 stores per request: each gets 6 real pi turns of 50 KB answers (about 300 KB of transcript), then closes. The host alarm then opens a Session on each seeded store in turn and keeps every one open, each after one short turn, so pi holds its context (`contextRetentionMs`, 10 min). At 8, 32, 64, 128 (192, 256) it records the count, the cold-open and first-turn latencies and the runtime's memory report. A reset shows as the alarm's retry, which records how many were open and runs nothing again. One faux provider serves every Session of a case, its queue sized to the answers, so the memory per Session is pi's |
| P7 | main | one alarm | 200 calls on a per-call stub store and 200 on one `RpcTarget` session. Then 180 s idle, with the session held and the other store unheld. Then each store says whether its instance survived. `analytics.mjs` reads both stores' requests and active time from the GraphQL Analytics API |

Workers clocks advance only across I/O, so a local SQLite commit reads 0 ms. The remote
commit time is therefore the per-commit overhead. Per-call times are whole milliseconds;
each ping series also has a batch mean.

**Order.** P3, the control and P1a run first. P2 runs alone, before anything else loads
the isolate. P7, P1b and P1c run at once, in separate objects. The driver waits for P1c at P2's measured
ping pace. P4 runs alone, since it fills an isolate. Then the P1 runs, one after another.
A default run takes about 50 minutes. The workflow deletes every Worker right after the
cases, before the optional analytics step, which reads by object id and needs no Worker.

**Local.** `node scripts/probes/storeprobe/local.mjs` needs `npm ci` in `platform/`, and
the workflow runs it before deploying. It:
- bundles every copy with `wrangler deploy --dry-run`, which also validates the
  same-script binding under `disable_ctx_exports`;
- runs the bundles in miniflare;
- drives every case twice with short waits;
- checks the reading key, the configs against the owner's, and `analytics.mjs` on a
  stand-in API.

The second run has one case refused with a 1101, and must refuse its first-run records as
stale.

**Live only.** The local results never stand in for these:
- every P1 reading: workerd enforces no subrequest limit, so locally every call passes, the
  control does not stop, and every low-copy "uncounted" reads void;
- P7: nothing is evicted locally, so the reading is inconclusive, and there is no analytics;
- P4's checkpoints and break: there is no 128 MB isolate, and `process.memoryUsage()`
  reports zeros;
- P2's colos and latencies;
- P3's production deploy and its namespace checks.

Locally the harness proves the drive, the bundle, pi over `RemoteStorage`, and wrangler's
acceptance of the config.

## Reading key

`drive.mjs` holds this key as code (`p1aPredictions`, `p1bPredictions`, `p1cPredictions`,
`raisedReading`, `p4Cause` and `readings`). `local.mjs` asserts that P1a and P1b separate every rule
(`discrimination`), and checks the readings on synthetic records.

**P3.**
- The deploy step records `accepted` or `refused`, with wrangler's error. A refusal
  answers the question: production does not take the binding.
- "Reaches only `ThreadStore`" is true only when all of these hold:
  - the store answers as `ThreadStore`;
  - the owner's own name through `PISTORE` reaches a `ThreadStore` with a different id;
  - the owner's own id through `PISTORE` is refused, or reaches a `ThreadStore`;
  - `ownerOnly()` and `read()` on a store stub are refused;
  - nothing answers as `Owner`;
  - `ctx.exports` is `undefined` in both classes (`disable_ctx_exports` holds in
    production);
  - the store's `env` is exactly `["PISTORE"]`.

**The control** (C = 100). 150 fetches stopping at 100 prove the low copy's limit applied.
Without that, every low-copy "uncounted" (P1a, P1b) is void. It is void too when P1c main
reads "counted": store calls cannot count on one copy and not the other.

**P1a** (C = 100).
- Stub: stopping at 100 means store RPCs count as subrequests. 150 means they do not.
- Session: stopping at 99 means calls on a session count (its `openSession` is one call).
  150 means they do not.

**P1b** (C = 100, j = 8, k = 4, h = 4). The outcome is [phase 1 ok, phase 2 ok]. It is
computed from the beats as they happened, so a late heartbeat moves the prediction rather
than voiding the run. With beats on time:

| [phase 1, phase 2] | rule |
|---|---|
| [96, 0] | startEvent: work in flight stays charged to the alarm that started it. One alarm-tracked wait carries every call of the run |
| [84, 0] | neverReset: one counter for the object |
| [180, 96] | heartbeatResets: each heartbeat alarm takes the count over (newest-or-reset, cap-probe M12). Phase 2 stops at C − h |
| [180, 150] | uncounted |

Phase 1 is read as the calls before its first error (`firstErrorAt`, or every call when
none failed), which is what each rule predicts. A late heartbeat stretches a window, so
under heartbeatResets phase 1 can fail and then succeed again once the heartbeat lands;
only a count that a heartbeat resets can recover, so `okAfterFirstError > 0` reads as
heartbeatResets when the beats do not predict the stop exactly. The reading is void if a
read reached the object while it ran, or if no heartbeat came before phase 2.

**P1c.**
- Main: 10,000 means the owner's pinned limit bites store RPCs in one alarm. 10,100 means
  it does not.
- Raised: read against main. Completing (10,100) where main stopped at 10,000 means the
  raised limit applies. Stopping at 10,000 means it did not. A refused deploy means Paid
  did not accept the value.

**P1 runs** (C = 10,000).
- A "Too many subrequests" error within 2 × threads calls of C + 1 means startEvent or
  neverReset: the run was cut at the limit across heartbeats. P1b separates the two.
- No error, with more than C calls in the run, means not startEvent. The heartbeats reset
  the count, or calls are uncounted; P1a tells which.
- No error under C does not discriminate.

The run also gives the count itself:
- the store calls in the run, and the most in any heartbeat window;
- calls per thread per second, and their split by method;
- commit latency under load.

Size `limits.subrequests` from the window maximum under heartbeatResets, or from the run
total under startEvent. P1 counts store calls only: the gateway's model calls and the
files Worker's calls charge the same count, so leave headroom for them.

**P2.**
- The colos: the door's is where the driver's request entered. The owner's and each
  store's come from Cloudflare's trace page, fetched by that object.
- `newThread.storeOpen` − `reopenAfterReset.storeOpen` bounds pi's migration cost from
  above: a new store also pays for creating and placing a new object, which a reset
  instance of an existing one does not.

**P4.**
- The last checkpoint held is a lower bound on `OPEN_MAX` for 300 KB contexts. A
  checkpoint reached more than 10 minutes in is void: pi drops an idle context after
  `contextRetentionMs` (10 minutes by default), so it may hold less than it claims.
- The bound is per isolate, and the isolate also holds the same-script `ThreadStore`
  objects that serve these Sessions (and any other object of the script placed there), as
  production's owner isolate would.
- A `failed` stop is classified by its error: memory, subrequests, CPU or other. A
  `reset` (the alarm's retry) is memory only when analytics counts
  `exceededMemoryErrors` on the P4 owner, CPU when it counts `exceededCpuErrors`, and
  otherwise reads "reset, cause unproven".
- A memory break with k open bounds one Session's memory from above at 128 MB ÷ k.
  Workers does not report memory, so the break is the memory reading.

**P7.**
- The unheld store kept its instance: inconclusive within the idle window.
- The unheld store was evicted and the held store kept its instance: a held session keeps
  its store in memory, which is billed duration.
- Both were replaced: holding does not keep it.

The analytics rows show the billing side:
- requests per store object: the session's calls should not add requests;
- active time per object.

## Running it

Its trigger is `workflow_dispatch` only. **The founder approves each run**, because it
deploys to the production Cloudflare account.

The inputs are:
- `run_minutes`: 1 to 14, default 10. An alarm is cut at 15 minutes.
- `p4_max`: 8 to 256, default 128.
- `raised_limit`: 20,000 to 10,000,000, default 1,000,000.

It uses the repo secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, and needs a
workers.dev subdomain. The token needs:
- **Account → Workers Scripts: Edit**: upload, DO class migrations, the door's workers.dev
  route, delete, and reading the subdomain and the DO namespace list.
- Account → Billing: Read (optional): names the plan.
- Account → Account Analytics: Read (optional): P7's requests and active time per object,
  and the cross-checks for P1 (`subrequests`) and P4 (`exceededMemoryErrors`,
  `exceededCpuErrors`).

Safety, as the cap probe:
- No route, zone or DNS record.
- No owner data.
- No model credential.
- One run at a time (concurrency group `store-probe`).
- `delete.sh` force-deletes all four scripts before the deploy and again at the end,
  whatever happened. `force=true` deletes their Durable Object namespaces with them. It
  then reads the account's namespace list back until none of the four scripts' namespaces
  is listed, and fails the run if a script or namespace remains.
- After a run that failed or was cancelled, run it again (its first step deletes all four
  scripts before deploying) or run `delete.sh` by hand with the same token:
  `CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... bash scripts/probes/storeprobe/delete.sh
  ta-storeprobe-door ta-storeprobe ta-storeprobe-low ta-storeprobe-raised`.

The differences from the cap probe:
- four scripts, not two;
- the owner copies have no workers.dev URL;
- a refused main deploy is a recorded P3 reading, not a failed step;
- the namespace check after the deletes;
- an optional analytics step.

## Results

| Run | Date | Plan | Commit | Run minutes | P4 max | Raised |
|---|---|---|---|---|---|---|
| 41029757222 | 2026-10-30 | Paid (billing unreadable to the token: HTTP 403) | `2c4a7d404` | 10 | 128 held, no break | accepted |

Cleanup: all four `ta-storeprobe*` scripts deleted (HTTP 200) and no namespace left,
checked at 09:41:42Z.
