# Design: control-plane scheduler (S8b)

## Inventory (task 1)

Scan: every `while`/`for`/`async for` in `tinyassets/` whose body calls
`sleep`/`wait`, plus every call that schedules a callback later
(`threading.Timer`, `call_later`, `call_at`); the n-th site in one function is
its own key. 67 sites on `origin/main` `5838d535` (2026-10-02). The authoritative list, with a note per site, is
`tests/control_plane_timer_inventory.py`; `tests/test_control_plane_inventory.py`
holds it in lockstep with the code.

| Class | Count | Sites |
|---|---|---|
| **control_plane** (always-on duty of the execution owner) | 7 | assigned-queue consumer poll (the owner tick: automations + triggers); served-budget/run-file/delivery reconciler; run-owner watcher; engine-MCP supervisor; workspace sweeper; workspace-staging sweeper; account-seat refresh |
| **call_scoped** (bounded wait inside one call; ends with it) | 56 | lock acquisitions, bounded retries, slot/admission waits, WAL-switch retries, stream polls, the per-call tool-jail watcher, the per-batch automation lease refresher |
| **delete** (not in the target shape) | 3 | `host_pool` bid poller + heartbeat (no production importer), `claimed_branch_execution` heartbeat (fantasy_daemon only) |
| **client** (owner's device) | 1 | desktop tray menu refresh |
| **box** (forbidden) | 0 | — |

Residuals noted, not fixed here: the owner tick still writes a per-universe
heartbeat file and reads `.pause` inside each universe directory every poll.
Both are platform state living in user-content space; the cutover (#4262) moves
them to `.platform/cc-<ulid>/` (D8a row 2). Under the box, those two touches
would be box writes, so the cutover must land before S11.

## Decisions

**D1. User-declared schedules stay in `.automations.db`.** It is already a
platform-state trigger table with a `(automation_id, due_at)` fence, overlap
policy and owner authority re-derived per run. Copying its rows into a second
table would create two authorities for one fact. "One trigger table run by the
execution owner" is realised as one owner tick over both stores, under one
lease check. Activities' schedules (harness D2a, `origin_kind='schedule'`)
point at an automation firing and need no change.

**D2. The control-plane trigger table holds platform-originated triggers.**
Kind `proactive` today. The fire ledger is separate from the trigger row so a
claim and its outcome are durable and inspectable; the trigger advances in the
same transaction as the claim.

**D3. At most once, never replayed.** A fire is claimed (fence row inserted,
trigger advanced) before its handler runs. A process that dies between claim
and handler leaves a `claimed` row, which the next owner settles
`lost_on_restart`. A claim is the previous owner's when its generation is
older or its `owner_incarnation` (a per-scheduler-instance id) is not the
current one, so a claim made in the same second as the restart still settles.
This is the D2/D4 rule for an operation in flight across a crash
("holds instead of re-issuing"). For a proactive wake a lost fire costs one
window; a replay would cost a second turn the owner did not ask for.
The claim writes `last_run_id = 'claim:<due_at iso>'` as an unresolved barrier;
finishing replaces it with the started run id or clears it on decline/failure.
Restart settlement clears only barriers naming claims it settles lost, in the
same transaction, so a failed finish cannot release single flight in its owner.

**D3a. Decisions are fenced by revision.** Every write that changes when a
trigger is owed (engagement, enable, override, a claim) bumps `revision`; the
claim compares it. An owner message committed between the tick's read and its
claim voids the claim, and the next tick honours the idle wait.

**D3b. Active hours bound when a wake runs.** A fire owed at 20:00 and noticed
at 23:00 waits for 08:00; the missed window is counted as coalesced. The
collapse walk applies to the first fire too, so a trigger first served late
fires once. Ambiguous fall-back openings choose the earliest instant at or
beyond the instant reached, and the collapse walk stops without strict forward
progress. The opening search starts on the current local date, validates both
folds by a UTC round trip, and uses the gap end when the opening does not exist.
Each claim rechecks the clock, due instant and active hours inside `BEGIN
IMMEDIATE` after acquiring the writer lock, so lock waits cannot admit a late
wake; explicit tick timestamps remain fixed for deterministic tests.

**D4. Coalescing.** Clock triggers: a due fire waits while the previous fire's
run is live (single flight, run status from the runs store). Windows it
outlives collapse onto the latest grid point inside active hours; nights
outside active hours are not counted. The count is stored per trigger
(`coalesced_total`) and in each fire row (`collapsed`).
*Event subscriptions are not changed:* `owner_message` already coalesces to one
pending wake; `run_completed`, `pending_request_answered` and `app_event`
wakes stay one per occurrence, because each carries a distinct fact the woken
agent needs, and coalescing them would drop facts silently (Hard Rule 8). They
remain deduplicated per occurrence and serialised per agent by the agent lease.
Refute round 1 (DISAGREE_CONCERN) argued for one pending wake consuming a
batch or cursor of durable facts. That changes the woken branch's input
contract (`inputs.event` becomes a batch) — a public-surface change to
user-built loops. **Decided (lead, 2026-10-02): no batching.** Each event keeps
its own input, because user-built workflows depend on it; coalescing applies
only to data-free timer wakes.

**D5. The cadence is a policy object.** `CadencePolicy` fields: engaged period
(4 h), cooling threshold (7 d) and period (24 h), dormant threshold (30 d) and
period (7 d), idle wait (30 min), active hours (08:00–22:00, owner's clock).
Decay state is a pure function of days since the owner's last interaction; the
due instant is a pure function of the trigger row and `now`, so two owners
either side of a restart compute the same `due_at`. Resolution order: owner
override on the row → `TINYASSETS_PROACTIVE_CADENCE` → `DEFAULT_POLICY`. A
malformed deploy value raises rather than running everyone on an unchosen
cadence. **Founder confirmation of the decay values is pending**; the
mechanism ships with them as one default.

**D6. Engagement is the owner's message.** `automation_events.emit_owner_message`
(the one place both converse paths announce a stored owner message) calls
`note_owner_engagement`, which updates only rows whose owner is the verified
sender; an older stamp never replaces a newer one. It never raises into the
turn and creates no database on a root with no triggers.

**D6a. Keyed by agent.** One trigger per `(kind, command_center_id,
agent_id)` (harness §4.18), `main` seeded. The owner's message engages every
agent of that command center.

**D7. Wake through one seam.** `register_wake_handler(kind, fn)`; `fn(base,
WakeRequest) -> WakeResult(run_id | declined)`. A second registration for a
kind raises (two consumers racing one kind is the double fire this prevents).
A declined fire spends its window (≤1 proactive turn per window, harness §4.5)
and records its reason. Gates that need a command center's own state — paused,
an activity in progress, no compute — are the handler's (agreed with harness
D3, 2026-10-02: `declined="paused"|"busy"|"no_compute"`); the scheduler never
reads them from the command center. Under S11 the handler binds the box and calls
`ensure_awake`; the scheduler is unchanged.

**D8. Lease seam.** The tick checks `held()` before doing anything and
`check()` immediately before each claim; every fire row stores the
generation. Lost-claim settlement runs once per generation, before the first
fire. `OwnerLease.proof` is the plaintext secret minted per acquisition and
`verify_lease_proof(generation, proof)` checks it against the installed
authority (which stores only the hash): the S6 broker's `FENCE{G,
lease_proof}` needs it, because same image and uid are not ownership. S8a
supplies the fenced lease; until then `SingleProcessLease` is
correct because exactly one process serves (`agent_turn_boot` pins the same
invariant).

**D8a. Liveness reads the root runs row only.** `run_is_live` reads
`runs.status` from the root `.runs.db`, read-only; `runs.get_run` would also
resolve a queued run's workspace wait from the command center's own
`.runs.db` (refute round 1, P1). The audit test covers that path.
A missing store or runs table means no live run; other SQLite failures warn and
defer the trigger because liveness is unknown. Each trigger checks the lease
before reading its generation, settles lost claims for a changed generation,
and carries that generation in both the claim and wake request.
An unresolved `claim:` barrier reports waiting without consulting run liveness;
after settlement the scheduler re-reads that barrier so a restarted owner can
fire the next window in the same tick.

**D9. Harness `settings.yaml` vs platform state.** Harness §4.14 lists
"research cadence, idle period, active hours" in the agent-editable
`settings.yaml`. D7/D8a forbid scheduling from user content. The owner's
cadence therefore lives on the trigger row (platform state), set through an
owner-checked store call (`set_cadence_override`, `set_enabled`); harness D3's
Scheduled view writes it there. The agent may *propose* a cadence; it does not
set one by editing a file the scheduler reads.

## Risks

- **Handler latency blocks the tick.** A handler must start work and return;
  documented on `wake.py`. D3 starts an activity run, which is asynchronous.
- **Decay values unconfirmed.** Mitigated by D5: one default, flip by config.
- **Lost fire on crash.** Accepted (D3); recorded and visible in metrics.
