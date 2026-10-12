# Design: two-dimension usage limits

## D0. Why this is a spec-first change

Three of the five things `AGENTS.md` names as hard to reverse are in scope at
once: **authority** (the tier becomes an enforcement input for the first time),
**storage shape** (two new tables and a new accounting rule), and
**money/tiers** (the numbers a subscription buys). Public surface moves too
(`read_graph`, `converse`, `billing/status`). So: proposal + design + delta
before code.

## D1. The seat unit is one agent call, not one provider attempt

The obvious place to bound concurrency is `providers/router.py:1130`, the single
`async with _provider_slot(...)` every provider call already passes through. It
is the wrong place.

- One agent call makes **several** provider attempts: a fallback chain across
  sources, a judge ensemble, a retry after a refusal. Charged there, a seat
  would be taken and released repeatedly inside one agent call, so "2 seats"
  would not mean "2 agents".
- The founder's unit is explicit: "how many **agent calls** their universe can
  simultaneously run", and "a user could not prompt infinite **agents** at
  once".

So a seat is held **across** one agent call and the provider bound stays
underneath it, unchanged:

```
universe seats        (this change)   per-universe, tier-sized, QUEUES
  provider_admission  (existing)      host-wide, memory-sized, REFUSES
    _SYNC_CALL_MAX_WORKERS = 8        thread pool
```

These are not competing mechanisms. `provider_admission` is a **memory** bound
derived from measured RSS on a 2 GB box (its docstring is the derivation); it
protects the host from OOM and would exist if there were one user. Seats are a
**product** bound derived from the tier; they protect users from each other and
would exist if the box were infinite. A universe can be at its seat limit with
provider slots free (more universes than seats each), and provider slots can be
exhausted with seats free (many universes, one seat each). Both are correct.

`free` seats (3) sit below the non-nested effective provider limit (6 - 1 = 5),
so a single free universe never reaches the host bound first — which is what
makes the seat the number the owner experiences.

## D2. Seats are database leases, above the per-agent lease

There is already a per-agent fence: `universe_leases` in the automations
database, keyed `agent:<len(uid)>:<uid>:<branch>` (`automation_lease_key`).
It answers **"is THIS agent already running?"** — one automation never overlaps
itself, with a per-automation `overlap` policy (`queue` / `skip` /
`cancel_previous`).

Seats answer a different question: **"may ANY of this universe's agents start
right now?"** They are the layer above, and the two compose without either
knowing about the other:

| | per-agent lease (`universe_leases`) | seat (`universe_seats`) |
|---|---|---|
| Scope | one branch in one universe | the whole universe |
| Question | is this agent already running? | is there capacity for one more? |
| Count | 1 | tier `seats` |
| Over the limit | per-automation `overlap` policy | queue, always |

Order of acquisition is **lease first, then seat**. A due automation run that
its own lease already excludes must resolve that by its `overlap` policy
BEFORE joining the seat queue; otherwise a `skip`-policy automation would sit
in the seat queue only to be dropped when it got one, and its wait would have
displaced work that would have run. Stated as a spec requirement because the
reverse order is the natural thing to write and it wastes the scarce resource.

Leases, not an in-memory semaphore, for three reasons already load-bearing
elsewhere in this tree:

1. **Multi-process.** Engine MCP runs as a child process
   (`TINYASSETS_ENGINE_GRAPH_ID`); `automations.py`'s own comment records the
   bug an in-memory `_active` map caused ("a restarted process (empty map)
   could launch work an OLD process is still doing").
2. **Deploy restart.** Memory `deploy-kills-in-flight-turns`: every merge
   recreates the container. An in-memory seat count restarts at zero while
   provider subprocesses may still be dying; a lease table restarts with rows
   whose expiry reaps them.
3. **Crash.** A process that dies without unwinding leaks a seat forever. An
   expiry cannot leak.

### Table shape

```sql
CREATE TABLE universe_seats (
    seat_id      TEXT PRIMARY KEY,   -- opaque, generated
    universe_id  TEXT NOT NULL,
    class        TEXT NOT NULL,      -- 'interactive' | 'background'
    kind         TEXT NOT NULL,      -- chat_turn|agent_node|automation|wake|app_event
    run_id       TEXT NOT NULL DEFAULT '',
    holder       TEXT NOT NULL,      -- process holder token
    depth        INTEGER NOT NULL DEFAULT 1,  -- re-entrancy (D3)
    acquired_at  REAL NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX universe_seats_universe ON universe_seats(universe_id, expires_at);

CREATE TABLE seat_waiters (
    ticket       INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotone => longest-owed
    universe_id  TEXT NOT NULL,
    class        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    run_id       TEXT NOT NULL DEFAULT '',
    holder       TEXT NOT NULL,
    enqueued_at  REAL NOT NULL,
    expires_at   REAL NOT NULL       -- a waiter's own liveness lease
);
CREATE INDEX seat_waiters_universe ON seat_waiters(universe_id, class, ticket);
```

`ticket` is an `AUTOINCREMENT` rowid, so "longest-owed" is an integer
comparison rather than a float timestamp comparison — two waiters enqueued in
the same millisecond still have a total order. (`enqueued_at` is kept for the
owner-facing "waiting since".)

Location: `<data_dir>/.universe_seats.db`, resolved through
`tinyassets.storage.data_dir()` — never the CWD (`AGENTS.md` configuration
invariant), and the same symlink/out-of-tree refusal
`engine_admissions._ledger_is_trusted` applies, because a tampered seat ledger
is a cross-user concurrency escape.

**Cross-user isolation:** every statement is keyed on `universe_id`, and the
count that gates admission is `WHERE universe_id = ?`. No query aggregates
across universes, so no universe's occupancy can consume or reveal another's.
The one shared column is `ticket`, which is a global sequence — it orders
waiters, and ordering is only ever compared **within** a `universe_id`
predicate. Named here because "seat theft across users" is a review target.

### Acquisition, in one transaction

```
BEGIN IMMEDIATE
  reap:     DELETE FROM universe_seats  WHERE expires_at < now
            DELETE FROM seat_waiters    WHERE expires_at < now
  reentry:  if caller carries a seat_id held by this universe -> depth += 1, return it
  count:    live = COUNT(universe_seats WHERE universe_id = ?)
  ceiling:  interactive -> seats ;  background -> seats - reserve
  ahead:    COUNT(seat_waiters WHERE universe_id = ? AND eligible-ahead-of-me)
  if live < ceiling AND ahead == 0:  INSERT seat; DELETE my waiter row; -> HELD
  else:                              INSERT/refresh my waiter row;     -> WAITING(ticket)
COMMIT
```

`ahead == 0` is what makes the queue a queue: a caller that arrives while
someone is already owed a seat joins behind them rather than stealing the
capacity. "Eligible-ahead-of-me" is: every `interactive` waiter with a smaller
ticket, plus — only if I am `background` — every `background` waiter with a
smaller ticket. An interactive waiter is therefore never behind a background
one whatever the tickets say, which is the fairness rule as a query.

Reaping happens at the top of **every** acquisition rather than on a timer. A
timer is one more thing that can be dead when it matters, and after a deploy
the first acquisition is exactly when the stale rows need to be gone. The
startup-barrier subtlety `workspace_pool.PROCESS_STARTED_AT` solves does not
arise here: a seat's expiry is absolute, so a fresh process needs no opinion
about which rows predate it.

### Release on every terminal path

Success, failure, cancellation, timeout, and crash. The first four are a
`try/finally` in the seat context manager. The fifth is `expires_at`.

`SEAT_LEASE_SECONDS = 120`, refreshed every `SEAT_REFRESH_SECONDS = 30` while
held — the automations pump already runs a lease refresher on exactly this
cadence (`LEASE_REFRESH_SECONDS = 60`), so the pattern and its failure modes
are known here. The lease is deliberately much shorter than
`DEFAULT_RUN_TIMEOUT_SECONDS` (10800): a seat outlives a dead process by at
most 2 minutes, not 3 hours. A **live** long run keeps its seat for as long as
it runs, because the refresher keeps stamping it — "a served turn runs until it
is finished" (memory `turn-runs-until-finished-not-wall-clock`) is not
weakened by a 2-minute lease, only by a missing refresher.

## D3. Nested blocking calls inherit the seat

A graph whose agent node invokes a sub-branch containing another agent node:
the parent is **blocked** on the child. It is not executing a model. Charging
two seats would mean a 2-seat account cannot run a 2-deep agent chain at all —
it would deadlock on itself, which is the exact failure
`provider_admission._NESTED_RESERVE` was added to fix one layer down.

So a **blocking** nested acquisition is re-entrant: the held `seat_id` travels
on the existing `provider_invocation` carrier that `router._is_nested` already
reads, the nested acquire increments `depth`, and release decrements it. The
seat row disappears at `depth == 0`.

An **async / by-version** invoke is not re-entrant: the parent continues, both
are executing, both pay. This is the same blocking-vs-async distinction
`runs.py`'s two-pool model already makes, so the information is available where
it is needed.

Consequence worth stating: seats bound **simultaneous model execution**, not
the depth or breadth of what a user builds. "No structural caps on graph size"
(memory, founder 2026-08-30) survives intact — a 40-agent graph is legal on
free, it just runs 2 at a time.

## D4. Chat turns get feedback, never a hang

`converse` is a synchronous MCP handler. "Queue, never refuse" and "never hang"
are in tension there, and the interactive reserve resolves most of it: an
interactive turn only ever waits behind ANOTHER interactive turn, never behind
background work. Beyond that:

1. Acquire with a bounded wait, `SEAT_WAIT_S = 20.0` — the value
   `provider_admission` already uses for the same judgement ("long enough to
   ride out a brief burst, short enough that a queued user gets an answer
   rather than a hang").
2. If the seat arrives, the turn runs normally.
3. If it does not, the turn returns the **waiting message** and keeps its
   queue ticket. The work is not dropped: it runs when the seat frees and the
   reply arrives through the ordinary asynchronous delivery path.

The waiting message is one line with one link:

> Waiting for a free seat (3 running). [Upgrade](…) for more seats.

The same text and link are used for a storage refusal, differing only in the
fact:

> This universe is using 2.0 GiB of its 2.0 GiB of cloud storage. Delete
> run outputs or workspaces to free space, or [Upgrade](…) for more.

**Top-tier accounts get no link.** `upgrade_link_for(tier)` returns `None` for
the highest tier in `tiers.TIERS`, so the prompt degrades to the fact alone.
Deriving it from the table rather than from `tier != "free"` means a future
middle tier is handled without touching this code.

### The link

There is no GET upgrade route. The header button (`app.html`, `btn-plan` →
`startSubscribe`) POSTs `/mcp/app/billing/checkout`, which is identity-gated
(`auth/middleware.py:582`) and returns a Stripe URL. A link in a message cannot
POST, and inventing a route is forbidden.

So: **`https://tinyassets.io/mcp/app?upgrade=1`** — the app's existing route
(`Route("/mcp/app", _handle_app, ...)`), plus a query parameter the app reads
on load to call the **same** `startSubscribe()` the header button calls. No new
route, no new checkout path, no second URL to keep in sync, and the origin is
the canonical one Hard Rule 11 names. The URL is built by one function so there
is exactly one string to test.

## D5. Storage: measured plus pending

"Total gibs their universe takes up in the cloud" has to survive a scan that
cannot finish. The existing measurement,
`api/storage_observations.observe()`, is deliberately partial — `MAX_ENTRIES
10_000`, `SCAN_SECONDS 0.2`, and it reports `availability: unavailable` with
`reason: total_storage_not_measured`. Honest as an observation, unusable as an
authority: an under-count is an evasion and a refusal-on-unknown breaks a
working universe.

**Accounting = measured + pending.**

- `measured_bytes`: a full recursive sum of regular-file logical bytes under
  `<data_dir>/<universe_id>/`, minus bytes attributed to scratch leases
  (`workspace_leases WHERE storage_class = 'scratch'`) — scratch is the shared
  pool's, charged to no universe (memory
  `storage-permanent-vs-scratch`). Memoized with a TTL through the existing
  `ttl_memo` helper, refreshed off the write path.
- `pending_bytes`: a per-universe ledger row incremented by every admitted
  write and **reset to zero** by each remeasurement, transactionally with the
  measurement's timestamp so a write concurrent with a remeasure is counted
  once rather than dropped.
- `used = measured + pending`. A thousand small writes between measurements
  are all in `pending`, so they cannot slip the quota — the specific evasion
  a TTL cache alone would allow.

Symmetry note: this is the pattern `workspace_pool._universe_outstanding_bytes`
already uses for the same reason ("two reentrant admissions would each see 0
used and each reserve 6 against a 10 GiB quota"). Storage accounting is now
one rule instead of that one plus a 16 GiB constant in
`effectors/workspace.py`, which is deleted in favour of the tier quota.

**A measurement that fails does not refuse.** If the scan raises, the last good
measurement plus pending is used; if there has never been one, the universe is
admitted and the failure is logged loudly and reported as
`availability: unmeasured` on the owner's surface. Hard Rule 8 is about never
faking a result — this reports the unknown rather than hiding it — and the
founder's "everything we are testing should work" makes a
refuse-on-unmeasurable quota the wrong trade. Named explicitly so a reviewer
can disagree with it deliberately.

**Reads never consult it.** Delete, read, list and status never call it.

### D5a. Where the gate goes, named exactly

`grep` for byte-writing calls across `tinyassets/` returns 20+ modules
(`api/wiki.py` 14, `node_sandbox.py` 10, `api/universe.py` 9,
`universe_tools.py` 8, `soul_edit.py` 6, and a long tail of ledgers, session
stores and vaults). Gating all of them would be ~40 call sites and 40 chances
to miss one, and the misses would be silent — the worst shape available.

The gate goes on the paths a **user can drive volume through**, because that
is where a quota is meaningful and where an owner can act on a refusal:

| Gated | Why |
|---|---|
| run output persistence | the dominant growth path; one run can write MB |
| run-file custody intake | user uploads, already byte-bounded per file (`TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES`) but unbounded in aggregate |
| `write_page` / wiki writes | user-authored content, unbounded in count |
| permanent workspace generations | GiB-scale; already has an admission transaction to extend |
| node sandbox outputs | a code node can write arbitrary bytes |
| conversation-store growth | grows with use, not with an explicit user action |

Not gated: credential vault, session store, subscription state, the auto-ship
and admission ledgers, soul/persona edits, provider process scratch. Each is
bounded by its own schema and measured in KB. A universe does not reach 2 GiB
through bookkeeping rows, and refusing a credential write because a universe
is full would break sign-in to fix storage — the failure would be worse than
the condition.

Consequence stated plainly: the accounted footprint (D5) covers the WHOLE
universe directory, including the ungated writers, so ungated bookkeeping
still counts toward the quota and still shows in the owner's number. It just
cannot be refused. So the quota is never under-reported; it is only
under-enforced on writers that cannot move it. That asymmetry is deliberate,
and it is the right way round: accounting is complete, enforcement is
actionable.

## D6. Tier values in one place

`tinyassets/tiers.py`, a frozen dataclass per tier keyed by the strings
`storage/subscription_state.py` already owns (`TIER_FREE`, `TIER_PAID`):

```python
@dataclass(frozen=True)
class Tier:
    name: str
    seats: int                 # concurrent agent calls
    storage_bytes: int         # cloud footprint quota
    interactive_reserve: int = 1

TIERS = {TIER_FREE: Tier(TIER_FREE, seats=3, storage_bytes=2 * GIB),
         TIER_PAID: Tier(TIER_PAID, seats=8, storage_bytes=50 * GIB)}
```

Not env-driven, so nothing goes in the env-var catalog: a tier value read from
the environment is a per-deployment product definition, and the same free
account has to mean the same thing on the box and in a test. `SEAT_LEASE_SECONDS`,
`SEAT_REFRESH_SECONDS` and `SEAT_WAIT_S` are operational and stay module
constants beside `provider_admission`'s, which are the same kind of number.

An unknown tier string resolves to `free` and logs loudly — never to "no
limit". A missing subscription row already defaults to `TIER_FREE` in
`get_tier(default=TIER_FREE)`, so the fail-safe direction is already
established and this follows it.

### Why free is 3 seats and not the example's 2

Two requirements have to hold together:

- *"a 4-agent village works on free by queueing"* — with `seats = 2` and one
  reserved for interactive, background concurrency is 1. Four agents then run
  strictly one after another: correct, but a serial pipe, and the founder's
  stated purpose is to SEE "some pending waiting on an available seat" while
  others run.
- *"the chat must get a seat fairly / never blocks the owner's chat forever"* —
  guaranteeing this needs a reserved seat, not a priority ordering, because a
  background run may legitimately last hours.

`seats = 3` is the smallest value satisfying both: 2 background (a real queue —
2 run, 2 wait) plus 1 reserved (chat never waits on background). The founder
wrote "e.g. 2", an example rather than a value, and the reasoning is flagged at
the design point for the founder to overrule.

## D7. What deletion leaves behind

Deleting the meters leaves `engine_admissions` holding only **settlement**:
`admit` / `attach_run` / `settle` / `reclassify_read` / `fired_only_reads`.
That machinery is not a budget — it is how the effect boundary records whether
a run touched the far side, which `effectors/__init__.py` reads for reasons
that have nothing to do with usage. It stays, with `write_max` / `total_max` /
`day_max` / `window_s` removed from its signature so no caller can reintroduce
a cap through it.

`DISPATCHES_PER_HOUR` / `BYTES_PER_HOUR` go with the rest: they are per-hour
account counters, which is precisely the category the directive removes. Egress
abuse is then bounded by seats (nothing dispatches without holding one) plus
the per-call and per-run byte caps, which stay. Flagged as the one deletion the
directive lists only by pattern ("grep broadly: … hourly") rather than by name.

`RUN_DAY_LIMIT`'s stated job was to bound "a self-launching chain paced under
the hourly caps". Seats do that structurally: the chain holds at most
`seats - 1` and cannot grow. A rolling-day count was bounding the wrong
quantity — how many times a loop went round, rather than how much it occupied.

## D8. Runaway protection, and what it costs

One test proves the whole claim: two automations that wake each other, run for
a fixed wall-clock period, and assert that (a) concurrent seat holders never
exceeded `seats - reserve`, and (b) an interactive turn arriving at any point
during it acquires a seat within the bounded wait.

This is weaker than the old meters in one honest respect: a ping-pong now runs
**forever** at bounded concurrency, where before it stopped at 20,000 runs a
day. That is the directive's trade — usage is what a universe occupies, not how
many times it acted — and the cost lands on the owner's own provider
subscription, which is theirs to spend (memory
`user-subscription-runs-the-universe`). The bound that matters, "it cannot
affect another user" (memory `the-floor-is-cross-user-only`), is what seats
enforce. Stated so it is a decision rather than an oversight.

## D9. Rejected alternatives

- **Seat at the provider call** — D1: the unit would not be an agent call.
- **In-memory semaphore** — D2: leaks on crash, blind across processes, resets
  to zero on the deploy that recreates the container.
- **Refuse over the seat limit** — the directive's word is "pending". A refusal
  also makes a 4-agent village a user error on a 3-seat account, which it
  is not.
- **Priority queue with no reserve** — cannot bound how long a chat waits,
  because a background run may run for hours by design.
- **Charge scratch to the universe quota** — contradicts
  `storage-permanent-vs-scratch`: a universe would become as big as the
  largest repo it ever checked out.
- **Refuse writes when storage is unmeasurable** — D5: breaks working
  universes for a measurement failure.
- **Extend the existing hourly ledger with a seat count** — a rolling-window
  counter cannot express occupancy. A row that ages out of a window is not a
  seat that was released.


## 2026-09-30 as built: seats per ACCOUNT

The founder superseded every per-universe statement above: one seat pool per
person, shared across all their universes; universe creation stays unlimited.

- **Account.** `universe_seats.account_key` asks `universe_owner.owner_of` (#4139,
  the resolver storage uses) and prefixes it (`account:<id>`). A universe with no
  owner row is `unattributed:<uid>`, its own free pool. The tier is
  `universe_owner.tier_of`; `usage_policy` keeps only the tier table and
  `upgrade_url`. The first draft's `account_for_universe` guessed the owner from
  `universe_acl` / `agent_bindings` / `founder_home` -- a second definition, and an
  inference from adjacent tables -- and is gone.
- **Where seats are taken.** Only at agent calls: the prompt node's executor and
  the chat turn. The prompt node keys on the RUN's universe from its
  `BranchExecutionContext`: a run compiles nodes without a `universe_context` (it
  rides inside the bound provider call), so the first draft's node site, keyed on
  `universe_context`, never took a seat in any real run. The automation and wake
  worker make one non-blocking try per poll BEFORE claiming an attempt
  (`try_acquire`, ticket kept per automation) and give the seat straight back:
  holding it across the run deadlocked (astra round 1) -- the run waits for a
  shared run-pool worker while the pool's workers wait for that account's
  seats. The first draft also held a seat around every `_invoke_graph`, blocking
  a pool thread with no deadline; removed for the same reason. A prompt node
  carries its seat into its call (`carrying`), so a blocking agent call nested
  inside re-enters it.
- **Visible waiting.** A waiting node emits phase `waiting`, which the runs sink
  now records as a `waiting_for_seat` system event (it previously fell through
  to `ran`). A run cancelled while waiting stops waiting and abandons its ticket.
  A waiting chat is its waiter row, tagged with the universe; `get_status` shows
  the owning account `seats` with `chat_waiting`, and the app paints the one
  status line with the Upgrade link inside it.
- **Atomicity.** `_txn` ran `executescript(_SCHEMA)` after `BEGIN IMMEDIATE`;
  `executescript` commits first, so every reap, count and insert ran outside the
  write lock (the WIP's bug too): two acquirers could take the last seat and two
  releases of a lent seat left a depth-0 row holding a seat forever. Schema
  statements now run one by one inside the transaction, on an autocommit
  connection. A release the store refuses is retried by the refresher.
- **Death.** Holder = `process_liveness.owner_token(ledger root)`. Reclaim on
  proven death, or on a lapsed lease with no proof of life; waiters of a dead
  process are dropped at once (a dead waiter ahead of the queue would otherwise
  stall live work with a free seat). Release checks the holder.
- **Settlement.** `engine_admissions.admit` never refuses (a tampered ledger
  records nothing), engine edits are no longer recorded, and rows are pruned
  after two hours. The `settlement_unavailable` refusal the first draft
  introduced in place of the meters is gone with them.

- **Budget and borrowing (astra round 2).** A node's provider budget is
  restamped when its seat is held (`on_seated`), so a seat wait is never charged
  to the call. A queued run (`execute_branch_async`, resume) detaches the
  caller's seat from its copied context; only a blocking version invoke lends it
  (`_lend_seat`).
- **Enclosing deadlines (astra round 3).** A blocking version invoke polled its
  child with a 300 s default, which a child's seat wait counted against. It now
  waits until the child ends, as the blocking definition invoke always has; a
  dead child still ends through run-owner proof.

- **No shared thread waits for a seat.** A prompt node waits for its seat on
  its run's worker thread, so the run pools are now one pair PER ACCOUNT
  (`runs._get_executor(pool_key=run_pool_key(...))`, keyed by the same
  `account_key`); keyless work keeps the old pair. One account's waiting runs
  fill only its own pool, and another account's run is never queued behind
  them. The host-wide memory bound stays `provider_admission`, underneath.

Known and not fixed here, filed as a concern: a timed-out borrower whose parent
released first remembers a stale depth, so it cannot lend the seat on to a
nested blocking call.

## C2 folded obligations

The following unfinished source tasks retain their acceptance detail; the one target change owns them.

### usage-limits-replace-caps

# Tasks: usage-limits-replace-caps

## 1. Build
- [x] 1.1 Rolling-day run meter, ledger index, day refusal (`engine_admissions.py`, `engine_mcp_server.py`).
- [x] 1.2 invoke_branch: remove the depth cap, charge and bind every child, name stack exhaustion, pool-derived bound for blocking version invokes (`graph_compiler.py`, `runs.py`).
- [x] 1.3 Automations: drop the ceiling and cadence floors; charge registration (`automations.py`, `api/automations.py`).
- [x] 1.4 Schedules/subscriptions: drop per-owner counts; floor = tick; charge registration and every triggered run (`scheduler.py`, `api/runtime_ops.py`, `api/runs.py`).
- [x] 1.5 Agent definitions: drop the component count (`custom_agents.py`, `agent_runtime.py`).
- [x] 1.6 Remove `NodeEnqueueBudget` threading; delete the served-recursion concern.

## 2. Prove
- [x] 2.1 Tests through the real ledger, compiled graph and triggered-run path (`tests/test_usage_limits.py`); cap tests rewritten to the usage contract.
- [x] 2.2 Mutation-check each meter site.
- [x] 2.3 gpt-6-astra refute round: cross-user reach and runaway cost (ADAPT; four P1s folded, foreground fail-open kept with reason).
- [ ] 2.4 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.

## 3. Land
- [ ] 3.1 Sync deltas into `openspec/specs/`, archive.


### remove-non-usage-limits

# Tasks: remove-non-usage-limits

## 1. Account-shaped limits (PR 1)
- [ ] 1.1 Any user may create any number of universes; keep only the identity floor (`universe_server.py`, `api/prompts.py`).
- [ ] 1.2 Conversation history is never deleted (`conversation_store.py`, `storage/conversation_run_admissions.py`).
- [ ] 1.3 Storage-shaped caps go — project memory 1 MB, daemon wiki caps + eviction, app-UI-library 4 MiB (`memory/project.py`, `daemon_memory.py`, `custom_agents.py`).
- [ ] 1.4 Structural caps go — `MAX_PENDING`, `MAX_LINEAGE_DEPTH` + CHECK, `MAX_NOT_BEFORE`, consumer-binding 100 cutoff + `LIST_LIMIT` saturation, `MAX_SKILLS` silent drop, `MAX_DEFINITION_BYTES`/`MAX_OPERATIONS_PER_BATCH`.
- [ ] 1.5 Receiver per-sender rate becomes owner-set, default none, no ceiling (`storage/receiver_links.py`, `api/deliveries.py`, `storage/deliveries.py`).

## 2. Run and host bounds (PR 2)
- [ ] 2.1 Per-run and per-node counters go; per-call payload bounds stay (`effectors/__init__.py`, `node_sandbox.py`).
- [ ] 2.2 No recursion ceiling and no validated range; author retry policy honoured (`runs.py`, `graph_compiler.py`).
- [ ] 2.3 Wall clocks that capped a turn go; per-call transport timeouts audited and kept (`universe_intelligence.py`, `automations.py`).
- [ ] 2.4 Host safety waits — `provider_admission`, universe-tool jail slots, nested invoke, workspace job lock.
- [ ] 2.5 Inbound keeps per-token anti-flood only; per-universe rate/in-flight go and runs queue (`webhook_inbound.py`). Voice per-user windows go.
- [ ] 2.6 Serving-binding hourly/token/cost windows go; dark `_HTTP_ACTION_CAP` and market `_MAX_FANOUT` deleted after confirming no live caller.

  HTTP portion completed by PR #4476: new HTTP grants are uncapped, legacy
  `http_requests` caps are cleared on ledger open, and listings return `null`.
  Preservation, concurrent-open, and initialized-open locking regressions are
  covered; as-built contract is in `http-connections-and-outbound-authority`.
  Serving-binding windows and market fanout remain in this task.

## 3. Text, docs, specs (PR 3)
- [ ] 3.1 `get_status` `resource_usage` publishes storage and seats only (`api/resource_usage.py`).
- [ ] 3.2 Served handbook, connector docs and compiler guidance describe two dimensions (`engine_mcp_server.py`, `universe_server.py`, `graph_compiler.py`).
- [ ] 3.3 Website plan table, onboarding and legal text (`WebSite/site-react/app/{fine-print,start,legal}`), per `website-editing`.
- [ ] 3.4 Spec deltas + `environment-variables.md` default fix + drop the `TINYASSETS_USAGE_ENFORCEMENT` host-action row.

## 4. Prove
- [ ] 4.1 Limit-gone tests replace limit tests; new waiting behaviour covered; mutation-check each removed gate.
- [ ] 4.2 `gpt-6-astra` refute per PR: did removing X open a cross-user or host-safety hole.


### account-storage-quota

# Tasks: account-storage-quota

Design approval gates task 2 onward. Task 1 is read-only and gates the numbers.

## 1. Ground truth

- [ ] 1.1 Measure what each test account holds today on production, per store in
      D3, read-only. Record the numbers here. If either account is above 1 GiB,
      return Q1 to the founder before enforcing.

      **Result, 2026-09-30 ~18:10Z, production, read-only
      (`scripts/droplet.py ssh` running a `mode=ro` walk):**
      - 3 universes, 3 `founder_home` rows, 0 ambiguous. The backfill binds all
        3, and **0 stay unattributed**.
      - Account `5ac36c1b` (hash) home: **2,898.8 MiB** of non-runtime files.
        That is **over 1 GiB, so STOP; enforcement is held.** 2,800.2 MiB of it
        is leaked `.workspace-staging` (334 directories, 2026-09-09 to 09-21).
        **Resolved 2026-09-30:** leak fixed in #4143, and the boot sweep removed
        all 334 (2,936,213,247 bytes) at 20:00:54Z. Staging is excluded from
        user storage (founder decision).
        Without staging it is about 98 MiB, of which 93.6 MiB is
        `.credentials/codex`.
      - The two other accounts hold 0.3 MiB each.
      - Shared root stores, totals across all accounts: `.runs.db` 70.5 MiB,
        `.langgraph_runs.db` 94.5 MiB, commons `wiki` 9.5 MiB; custody,
        daemon wikis and project memory are 0.

## 2. Shape

- [ ] 2.1 `usage_policy`: storage in GiB (free 2, paid 20), with
      `TINYASSETS_{FREE,PAID}_STORAGE_GIB`. Delete the MB variables,
      `_universe_quota_kwargs` and the pool's universe-quota predicate. Update
      `environment-variables.md`.
- [x] 2.2 `universe_owner.py`: add the `universe_owner` table, written in the creation
      transaction(s), a backfill from stored bindings only (D2), `owner_of`,
      `tier_of` and `usage`. Coordinate with the seats-per-account lane so there
      is a single resolver.
- [ ] 2.3 `storage_accounting.py`: the store registry, `measurements`, `pending`
      and `seq`; `reserve` / `commit` / `release` / `touch`; the
      fresh-before-refuse rule; the bounded unmeasured allowance; and the
      single-flight sweep.
- [ ] 2.4 Attribution queries for the shared stores (run records, checkpoints,
      uploads, branches, commons) and their idempotent indexes. Add the
      completeness guard test over a populated data directory.

## 3. Enforcement

- [ ] 3.1 Workspaces (D6): fitted reservation with credit for the replaced
      generation, measure-before-publish, `publish_generation` requires a
      reservation, and lease-release measurement plus crash-window
      reconciliation by `lease_id`.
- [ ] 3.2 Gate files, project memory, UI library, pages (universe, commons and
      daemon wiki), uploads (including cross-owner delivery charged to the
      receiver) and branch/version writes (D7). Add the at-quota check on
      write-capable jail calls. Call `touch` on each delete path.
- [ ] 3.3 Build the refusal record and message (D8) through `upgrade_sentence`,
      with no link on the top tier. Prove that reads, the vault, sessions, chat
      and run records are never refused.

## 4. Prove and land

- [ ] 4.1 Tests:
      - An empty free account creates a small workspace.
      - Delete, then retry, succeeds without a `touch`.
      - Concurrent admissions can't overshoot.
      - A write that lands mid-scan is still counted.
      - The unmeasured allowance is bounded.
      - A shared universe charges only its owner.
      - One account's bytes are summed across its universes.

      Mutation-check each gate. Run the concurrency tests in the Linux oracle
      container.
- [ ] 4.2 Run a gpt-6-astra refute via `peer-agents` from this worktree, at most
      3 rounds. Then deploy, confirm with
      `python scripts/deployed_sha.py --assert-contains <sha>`, and run a live
      `ui-test` on a free account: a refused write that shows the Upgrade link,
      then delete and retry.
- [ ] 4.3 Sync the deltas into `openspec/specs/`, archive this change, and
      record the usage-limit decision as an ADR in `docs/decisions/` (the
      founder accepts direction-level ADRs).


### consolidate-platform-resource-policy

## 1. Shape and existing truth

- [x] 1.1 Diagnose the live ten-start refusal, record owner acceptance boundary and independent review, and adapt the candidate to reuse existing stores without migration or new authority.
- [x] 1.2 Verify/sync/archive landed run-usage-budgets bookkeeping, preserving its actual current behavior and limitations.

## 2. Bounded platform correction

- [x] 2.1 Remove both workspace jobs-count refusal gates and obsolete parameters; retain observations and add regression tests proving more than ten operations while byte/storage/lock controls still refuse correctly.
- [x] 2.2 Add non-mutating owner-scoped resource-status observations with missing/corrupt/symlink/privacy/coverage tests; do not claim global capacity or total storage where unmeasured.

## 3. Ship and prove

- [x] 3.1 Run focused Windows and Linux-oracle tests, rebuild plugin parity, obtain independent exact-head review and required CI, then deploy with authenticated canary and SHA containment.
- [ ] 3.2 Coordinate rendered app-owned continuation/acceptance, record exact scope and organic follow-up, sync shipped specs and archive this bounded correction; keep wider Patches open for remaining platform issues and owner acceptance.

Local evidence (2026-09-08, Windows, uncommitted candidate):
`python -m pytest -q tests/test_workspace_pool.py tests/test_workspace_effector.py tests/test_run_usage_budgets.py tests/test_engine_mcp_hardening.py tests/test_engine_mcp_server.py tests/test_resource_usage_status.py tests/test_api_status.py tests/test_get_status_primitive.py`
reported 390 passed, 9 skipped initially; after the round-1 WAL correction and
three added regressions it reported 393 passed, 9 skipped. This does not satisfy
the Linux gate.

The local oracle could not start because Docker Desktop crashed initializing its
inference socket, before any test execution. Commander authorized the existing
Linux CI alternative without weakening tests or repairing the host. The relevant
workspace/status suites are not in `.github/heavy-test-files.txt` or quarantine;
their POSIX/symlink tests do not require bubblewrap. `tests.yml` workflow_dispatch
checks out the selected ref on Ubuntu/Python 3.11 and uploads JUnit. Use pinned
baseline and candidate refs, verify the executed SHAs and compare relevant
test outcomes/skips. Required PR CI remains an additional landing gate. This is
Linux evidence for this change, not a claim that sandbox-jail tests ran.

Delivery evidence: bounded proof record (archived in git).
Linux PR checkout 18443d16050715cfa352e4b04b89704d509909cc has the identical
complete tree to approved head 79a4f765; its relevant suite passed 400 tests
with two unchanged Windows-only skips. Deploy 34305023434 passed both
authenticated gates for merge 0eb1388f at 2026-09-09 02:55 UTC.

Task 3.2 is intentionally still open. Shipped specs are synchronized, but the
owner retains live testing and acceptance. Commander explicitly relayed that no
new app test message or workflow run is authorized without a fresh user request.
Do not archive this as fully accepted or treat technical readiness as completion
of broader Patches.

Update September 8, 21:51 PDT: the rendered app's 21:39 report now supplies
organic owner-initiated evidence of 12 workspace starts without the old limit
and contention/recovery success. It also reports explicit discard failure and
four other gaps, so complete cleanup/whole-goal acceptance is not established.
The newer owner directive in `goal-completion-contract.md` adds all five gaps
and authorizes the ordinary retest loop, with explicit agent confirmation as
the completion gate. No extra test was sent during the read-only inspection.
