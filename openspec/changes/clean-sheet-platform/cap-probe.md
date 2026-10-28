# The 32-call cap probe (gateway G0)

For `tasks.md` §6 (the probe before the agent runs a turn on the object). Code:
`scripts/probes/capprobe/`. Workflow: `.github/workflows/cap-probe.yml` (manual only).

## Why

Cloudflare documents "a maximum of 32 Worker invocations" per request, with each
service-binding call counting and later calls throwing. It says nothing about Durable
Object events. `platform/owner/src/calls.ts` budgets 32 per platform invocation: 24 for
`FILES`, 8 for the gateway and hooks. If the cap covers a whole alarm, including the work
it awaits, a pi turn inside one PiHarness wait gets about 8 model calls. Pi starts work
in `submit()` and continues it after that RPC returns, so which invocation such calls are
charged to decides the gateway's shape. workerd charges an object's usage to its newest
incoming request (`incomingRequests.front()`) and tops the actor up on every incoming
request and WebSocket event, so "charged to the newest invocation" and "reset on each
event" are both plausible. Its local limit enforcer is a no-op, so only a live run answers.

## Method

Two throwaway Workers on workers.dev, deployed, driven and deleted by one workflow run:

- `ta-capprobe` (`caller.js`): the SQLite Durable Object class `Probe`, the service binding
  `CALLEE`, and `PEER`, the callee's `Peer` namespace bound by `script_name`.
- `ta-capprobe-callee` (`callee.js`): a `WorkerEntrypoint` with `ping()`, `hold()` and
  `fetch()` (`pong`, or an echoing WebSocket; it answers only the run's key), and the class
  `Peer` with `ping()`.

Both use the owner Worker's runtime: compat date `2026-10-23`, `nodejs_compat`,
`disable_ctx_exports`, plus `global_fetch_strictly_public`. Each case has its own `Probe`
object. A burst makes its calls in sequence and records successes, the first error and
where it came, successes after it, and each call's milliseconds. Every call runs, so a cap
shows as where errors start and whether they stay. Some cases first spend a known number
of calls (a pre-spend), so each charging rule predicts a different stopping point.

Every stored record carries the run id, and the driver refuses records from another run.
The workflow also force-deletes both scripts, with their objects, before it deploys.
The driver records a failing case (status, the first 200 bytes and any Cloudflare error
code such as 1101) and goes on. It rewrites the results after every phase, and it always
reads `/results` at the end.

Run order (`drive.mjs`): the Worker cases and M4; M6 and M6a; the alarm cases; M12; M10
last. No case is read while it should still be running, because a read is an incoming
call to that object.

| # | Where | What |
|---|---|---|
| M1 | Worker `fetch` handler | 40 × `CALLEE.ping()` |
| M2 | Worker `fetch` handler | 40 × `CALLEE.fetch()` |
| M3 | Worker `fetch` handler | 20 RPC, then 20 fetch |
| M4 | the Worker, then a DO RPC method it calls | the Worker makes 16 RPC, then the object makes 40 |
| M5 | `arm()` (a DO RPC), then the object's alarm | `arm()` makes 8 RPC; the alarm makes 40 RPC, then 40 fetch |
| M6 | an RPC that starts a loop and returns; nothing awaits it | the RPC makes 8 calls (j); the loop makes 1 call every 2 s, 40 times |
| M6a | as M6, plus an alarm that awaits the loop (PiHarness's shape) | j = 8 in the RPC; the alarm makes 4 (k), then awaits the loop's 40 |
| M7 | DO `alarm()` | 40 × `PEER.getByName(i).ping()` |
| M8 | DO `alarm()` | 40 × global `fetch` to the callee's workers.dev URL |
| M9 | DO `alarm()` | one WebSocket over `CALLEE.fetch`; 100 echoes; 20 RPC; 1 echo; 20 RPC; 100 echoes |
| M10 | DO `alarm()` | awaits `CALLEE.hold(16 min)`, with a storage beat every 30 s |
| M11 | Worker `fetch` handler | `fetch` to 127.0.0.1, 10.0.0.1, 169.254.169.254 and localhost, as literals and `nip.io` names; status and size only |
| M12 | two DO RPCs on one object, A and B | A makes 16; B starts 10 s later, after A's 16 have ended, and makes 16; both make 24 at once; after B's invocation ends, A makes 10 |

The plan check reads the plan from the billing API (Billing: Read is optional), and burns
under a second of CPU in one request (1e8 loop steps), far past the Free plan's 10 ms. A
Free or unknown plan is reported as a warning and the run still goes ahead. On Free, the
50-subrequest limit can mask the cap.

**Local.** `node scripts/probes/capprobe/local.mjs` (after `npm ci` in `platform/`) loads
both configs in miniflare and runs every case with short waits. It checks each pre-spend's
size, that M12's B started after A's first phase, and every burst. A second run on the
same objects then refuses one case with a 1101; the driver must record it, carry on, and
refuse that case's first-run records as stale. Every call passes locally, so this proves
the harness, not the cap. The workflow runs it before deploying.

## Reading key

Each outcome names one rule. "Fresh" means a counter of its own, starting at 0.

**M1–M3.** M1's stopping point is the base cap (32 if documented). M2: the same number
means `fetch` counts like RPC. M3: 20 + 12 means one counter for both kinds; 20 + 20 means
separate counters.

**M4** (the Worker spent 16):

| object's RPC ok | rule |
|---|---|
| 32 | a DO RPC has a fresh budget |
| 16 | the DO RPC shares the calling request's counter, and the stub call is not counted |
| 15 | the DO RPC shares the calling request's counter, and the stub call is counted |
| 40 | DO RPCs are uncapped |

**M5** (`arm()` spent 8; the alarm makes 40 RPC, then 40 fetch):

| alarm RPC ok | rule |
|---|---|
| 32 | an alarm has a fresh budget |
| 24 | an alarm continues the count from the RPC that armed it (one counter for the object, or charged across events) |
| 40 | alarms are uncapped |

The alarm's fetch burst then repeats M3's question inside an alarm.

**M6** (the RPC spent j = 8; nothing awaits the loop):

| loop ok | rule |
|---|---|
| 24 (32 − j) | background work is charged to the RPC that started it, or to one counter for the object |
| 32 | background work has a fresh budget of its own |
| 40 | background work is uncapped |
| fewer, with a non-cap error or calls that stop | the object was evicted mid-loop: read the times |

**M6a** (j = 8 in the RPC; the alarm spends k = 4, then awaits the loop; each call
records `inAlarmBefore` and `inAlarmAfter`). The loop waits, for at most 30 s, until the
alarm has spent its k and is awaiting. The run's M6a is void unless that wait was met and
every loop call has `inAlarmBefore` true (the driver's `m6aOrder`, which the summary prints):

| loop ok | rule |
|---|---|
| 24 (32 − j) | charged to the RPC that started the loop; or to the oldest live invocation, if the returned RPC still counts as live |
| 28 (32 − k) | charged to the alarm awaiting it: the newest invocation, a counter reset when the alarm started, or the oldest live invocation once only the alarm is live (M12 separates these) |
| 20 (32 − j − k) | one counter for the object, never reset |
| 32 | the loop has a fresh budget of its own |
| 40 | uncapped |

**M7, M8.** 32 (or M1's cap) means DO stub calls, or public-URL fetches to a Worker, count
like service-binding calls. 40 means they don't.

**M9** (the socket's open is one `fetch`, 1 call; rpc1 = 20; then one echo; then rpc2):

| rpc2 ok | rule |
|---|---|
| 11 | the socket counted once, and a socket message does not refresh the budget |
| 12 | the socket's open did not count, and a message does not refresh |
| 20 | a socket message refreshed the budget (if M5 shows alarms are capped) |

`echoesAfter` = 100 means the channel keeps working after the cap.

**M10.** Read the gap between `attempts[].startedAt` together with the first attempt's last
beat. Alarms are at-least-once, so any retry alone means nothing.
- `finished` after ≥ 16 min: no wall-time cut while an alarm awaits I/O.
- A retry about 15 min (plus backoff) after the first start, with the first attempt's last
  beat near 15 min: the cut, at about that beat.
- A retry well under 15 min after the first start: a redelivery or crash, not the cut. Read
  the beats for when the first attempt stopped.
- Still `holding` with one attempt, and beats that stopped near 15 min: a cut with no retry
  seen.

**M11** applies only to Workers that set `global_fetch_strictly_public`, as the probe and
the gateway do. A Worker without the flag must be probed separately.

**M12.** The run's M12 is void unless the driver's `m12Order` holds: B started after A's
16 (`B.startedAt > A.startedAt + A.phase1.totalMs`), and all three waits were met (A for
B's phase 1, A for B's end, B for A's phase 1). The summary prints the check and each wait.

| A phase 1 | B phase 1 | A + B phase 2 | A phase 3 | rule |
|---|---|---|---|---|
| 16 | 16 | 32 (16 each) | 0 | per invocation: each RPC counts its own calls |
| 16 | 16 | 0 | 0 | one counter for the object, never reset, or charged to the oldest live invocation: M6a tells them apart (20 means never reset; 24 or 28 means oldest) |
| 16 | 16 | 16 combined | ≥ 1 (typically 9–10) | charged to the newest invocation: once B ends, A's own counter (16 used) is current again |
| 16 | 16 | 16 combined | 0 | one counter for the object, reset on each incoming event; or the newest invocation with B still current |
| 16 | 16 | 48 | 10 | uncapped |

Phase 3 under the newest-invocation rule is "at least 1", not exactly 10. B can stay the
current request until A's settle-sleep timer fires, and that races A's first phase-3 call.
So a phase 3 of 0 after "16 combined" means either a reset on each event, or the newest
invocation with B still current. Neither reading pins it alone.

## Running it

Its trigger is `workflow_dispatch` only, so it runs once the workflow is on `main` and
someone dispatches it. **The founder approves each run**: it deploys to the production
Cloudflare account. It uses the repo secrets `CLOUDFLARE_API_TOKEN` (Account: Workers
Scripts: Edit) and `CLOUDFLARE_ACCOUNT_ID`, and needs a workers.dev subdomain on the account.
`hold_minutes` (M10) is 0 to 20, with a default of 16. The run takes about 25 minutes. The last step
deletes both Workers (`force=true`, which also deletes their Durable Objects), confirms
each is gone, and fails the run if one is not.

## Results

Not yet run. Fill this in from the job summary, then set `INVOCATION_CALLS` and the
gateway's share in `calls.ts`, or choose a fallback from the gateway design §8.5.

| Run | Date | Plan | Commit |
|---|---|---|---|
| | | | |

| # | ok (first error at) | First error | Rule (from the key) |
|---|---|---|---|
| M1 | | | |
| M2 | | | |
| M3 | | | |
| M4 | | | |
| M5 | | | |
| M6 | | | |
| M6a | | | |
| M7 | | | |
| M8 | | | |
| M9 | | | |
| M10 | | | |
| M11 | | | |
| M12 | | | |
