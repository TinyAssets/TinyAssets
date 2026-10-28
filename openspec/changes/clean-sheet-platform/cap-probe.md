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
charged to decides the gateway's shape. workerd's local limit enforcer is a no-op, so only
a live run answers.

## Method

Two throwaway Workers on workers.dev, deployed, driven and deleted by one workflow run:

- `ta-capprobe` (`caller.js`): the SQLite Durable Object class `Probe`, the service binding
  `CALLEE`, and `PEER`, the callee's `Peer` namespace bound by `script_name`.
- `ta-capprobe-callee` (`callee.js`): a `WorkerEntrypoint` with `ping()`, `hold()` and
  `fetch()` (`pong`, or an echoing WebSocket), and the class `Peer` with `ping()`.

Both use the owner Worker's runtime: compat date `2026-10-23`, `nodejs_compat`,
`disable_ctx_exports`, plus `global_fetch_strictly_public`. Each case has its own `Probe`
object. A burst makes N = 40 sequential calls and records successes, the first error and
where it came, successes after it, and each call's milliseconds. Every call runs, so a cap
shows as where errors start and whether they stay. Results are stored in the case's
object, and `GET /results` returns them.

Run order (`drive.mjs`): the Worker cases and M4; M6 and M6a; the alarm cases; M12's two
invocations at once; M10 last. No case is read while it should still be running, because a
read is an incoming call to that object.

| # | Where | What | Answers |
|---|---|---|---|
| M1 | Worker `fetch` handler | 40 × `CALLEE.ping()` | the base cap |
| M2 | Worker `fetch` handler | 40 × `CALLEE.fetch()` | whether fetch counts like RPC |
| M3 | Worker `fetch` handler | 20 RPC, then 20 fetch | one counter or two |
| M4 | a DO RPC method the Worker calls | 40 RPC | per DO call, or shared with the caller's request |
| M5 | DO `alarm()` | 40 RPC, then 40 fetch | per DO event |
| M6 | DO, a loop started in an RPC that returned; nothing awaits it | 1 RPC every 2 s, 40 times | what background work is charged to, and whether it survives |
| M6a | as M6, with an alarm that awaits the loop (PiHarness's shape) | 1 RPC every 2 s, 40 times | the drain loop's budget |
| M7 | DO `alarm()` | 40 × `PEER.getByName(i).ping()` | whether DO stub calls count |
| M8 | DO `alarm()` | 40 × global `fetch` to the callee's workers.dev URL | whether a public-URL hop counts (a fallback) |
| M9 | DO `alarm()` | one WebSocket over `CALLEE.fetch`: 100 messages, 40 RPC, 100 messages | whether a multiplexed channel counts once and outlives the cap (a fallback) |
| M10 | DO `alarm()` | awaits `CALLEE.hold(16 min)`, a storage beat every 30 s | the 15-minute alarm cut |
| M11 | Worker `fetch` handler | `fetch` to 127.0.0.1, 10.0.0.1, 169.254.169.254 and localhost, as literals and as `nip.io` names | the address check (gateway §6); status and size only, no body |
| M12 | two concurrent DO RPCs on one object | each makes 16 RPC, they meet, then each makes 24 more at once | the attribution question, directly |

Reading M12: per-invocation counters let each side reach 32. One counter per object stops
both sides near 16. Charging calls to the newer invocation stops both somewhere in between.
Reading M6 and M6a: each call records its time and whether the alarm was awaiting it.
Reading M10: a retry attempt, or beats that stop near 15 minutes, means a cut.

**The plan check.** The workflow reads the plan from the billing API (it needs Billing:
Read, which is optional) and burns under a second of CPU in one request (1e8 loop steps), far past
the Free plan's 10 ms. A Free or unknown plan is reported as a warning and the run still
goes ahead. On Free, the 50-subrequest limit can mask the cap.

**Local.** `node scripts/probes/capprobe/local.mjs` (after `npm ci` in `platform/`) loads
both configs in miniflare and runs every case with short waits. Every call passes there,
so it proves the harness, not the cap. The workflow runs it before deploying.

## Running it

Its trigger is `workflow_dispatch` only, so it runs once the workflow is on `main` and
someone dispatches it. The founder approves the run. It uses the repo secrets
`CLOUDFLARE_API_TOKEN` (Account: Workers Scripts: Edit) and `CLOUDFLARE_ACCOUNT_ID`, and
needs a workers.dev subdomain on the account. It takes about 25 minutes. The last step
deletes both Workers (`force=true`, which also deletes their Durable Objects), confirms
each is gone, and fails the run if one is not.

## Results

Not yet run. Fill this in from the job summary, then set `INVOCATION_CALLS` and the
gateway's share in `calls.ts`, or choose a fallback from the gateway design §8.5.

| Run | Date | Plan | Commit |
|---|---|---|---|
| | | | |

| # | ok / 40 (first error at) | First error | Reading |
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
