# Account usage is two numbers: cloud GiB and concurrent seats

## Why

Founder directive, 2026-09-30, verbatim:

> "usage limits for accounts should really only be based on 2 things, total
> gibs thier universe takes up in the cloud. and how many agent calls thier
> universe can simoltaniously run. both my users are currently on the
> tinyassets free account for now and everything we are testing should still
> function for free users, but for example in the village testing a user could
> not prompt infinate agents at once without some pending waiting on a
> avalable seat. free users have less cloud storage space and less
> simaltaniouse agent runs. but everything we are testing should work so
> neiter of our users should need to upgrade there tinyassets account just for
> it to work"

Addition, 2026-09-30:

> "when a user is waiting for a free seat it should prompt and link them to be
> able to upgrade."

Refinement, 2026-09-30:

> "the upgrade prompt is just a clickable link inside the waiting message
> itself, e.g. 'Waiting for a free seat (2 running). Upgrade for more seats'
> with 'Upgrade' linked. No separate banner, button, card or modal."

What exists today is a pile of RATE meters, all of them refusals, none of them
either of those two numbers:

| Meter | Value | Where |
|---|---|---|
| hourly write admissions | 1200 / 3600 s | `engine_admissions.RUN_WRITE_LIMIT` |
| hourly total admissions | 3600 / 3600 s | `engine_admissions.RUN_TOTAL_LIMIT` |
| rolling-day runs | 20,000 / 86400 s | `engine_admissions.RUN_DAY_LIMIT` |
| hourly effect dispatches | 5,000 / 3600 s | `engine_admissions.DISPATCHES_PER_HOUR` |
| hourly effect bytes | 2 GiB / 3600 s | `engine_admissions.BYTES_PER_HOUR` |
| app_event emit meter | charged to run admission | `api/app_events.py` (#4107) |

Every one of them REFUSES. That is the wrong verb for a concurrency limit: a
user who prompts four agents on a two-seat account has not done anything
wrong, they have asked for more than fits at once. The founder's word is
"pending", not "refused".

None of them is a storage limit. The only storage quota in the tree is
`_universe_quota_kwargs` in `effectors/workspace.py`, hardcoded at 16 GiB, and
it covers the `workspaces/` subtree only — not the universe's cloud footprint.

`consolidate-platform-resource-policy` already walked one step down this road
("the owner's direction is simpler accounting of actual platform use, not
another isolated larger number") and its own status text says the metering
change "has NOT landed". This is that change.

## What Changes

### 1. Seats — concurrent agent calls, queued not refused

A **seat** is one in-flight agent call belonging to one universe. Everything
that runs a model holds one for as long as it runs: a `converse` chat turn, an
agent node inside a graph run, an automation run, an `event` / `once` wake, an
`app_event` wake.

- Over the limit, work **queues**. It is never refused and never dropped.
- The queue is ordered by **(priority, enqueued_at)** — longest-owed first
  within a priority class. Two classes: `interactive` (a chat turn the owner
  is sitting in front of) and `background` (everything else).
- **Fairness rule:** background work may occupy at most `seats - 1` seats. The
  last seat is reachable only by interactive work. So a runaway ping-pong of
  background wakes cannot make the owner's chat wait for anything except
  another chat turn. This is the shape `provider_admission`'s nested reserve
  already proved on the host bound; it gives a hard guarantee rather than a
  probabilistic one.
- **A blocking nested call inherits its parent's seat.** An agent node that
  invokes a sub-branch and waits for it is not executing a model while it
  waits. Re-entrant by seat id, so a chain cannot deadlock against itself.
  A non-blocking (async / by-version) invoke takes its own seat.
- Seats are **leases in SQLite**, not an in-memory semaphore: engine MCP runs
  in a child process, and a deploy restart must not strand them. A seat is
  released on every terminal path, and a lease that stops being refreshed is
  **reaped by expiry**.
- The waiting state is **visible** wherever the owner looks: the chat turn's
  own reply, `read_graph`, and the app's run/automation status. The message
  names the number running and carries an **upgrade link** inline.

### 2. Storage — total cloud GiB against a tier quota

- `universe_storage_bytes(universe_id)` is the universe's whole cloud
  footprint: every regular file under `<data_dir>/<universe_id>/`, minus
  scratch-lease bytes (which belong to the shared pool and are charged to no
  universe — memory `storage-permanent-vs-scratch`).
- Accounting is **measured + pending**: a TTL-memoized full measurement, plus
  a per-universe ledger of bytes admitted since that measurement. Many small
  writes therefore cannot evade the quota between measurements.
- **At the quota, new writes are refused** with a structured visible failure
  naming the used bytes, the quota, how to free space, and the upgrade link.
- **Reads never break.** Nothing on a read path consults the quota.

### 3. Every other usage meter is deleted

`RUN_WRITE_LIMIT`, `RUN_TOTAL_LIMIT`, `RUN_DAY_LIMIT`, `DISPATCHES_PER_HOUR`,
`BYTES_PER_HOUR`, `BUDGET_WINDOW_S`, `usage_notice`, the `usage_limit` /
`run_usage_limited` / `run_rate_limited` / `usage_limited` refusals and the
`app_events` emit meter all go. The admission ledger keeps only what is not a
meter: the read/write **settlement** machinery, which exists so the effect
boundary can tell what a run actually did.

Per-call and per-run byte caps stay. They are execution safety (one packet, one
run), not account usage, and the founder's two numbers do not replace them.

### 4. Tier values live in one place

`tinyassets/tiers.py` — one table, two columns, keyed by the tier
`storage/subscription_state.py` already owns:

| tier | seats | storage |
|---|---|---|
| `free` | 3 (2 background + 1 interactive-reserved) | 2 GiB |
| `paid` | 8 (7 background + 1 interactive-reserved) | 50 GiB |

Free gets a smaller quota and fewer seats, as directed. A 4-agent village on
free runs 2 at a time and the other 2 wait — which is the behaviour the
founder asked to be able to see — and completes without an upgrade. Both test
accounts are free and every surface under test works for them.

The founder's example was "e.g. 2 seats". 3 is the smallest number that
satisfies BOTH stated requirements at once: a 4-agent village that queues
(needs `seats - 1 >= 2` to be a queue rather than a serial pipe) and a chat
that never waits on background work (needs one reserved seat). Flagged at the
design point.

### 5. Runaway protection is seats alone

A self-waking loop holds at most its seats, so it costs a bounded amount of
concurrency forever instead of a bounded number of runs once. There is no
hourly or daily ceiling left to pace it against, and it does not need one: it
cannot grow, and it cannot reach the interactive seat.

## Capabilities

### New Capabilities

- `universe-seats`: the per-universe concurrency layer — seat leases, the
  priority queue, the interactive reserve, reaping, the tier table, and the
  visible waiting state with its upgrade link.

**`universe-storage-quota` is re-scoped to its own change** (astra round 1,
findings 10-16 and 18; `REVIEW.md`). Finding 16 is a hard blocker: workspace
creation reserves a fixed 4 GiB, so a 2 GiB free quota refuses a permanent
workspace on an EMPTY free universe — the opposite of the directive's own
floor. The accounting also spans four physical stores that sit BESIDE universe
directories rather than inside them, so a universe-directory scan cannot
reconcile the bytes it is supposed to charge. Section 2 below is the target
model and stays as the record of intent; it is not built here.

### Modified Capabilities

- `engine-run-admissions`: every rate meter and its refusals are removed;
  settlement survives as effect bookkeeping, not as a budget.
- `graph-execution-substrate`: an agent node holds a seat; a blocking nested
  invoke inherits it.
- `user-owned-automations`: a due run waits for a seat instead of being
  rate-refused; `run_rate_limited` is gone.
- `live-mcp-connector-surface`: `read_graph` reports seats and storage;
  `converse` reports a waiting turn; `get_status` drops the removed meters.
- `onboarding-web-app`: `/mcp/app?upgrade=1` opens the existing checkout flow
  so a message can carry a clickable upgrade link; `billing/status` reports
  the two enforced numbers instead of `"enforced": []`.

## Impact

- **Authority / money:** the tier now gates two real numbers. It did not gate
  anything before (`billing/status` returned `"enforced": []`).
- **Storage shape:** two new tables (`universe_seats`, `seat_waiters`) and one
  storage-delta table. No migration of existing data; the removed meters'
  tables are dropped on first touch.
- **Behaviour:** work that used to be refused now waits. That is the point,
  and it is the founder's word.
- Code: `universe_seats.py` (new), `universe_storage.py` (new), `tiers.py`
  (new), `engine_admissions.py`, `graph_compiler.py`, `automations.py`,
  `api/runs.py`, `api/automations.py`, `api/deliveries.py`,
  `api/resource_usage.py`, `effectors/__init__.py`, `engine_mcp_server.py`,
  `universe_server.py`, `onboarding/__init__.py`, `onboarding/app.html`.
- Split: **PR 1 = seats + queue + tier table**; **PR 2 = meter deletion**;
  storage its own change after its own storage-shape review. PR 2 rebases onto #4107 so the `app_events` emit meter is
  deleted rather than merged around.

C2 folds `usage-limits-replace-caps`, `remove-non-usage-limits`, `account-storage-quota`, `consolidate-platform-resource-policy` into this single active change.
