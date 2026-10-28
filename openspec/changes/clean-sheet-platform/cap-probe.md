# The 32-call cap probe (gateway G0)

For `tasks.md` §6 (the probe before the agent runs a turn on the object). Code:
`scripts/probes/capprobe/`. Workflow: `.github/workflows/cap-probe.yml` (manual only).

## Found (run 1 on Workers Free, run B on Workers Paid)

- **The cap that bites is subrequests per invocation: 50 on this account.** The 32 the
  probe was designed around never bit: 40 sequential service-binding calls passed in one
  Worker request.
- **A DO RPC does not share its caller's counter (M4), and an alarm starts fresh (M5).**
  Inside one alarm, RPC and `fetch` share one counter.
- **A call is charged to the object's current event, not to the async context that made
  it (M12).** Concretely, it goes to the newest incoming invocation, or to one counter that
  each incoming event resets. M12 cannot tell those two apart, and for `calls.ts` they mean
  the same thing: work in flight spends the newest event's budget. This rules out the
  per-async-context attribution that `calls.ts` assumes, a counter that never resets, and
  charging to the oldest invocation.
- **An alarm is cut at 15.0 minutes, even while it awaits I/O, and is then retried (M10).**
- Run 1's account was on Workers Free: 50 is Free's subrequest limit. The founder has since
  moved it to Workers Paid.
- **On Workers Paid at the default limit, no cap bit within what run B tested.** That is up
  to 60 calls per Worker request or DO RPC, and up to 120 in one alarm. The documented 32
  does not bite.
- **A Worker with `global_fetch_strictly_public` cannot reach the private addresses tested
  (M11, run B).** The targets were loopback (127.0.0.1), one 10/8 address (10.0.0.1), the
  metadata address (169.254.169.254) and `localhost`. All four were refused with 403, error
  1003 (direct IP access). The `nip.io` names of the three addresses were refused with 530,
  error 1016 (origin DNS).
- **Decision: run A is not needed.** `calls.ts` is deleted by agent A0
  (`platform-agent-a0`), so attribution no longer drives any design. Run 1 already narrowed
  it to newest-or-reset.

The probe was designed for a cap of 32, but the cap was 50. So several cases stayed under
it and did not discriminate. The results table marks each of those cases "no".

## Why

The service-bindings page says (cloudflare-docs @95e3463,
`workers/runtime-apis/bindings/service-bindings/index.mdx:190-191`):

> - Each request to a Worker via a Service binding counts toward your subrequest limit.
> - A single request has a maximum of 32 Worker invocations, and each call to a Service
>   binding counts towards this limit. Subsequent calls will throw an exception.

The limits page says (`workers/platform/limits.mdx:192-201`, same commit):

> | Limit                            | Workers Free | Workers Paid                              |
> | Subrequests per invocation       | 50           | 10,000 (up to 10M)                        |
> | Subrequests to internal services | 1,000        | Matches configured limit (default 10,000) |
>
> You can change the subrequest limit per Worker using the `limits` configuration in
> your Wrangler configuration file.

The Wrangler configuration page (`workers/wrangler/configuration.mdx:481-482`) says the
limit "defaults to 50 for free accounts and 10,000 for paid accounts. The free account
maximum is 50 and the paid account maximum is 10,000,000."

Neither page says what a Durable Object event counts as. `platform/owner/src/calls.ts`
budgets 32 per platform invocation, 24 for `FILES` and 8 for the gateway and hooks, and
it attributes each call to the async context that made it. Pi starts work in `submit()`
and continues it after that RPC returns, so the attribution rule decides the gateway's
shape. workerd's local limit enforcer is a no-op, so only a live run answers.

## Method

Two throwaway Workers on workers.dev, deployed, driven and deleted by one workflow run:

- `ta-capprobe` (`caller.js`): the SQLite Durable Object class `Probe`, the service binding
  `CALLEE`, and `PEER`, the callee's `Peer` namespace bound by `script_name`.
- `ta-capprobe-callee` (`callee.js`): a `WorkerEntrypoint` with `ping()`, `hold()` and
  `fetch()` (`pong`, or an echoing WebSocket; it answers only the run's key), and the class
  `Peer` with `ping()`.

Both use the owner Worker's runtime: compat date `2026-10-23`, `nodejs_compat`,
`disable_ctx_exports`, plus `global_fetch_strictly_public`. Each case has its own `Probe`
object. A burst makes N calls in sequence (the workflow's `burst` input, 25–120, default
40). It records successes, the first error and where it came, successes after it, and each
call's milliseconds. Every call runs, so a cap shows as where errors start and whether
they stay. Some cases first spend a fixed number of calls (a pre-spend), so each charging
rule predicts a different stopping point.

Every stored record carries the run id, and the driver refuses records from another run.
The workflow force-deletes both scripts, with their objects, before it deploys. The driver
records a failing case (status, the first 200 bytes and any Cloudflare error code such as
1101) and carries on. It rewrites the results after every phase, always reads `/results`
at the end, and prints the whole redacted report to the step log. No case is read while it
should still be running, because a read is an incoming call to that object.

| # | Where | What |
|---|---|---|
| M1 | Worker `fetch` handler | N × `CALLEE.ping()` |
| M2 | Worker `fetch` handler | N × `CALLEE.fetch()` |
| M3 | Worker `fetch` handler | h = ⌊N/2⌋ RPC, then N − h fetch |
| M4 | the Worker, then a DO RPC method it calls | the Worker makes 16 RPC, then the object makes N |
| M5 | `arm()` (a DO RPC), then the object's alarm | `arm()` makes 8 RPC; the alarm makes N RPC, then N fetch |
| M6 | an RPC that starts a loop and returns; nothing awaits it | the RPC makes j = 8; the loop makes 1 call every 2 s, N times |
| M6a | as M6, plus an alarm that awaits the loop (PiHarness's shape) | j = 8 in the RPC; the alarm makes k = 4, then awaits the loop's N |
| M7 | DO `alarm()` | N × `PEER.getByName(i).ping()` |
| M8 | DO `alarm()` | N × global `fetch` to the callee's workers.dev URL |
| M9 | DO `alarm()` | one WebSocket over `CALLEE.fetch`; 100 echoes; h RPC; 1 echo; h RPC; 100 echoes |
| M10 | DO `alarm()` | awaits `CALLEE.hold(16 min)`, with a storage beat every 30 s |
| M11 | Worker `fetch` handler | `fetch` to 127.0.0.1, 10.0.0.1, 169.254.169.254 and localhost, as literals and `nip.io` names; status and size only |
| M12 | two DO RPCs on one object, A and B | A makes f = ⌊0.4N⌋; B starts 10 s later, after A's f have ended, and makes f; both make s = N − f at once; after B's invocation ends, A makes 10 |

**The plan check.** The workflow asks the billing API for the plan (Billing: Read is
optional; run 1's token got a 403). It also burns under a second of CPU in one request.
That burn is inconclusive when it survives, because "each isolate has some built-in
flexibility" for an infrequent overrun (`configuration.mdx`, the `isolate-cpu-flexibility`
partial). Run 1's burn survived on Free. Only a refused burn says anything.

**Local.** `node scripts/probes/capprobe/local.mjs` (after `npm ci` in `platform/`) loads
both configs in miniflare and runs every case twice. The first run uses N = 40. The second
uses N = 60, refuses one case with a 1101, and must refuse that case's first-run records as
stale. Each run checks the pre-spends, the ordering checks and every burst. Every call
passes locally, so this proves the harness, not the cap.

## Reading key

C is the per-invocation cap. It is either read from the case that hits it (on Free, 50) or
set with `subrequests_limit`. "Fresh" means a counter of its own, starting at 0. A value at
or above N cannot show, so each case says when it discriminates.

`rulePredictions(C, N)` in `drive.mjs` is this key as code, and `discrimination(C, N)`
grades each case as full, partial or none. The report carries both for a run that sets the
limit, and `local.mjs` asserts the grades for run 1 and for C = 50, N = 60 (run A's plan,
not needed now).

**M1–M3.** M1 and M2 stop at min(N, C). M3: with one counter for both kinds, the fetch
burst stops at C − h; with separate counters, at min(N − h, C). These discriminate only
when N > C.

**M4** (the Worker spent 16). The object's RPC stops at:
- min(N, C): a DO RPC has a fresh budget;
- C − 16: it shares the calling request's counter, and the stub call is not counted;
- C − 17: it shares the counter, and the stub call is counted;
- N: uncapped.

This discriminates when N > C − 17.

**M5** (`arm()` spent 8). The alarm's RPC + fetch total:
- C: an alarm has a fresh budget;
- C − 8: it continues the count from the RPC that armed it;
- 2N: uncapped.

This discriminates when 2N > C. A fetch burst that stops once the RPC burst has spent the
rest means one counter for both kinds.

**M6** (j = 8 in the starting RPC). The loop stops at:
- C − j: charged to the starting RPC, or to one counter for the object;
- C: a fresh budget of its own;
- N: uncapped.

A loop that stops early with a non-cap error means the object was evicted. This
discriminates when N > C − j.

**M6a** (j = 8, k = 4; void unless `m6aOrder` holds: the loop's wait for the alarm was
met, and every call has `inAlarmBefore` true). The loop stops at:

| loop ok | rule | visible when |
|---|---|---|
| C − j − k | one counter for the object, never reset | N > C − 12 |
| C − j | charged to the starting RPC; or to the oldest live invocation, if the returned RPC still counts as live | N > C − 8 |
| C − k | charged to the alarm: the newest-or-reset rule (M12), or the oldest live invocation once only the alarm is live | N > C − 4 |
| C | a fresh budget of its own | N > C |
| N | uncapped | — |

**M7, M8.** Stopping at C means DO stub calls, or public-URL fetches to a Worker, count like
service-binding calls; reaching N means they don't. These discriminate only when N > C.

**M9.** The socket's open is one `fetch`, so the two RPC bursts total:
- C − 1: the socket counted once, and a message does not refresh the budget;
- C: the socket's open did not count, and a message does not refresh;
- 2h: a socket message refreshed the budget, if each h < C.

This discriminates when 2h > C. If `echoesAfter` is 100, the channel works after the cap.

**M10.** Read the gap between `attempts[].startedAt` together with the first attempt's last
beat. Alarms are at-least-once, so a retry alone means nothing.
- Finished after ≥ 16 min: no cut.
- A retry about 15 min after the first start, with the last beat near 15 min: the cut.
- A retry well under 15 min: a redelivery or crash.
- Still holding, with beats that stopped near 15 min: a cut with no retry seen.

**M11** applies only to Workers that set `global_fetch_strictly_public`, as the probe and
the gateway do.

**M12** (void unless `m12Order` holds: B started after A's f, and all three waits were
met). Both phase 1s are f when 2f ≤ C. M12 separates four rules:

| A + B phase 2 | A phase 3 | rule |
|---|---|---|
| 2 · min(s, C − f) | min(10, C − min(N, C)) | per invocation: each RPC counts its own calls (per async context, as `calls.ts` assumes) |
| C − 2f | min(10, C − 2f − 2s) | never reset or oldest: one counter for the object that never resets, or charged to the oldest live invocation (M6a tells them apart) |
| C − f | min(10, C − f − 2s) | newest-or-reset: charged to the newest invocation, or one counter that each incoming event resets |
| 2s | 10 | uncapped |

"Newest invocation" and "reset on each event" are one rule here. Both stop phase 2 at
C − f, and nothing makes A's older counter current again: run 1's phase 3 was 0. For
`calls.ts` they mean the same thing: work in flight spends the newest event's budget. M12
separates per-invocation from newest-or-reset when 2 · min(s, C − f) ≠ C − f.

## Running it

Its trigger is `workflow_dispatch` only. **The founder approves each run**, because it
deploys to the production Cloudflare account. It uses the repo secrets `CLOUDFLARE_API_TOKEN`
(Account: Workers Scripts: Edit) and `CLOUDFLARE_ACCOUNT_ID`, and needs a workers.dev
subdomain.

The inputs are:
- `hold_minutes` (M10): 0 to 20, default 16;
- `burst` (N): 25 to 120, default 40;
- `subrequests_limit`: empty (the plan's default), or 25 to 10,000.

When `subrequests_limit` is set, `config.mjs` writes copies of both committed configs with
`limits.subrequests` pinned, and the run deploys those. It pins the callee too, so the
callee's own budget can't confound the result. The report and summary record the limit,
where M5 stopped, and which M5 rule that stop matches. On Free the maximum is 50, so a
higher value may not deploy.

A run takes about 25 minutes. The last step deletes both Workers
(`force=true`, which also deletes their Durable Objects), confirms each is gone, and fails
the run if one is not.

## Results

| Run | Date | Plan | Commit | N | Hold |
|---|---|---|---|---|---|
| 40701631460 | 2026-10-28 | Free by inference (billing API 403; CPU burn survived, inconclusive) | 816950be | 40 | 16 min |
| 40741034491 (run B) | 2026-10-28 | Paid (the founder's upgrade; billing API still 403), default limit | 33fbf2d17 | 60 | 0 (M10 skipped) |

**Run 1.** Both Workers were absent before the deploy (404) and deleted after the run (200).
C = 50. The error text was "Too many subrequests by single Worker invocation. To configure
this limit, refer to …/wrangler/configuration/#limits".

| # | Result | Discriminates at C = 50, N = 40? | Rule (from the key) |
|---|---|---|---|
| M1 | rpc 40/40 | no (N < C) | the documented 32-invocation cap did not bite on 40 sequential calls |
| M2 | fetch 40/40 | no | — |
| M3 | 20/20 + 20/20 | no | — (M5 answers the one-counter question inside an alarm) |
| M4 | workerPre 16/16; the DO's rpc 40/40 | partly: sharing would stop at 34 or 33; fresh (min(N, C)) and uncapped both give 40 | a DO RPC does not share the caller's counter |
| M5 | armPre 8/8; alarm rpc 40/40, then fetch 10/40, failing at call 10 | yes | the alarm starts fresh (40 + 10 = C, not C − 8); RPC and fetch share one counter |
| M6 | startPre 8/8; loop 40/40 | no (C − j = 42 > N) | — |
| M6a | startPre 8/8, alarmPre 4/4, loop 40/40; `m6aOrder` ok | partly: only never-reset (C − 12 = 38) shows | not one never-reset object counter; start RPC (42), alarm (46) and fresh (50) are all above N |
| M7 | peer 40/40 | no (N < C) | not shown whether DO stub calls count |
| M8 | public 40/40 | no (N < C) | not shown whether public-URL fetches count |
| M9 | rpc1 20/20, rpc2 20/20 | no (2h + 1 = 41 < C) | — |
| M10 | retried 15.0 min after the first start; the first attempt's last beat at 15.0 min | yes | an alarm is cut at 15 minutes, even while it awaits I/O, and is retried |
| M11 | 7 targets done | — | the per-target detail was only in the job summary's JSON; the driver now prints it to the log and the summary |
| M12 | `m12Order` ok. Phase 1: A 16/16, B 16/16. Phase 2: A 17/24, B 17/24 (34 combined, both failing at call 17). Phase 3: A 0/10, failing at call 0 | partly: per invocation and uncapped both predict 48, 10 | B's 16 + 34 = C, then 0: newest-or-reset (34, 0). Rules out per invocation and uncapped (48, 10) and never reset or oldest (18, 0) |

**Run B.** Both Workers were deleted after the run (each delete returned 200). Every
burst completed, and M5 reported "not reached by 120 calls". At the default C = 10,000 no
case discriminates attribution, because every rule predicts the uncapped outcome. What run B
shows is that no cap applied within what it tested: 60 calls per Worker request or DO RPC,
and 120 in one alarm. The documented 32 does not bite.

| # | Result |
|---|---|
| M1 | rpc 60/60 |
| M2 | fetch 60/60 |
| M3 | 30/30 + 30/30 |
| M4 | workerPre 16/16; the DO's rpc 60/60 |
| M5 | armPre 8/8; alarm rpc 60/60, then fetch 60/60: 120 calls in one alarm |
| M6 | startPre 8/8; loop 60/60 |
| M6a | startPre 8/8, alarmPre 4/4, loop 60/60; `m6aOrder` ok |
| M7 | peer 60/60 |
| M8 | public 60/60 |
| M9 | rpc1 30/30, rpc2 30/30 |
| M10 | not run (hold 0); run 1's 15-minute cut stands |
| M11 | Loopback (127.0.0.1), one 10/8 address (10.0.0.1), the metadata address (169.254.169.254) and `localhost`: 403, error 1003 (direct IP access not allowed). The `nip.io` names of the three addresses: 530, error 1016 (origin DNS) |
| M12 | `m12Order` ok. A 24/24, 36/36, 10/10; B 24/24, 36/36: 130 calls on one object, no error |

**What it means for the platform.** The budget is not per async context. Every service
call an object makes counts against its newest event. Under either form of the rule
(newest invocation, or reset on each event), work in flight spends that event's budget.
That includes background work started by an earlier RPC and continued inside an alarm's
wait, which is PiHarness's shape. An incoming RPC during a turn takes that budget over.
`calls.ts` therefore has the wrong model in two ways:
- its split is 32/24/8, while the cap was 50 here and is 10,000 by default on Paid;
- it attributes per async context.

Run A is not needed. Attribution no longer drives any design: `calls.ts` is deleted by
agent A0 (`platform-agent-a0`), and run 1 already narrowed attribution to newest-or-reset.
