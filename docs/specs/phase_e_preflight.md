---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-14. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase E Pre-Flight — Tier-aware DaemonController + BranchTask queue

Planner draft, 2026-04-14. For dev handoff on task #5's follow-up (Phase E rollout row).

Phase E is the first UX surface where a user can see and steer a daemon queue. Phase D was infrastructure: the fantasy loop becomes a compiled BranchDefinition behind a flag. Phase E is the consequence: if the daemon runs a Branch, any Branch, then "which Branch next?" becomes a scheduling question, and scheduling is user-facing. This phase is where the memo's `tier × bid` priority function shows up in code, where `branch_tasks.json` starts persisting queued work, and where the MCP surface gains a queue-inspection action the user can point at when someone asks "what is your daemon doing?"

## 1. Source material

- `docs/exec-plans/daemon_task_economy_rollout.md` §Phase E (lines 128-152).
- `docs/planning/daemon_task_economy.md` §4 (full) — tiers, priority function, UX check, data model asks (§4.6).
- `docs/planning/daemon_task_economy.md` §3.2 — BranchTask shape reference.
- `docs/specs/phase_d_preflight.md` — structural precedent and resolved patterns (domain registry, feature flag, boundary invariants).
- `fantasy_author/__main__.py:171-249` (DaemonController init), `:250+` (start), `:455+` (`_run_graph`), `:580-620` (stream loop + stop/switch handling).
- `tinyassets/producers/__init__.py` — active `TaskProducer` registry + `register` + `run_producers` dispatcher. Phase C.3/C.4 precedent.
- `domains/fantasy_author/producers.py` — three registered producers: `SeedProducer` / `FantasyAuthorialProducer` / `UserRequestProducer` with `origin` values `seed` / `fantasy_authorial` / `user_request`.
- `tinyassets/universe_server.py:1303-1400` — `_action_submit_request` writes `<universe>/requests.json`.
- `tinyassets/work_targets.py:383+` — `REQUESTS_FILENAME` + materialization path. Phase B's wiring into the producer loop.
- Phase D landing commit `c5f29bb`, follow-up concerns at STATUS.md 2026-04-14.

## 2. What exists vs. what Phase E adds

Before designing the new surface, pin what's already live. Phase E's job is to **compose existing pieces into a tier-aware dispatcher**, not rebuild them.

**Already live:**
- TaskProducer protocol + module-level registry (`tinyassets/producers/__init__.py`).
- Three fantasy producers registered with `origin` stamping.
- `submit_request` writing to `requests.json` + `UserRequestProducer` materializing pending entries into WorkTargets.
- WorkTarget `origin` field populated per producer.
- Phase C.4 feature flag `TINYASSETS_PRODUCER_INTERFACE` (default on) as the revert lever for the producer pipeline.
- Phase D feature flag `TINYASSETS_UNIFIED_EXECUTION` (default off) gates whether the fantasy graph runs directly or through `compile_branch`.
- `DaemonController.start()` → `_run_graph(universe_id)` → `compiled.stream(initial_state, config)` stream loop. One graph invocation per `start()`; the "forever" behavior comes from the graph's own `universe_cycle → foundation_priority_review` conditional edge.

**Phase E adds:**
- A `BranchTask` durable record type (separate from `WorkTarget`; BranchTasks wrap a `(branch_def_id, universe_id, inputs, trigger_source, priority_weight)` execution intent; WorkTargets remain the *content* being worked on).
- A per-universe `branch_tasks.json` durable queue.
- `tinyassets/dispatcher.py` — the tier-aware selection function. Walks registered producers + reads `branch_tasks.json` + applies §4.3 priority math + returns "next task."
- `tinyassets/branch_tasks.py` — queue plumbing (read, write, claim, mark-done, mark-cancelled). File-locked on `branch_tasks.json`.
- A new `origin` value `host_request` (separate from `user_request`) so the dispatcher can distinguish host-submitted from externally-submitted work — §4.6 calls this out.
- An MCP surface for inspecting + steering the queue (a new action on the existing `universe` tool, OR a new `queue` tool — §4.3 decision).
- Tier on/off switches in universe config. First rollout: `accept_external_requests`, `accept_goal_pool`, `accept_paid_bids`, `allow_opportunistic` land as config keys, but §Phase F/G tiers are stubs — Phase E only *wires* them and enforces `host_request` + `owner_queued`.

**Explicitly NOT in Phase E:**
- Goal-pool subscription (Phase F).
- NodeBid executor (Phase G).
- Bid-weighted scoring within the paid tier (Phase G). Phase E's priority function uses tier order + recency decay + user boost only; `bid_term` coefficient is zero until Phase G lands.
- Host-dashboard UI for tier switches (Phase H). Phase E ships config-file steering only; MCP inspection tools let the user *see* what's queued, and edits happen by hand-editing `branch_tasks.json` OR by using existing MCP actions that land new entries.
- Cross-universe daemon identity. Each universe still has its own daemon process, its own queue, its own config.

## 3. Risk map

| # | Risk | Blast radius | Reversible? | Mitigation |
|---|------|--------------|-------------|------------|
| R1 | Dispatcher replaces the fantasy graph's internal `foundation_priority_review → authorial_priority_review → dispatch_execution` selection with a tier-aware outer loop. If the outer loop is wrong, the fantasy daemon starts dispatching work the review gates would have blocked | Catastrophic — violates foundation review's hard-block invariant (unsynthesized uploads must not be skipped) | Yes (flag off) | Tier 1+2+3 selection in the dispatcher runs BEFORE the graph invocation, but cannot bypass foundation review. The Branch that runs is still the fantasy universe-cycle Branch (Phase D wrapper), and the wrapper's internal graph still runs its review gates. Dispatcher selects WHICH BranchTask to hand in; it does NOT replace the review gates. Spell this out in §4.1 clearly. |
| R2 | Two sources of "what's next" now coexist: the producer registry (Phase C, returns WorkTargets) and `branch_tasks.json` (Phase E, holds BranchTasks). Overlap ambiguity: is a `user_request`-origin WorkTarget materialized by `UserRequestProducer` the SAME thing as a `host_request` BranchTask queued in `branch_tasks.json`, or two different things? | High — if they're the same thing, double-counting. If they're different, unclear to user which channel to use. Pressure to collapse the distinction | Yes | §4 nails down: WorkTargets are the *content* unit (what work); BranchTasks are the *execution* unit (which branch + which inputs, referencing the WorkTarget via `inputs.work_target_ref`). A `user_request` WorkTarget + a `host_request` BranchTask wrapping it are distinct and legitimate: the WorkTarget says "write scene-3 with this direction"; the BranchTask says "run the fantasy universe-cycle Branch against universe-X with work_target_id=Y". One-to-many: one WorkTarget → zero or more BranchTasks (retry re-runs the same content). |
| R3 | Queue plumbing writes `branch_tasks.json` with sibling-file semantics (alongside `work_targets.json`). Concurrent `submit_request` + daemon-claim + daemon-mark-done can race — read-modify-write on the same JSON file | Medium — lost queue entries, double-claims, stuck-in-running rows | Yes | File lock on `branch_tasks.json` via `fcntl`/`msvcrt` cross-platform lock (same pattern as `work_targets.json`). Every read+write is atomic under the lock. Race tests explicitly exercised (see stress-test scenario 9.1 for `requests.json` as precedent). |
| R4 | The memo's `host_request` tier doesn't exist today — everything routes through `user_request` (MCP `submit_request`) regardless of who submitted. Distinguishing host-vs-user submitter requires a source-identity signal at the submission boundary | Medium — without the distinction, §4.3's "host_request can't be starved" invariant is unenforceable; all external submissions sit in the same tier | Partial (adding identity signal is reversible but changes the submit_request contract) | `submit_request` already captures `os.environ.get("UNIVERSE_SERVER_USER", "anonymous")` at `tinyassets/universe_server.py:1346`. Phase E dispatcher classifies: `source == "host"` → `host_request` origin; anything else → `user_request`. No schema change to submit_request; the `origin` stamp happens at the producer layer when materializing into BranchTasks. Risk reduces to "what is the right test for 'host'?" — answer: `source == os.environ.get("UNIVERSE_SERVER_HOST_USER", "host")` to allow host to customize the identity string. Default "host". |
| R5 | Priority function §4.3 has ~6 terms. First implementation is tempted to ship all of them. Two-thirds are deferred-phase stubs (bid_term = Phase G; goal_affinity = Phase F) | Medium — over-shipping invites bugs in untested code paths; under-shipping invites rework when later phases arrive | Yes (deleted code is free) | Phase E implements ONLY: `tier_weight[trigger_source]` + `recency_decay(queued_at)` + `user_boost(priority_weight)`. `bid_term`, `goal_affinity`, `cost_penalty` land as no-op stub functions returning 0 (or 1 for cost_penalty's scale factor) so later phases fill them in without re-shaping the `score()` function. Tests pin: Phase E score is deterministic given tier + age + boost; bid/goal-affinity inputs are ignored. |
| R6 | Interaction with Phase D flag. If `TINYASSETS_UNIFIED_EXECUTION=off`, the fantasy graph runs directly via `build_universe_graph()` — dispatcher has nothing to dispatch TO because there's no `execute_branch_async` call involved | High — half the landed Phase E surface silently inert under the default flag state | Yes (flag matrix is explicit) | Phase E respects Phase D's flag. Under flag-off, dispatcher still reads `branch_tasks.json` and produces a "next BranchTask" decision, but the fantasy daemon's outer loop ignores it and continues the direct-graph path. Dispatcher output is observable via the new MCP inspect action — so a user can see "what the daemon WOULD pick" even with flag off. Under flag-on, dispatcher output drives `_run_graph`'s choice of which Branch + inputs to invoke. Document this as "dispatcher is always live as a reader; only becomes a writer when flag-on." |
| R7 | BranchTask queue starts empty. First-boot behavior: daemon has nothing in `branch_tasks.json`, no opportunistic tier enabled in Phase E, `host_request` tier empty. What does the dispatcher return? | Low — first-boot UX is a learnable state, but silent "daemon doing nothing" is a confusion | Yes | Dispatcher returns `None` and the daemon falls back to the existing fantasy graph behavior (which selects its own WorkTarget via foundation/authorial review). Phase E does NOT break the "leave daemon on → makes progress" default. The existing producers continue to emit WorkTargets; the graph's `authorial_priority_review` still picks one. Phase E layers a queue ON TOP, not instead of. Document this fallback explicitly in §4.1. |
| R8 | `branch_tasks.json` schema is new. Future-phase fields (bid, goal_id, required_llm_type) are memo-listed but not in Phase E. If the v1 schema doesn't leave room, Phase F/G either has to migrate or ship with ugly sidecar fields | Low-medium — v1 schema stability matters | Yes (JSON is schema-flexible; migration is `{new field: default}`) | Pre-reserve field names in the v1 schema with default/empty values: `bid: 0`, `goal_id: ""`, `required_llm_type: ""`, `evidence_url: ""`. No code reads them in Phase E; they serialize empty. Phase F/G wire them in. Cheap insurance against "oh we forgot a field" migrations. |
| R9 | The existing producer pipeline (Phase C.3/C.4) currently runs inside `foundation_priority_review`/`authorial_priority_review` gates per Phase C.5 wiring. Phase E dispatcher runs OUTSIDE the graph. If the dispatcher also calls producers, producers run twice per cycle — once in the dispatcher (to populate `branch_tasks.json`) and once in the graph (to pick a WorkTarget). Double-execution = duplicate work + test pollution | Medium — visible as duplicate activity.log entries + possibly duplicate WorkTarget upserts | Yes | Dispatcher does NOT call producers. Producers remain the source of WorkTargets, called inside the graph's review gates as today. Dispatcher reads `branch_tasks.json` only. `branch_tasks.json` is populated by explicit queue-writing actions (`submit_request`, future host/pool paths). WorkTarget materialization and BranchTask queueing are separate flows, even when a `user_request` WorkTarget triggers a `host_request` BranchTask — the trigger is explicit and happens at submission time, not by producer scan. |
| R10 | Stop/pause semantics under Phase E. Today, `_stop_event` interrupts the stream loop. With a tier-aware outer loop, stop needs to interrupt at dispatcher-pick boundaries AND inside the invoked Branch's execution | Low (stream-loop stop still works for the inside) | Yes | Dispatcher inherits Phase D's pause/stop semantics: checks `_stop_event` at dispatch boundaries. §4.3 invariant mirrors Phase D §4.10 — stop granularity under the new outer loop is "between BranchTask invocations," same blast radius as under flag-on Phase D. Document. |
| R11 | Tier on/off config flags written but unused in Phase E (Phase F/G turn them on). If a user reads their config and sees `accept_goal_pool: false` they may reasonably expect pool BranchTasks to be silently ignored — but in Phase E there IS no pool path, so the flag does nothing and the user can't tell | Low — documentation issue, not a correctness issue | Yes | Config loader logs one INFO line at startup listing which tier flags are live vs stubbed in the current build. MCP `universe inspect` response includes `tier_status: {host_request: "live", user_request: "live", goal_pool: "stubbed (Phase F)", paid_bids: "stubbed (Phase G)", opportunistic: "stubbed"}`. Self-documenting. |

### Reversibility summary

Everything is reversible by leaving Phase D flag off. If `TINYASSETS_UNIFIED_EXECUTION=off`, Phase E's dispatcher becomes a pure-read observatory (MCP inspect action shows queue state) but doesn't alter fantasy daemon behavior. The queue plumbing lands inert until Phase D's flag flips. This means Phase E can land safely before Phase D's flag default flips — a staged rollout where Phase E is observable before it's load-bearing.

Secondary safety net: `TINYASSETS_DISPATCHER_ENABLED=off` flag on Phase E itself (default on when Phase D flag is on, but explicitly togglable). Under off, the dispatcher short-circuits and returns the equivalent of "no BranchTask queued" regardless of `branch_tasks.json` contents. Revert lever independent of Phase D.

## 4. Implementation contract

Scope boundaries, signatures, invariants, and non-goals. Dev fills in HOW.

### 4.1 Deliverables

1. **`tinyassets/branch_tasks.py` — queue plumbing.** Pure-Python file-backed queue. `BranchTask` dataclass with the memo §3.2 shape PLUS reserved fields for future phases (see §R8). Exports:
   - `BranchTask` dataclass — fields: `branch_task_id: str` (ulid, unique), `branch_def_id: str`, `universe_id: str`, `inputs: dict` (may contain `work_target_ref`), `trigger_source: str` (one of `host_request`, `user_request`, `owner_queued`, `goal_pool`, `opportunistic`), `priority_weight: float = 0.0`, `queued_at: str` (ISO8601), `claimed_by: str = ""`, `status: str` (`pending`, `running`, `succeeded`, `failed`, `cancelled`), `bid: float = 0.0` (reserved), `goal_id: str = ""` (reserved), `required_llm_type: str = ""` (reserved), `evidence_url: str = ""` (reserved).
   - `queue_path(universe_path: Path) -> Path` — returns `universe_path / "branch_tasks.json"`.
   - `read_queue(universe_path) -> list[BranchTask]` — file-locked read. Returns `[]` on missing file. Raises on unreadable JSON (loud-fail per Hard Rule 8; no silent `[]` fallback).
   - `append_task(universe_path, task: BranchTask) -> None` — file-locked append.
   - `claim_task(universe_path, task_id, claimer) -> BranchTask | None` — file-locked claim; returns the claimed task or `None` if already claimed / missing.
   - `mark_status(universe_path, task_id, *, status, error="") -> None` — file-locked status update. Valid transitions: `pending → running → {succeeded, failed, cancelled}`; `pending → cancelled` allowed.
   - File lock: cross-platform (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere). **Note:** `tinyassets/work_targets.py` does NOT currently use file locking — it relies on single-daemon discipline, which stress-test scenario 9.1 already flags as a gap. Phase E introduces the locking primitive here rather than inheriting a non-existent pattern. Acquire the lock on a sidecar `.lock` file (not on `branch_tasks.json` itself — opening the data file with `r+` for a lock serializes with the JSON-read path which is racy on Windows). Helper module `tinyassets/_file_lock.py` if inlined use ends up >30 lines; otherwise keep the lock helper private to `branch_tasks.py`. This is the first locking introduction in the codebase — reviewer should cross-check the implementation directly.

2. **`tinyassets/dispatcher.py` — tier-aware selection.** Stateless function + module-level config. Exports:
   - `score_task(task: BranchTask, *, now_iso: str, config: DispatcherConfig) -> float` — applies the Phase E subset of §4.3 priority math (tier_weight + recency_decay + user_boost; other terms return 0). Deterministic.
   - `select_next_task(universe_path, *, config: DispatcherConfig) -> BranchTask | None` — reads `branch_tasks.json`, filters to `status == "pending"` and tier-allowed by config, scores, returns highest-scored task. Returns `None` if none match.
   - **Polling cadence (pinned):** `select_next_task` is called exactly twice per `_run_graph` invocation — once at daemon startup (before the first graph compile) and once at each cycle boundary (inside the outer `compiled.stream` loop, between wrapper returns and the next iteration). No internal timer thread. No continuous polling loop. The dispatcher is event-driven on graph-cycle completion, not wall-clock-driven. Rationale: avoids a second thread contending for the file lock; keeps the dispatcher's observability tied to natural execution boundaries; matches the existing `_run_graph` stream loop shape from Phase D. If a user queues work mid-cycle, they observe their task pick up at the next cycle boundary — typical latency = one cycle (seconds to minutes depending on inner-phase work). That's acceptable for v1 and documented in §4.10.
   - `DispatcherConfig` dataclass — tier on/off flags (`accept_external_requests: bool = True`, `accept_goal_pool: bool = False`, `accept_paid_bids: bool = False`, `allow_opportunistic: bool = False`), tier weights (`tier_weights: dict[str, float]` with Phase E defaults: host_request=100, owner_queued=80, user_request=60, goal_pool=40, opportunistic=10), recency half-life (`recency_half_life_seconds: float = 86400`), and reserved coefficients (`bid_coefficient: float = 0.0`, `goal_affinity_coefficient: float = 0.0`, `cost_penalty_coefficient: float = 0.0`).
   - `load_dispatcher_config(universe_path) -> DispatcherConfig` — reads `<universe>/dispatcher_config.yaml` with defaults if missing.

3. **Submission path integration — new `host_request` origin.** `tinyassets/universe_server.py:_action_submit_request` extended to queue a BranchTask at submission time (in addition to, not instead of, writing `requests.json`). Source identity logic (R4): if `source == <HOST_USER env>`, stamp `trigger_source=host_request`; else `user_request`. The BranchTask wraps `inputs.work_target_ref = None` initially; `UserRequestProducer` materializes the request into a WorkTarget on the next producer cycle and back-fills `work_target_ref` via a separate update path, OR (simpler): `submit_request` both materializes the WorkTarget AND queues the BranchTask in one operation. Dev picks the simpler shape; preflight doesn't require the split.

4. **DaemonController outer-loop extension.** `fantasy_author/__main__.py:_run_graph` gains a dispatcher-read at startup AND at the graph's natural cycle boundary (when the outer `compiled.stream` loop iterates between cycle completions). Flag matrix:
   - **Phase D flag off + Phase E flag off:** existing direct-graph path. No dispatcher read.
   - **Phase D flag off + Phase E flag on:** existing direct-graph path. Dispatcher reads `branch_tasks.json` at cycle boundaries and logs its "would-have-picked" decision to activity.log (one line per cycle, tagged `dispatcher_observational`), but does NOT alter graph invocation. The MCP `queue_list` action reads the file fresh — no in-memory cache (reviewer's preferred drop). This is the "observable but not load-bearing" staging state: users see dispatcher reasoning in the log, and can cross-reference against `queue_list` for queue state, without the dispatcher's output affecting what the daemon actually runs.
   - **Phase D flag on + Phase E flag off:** Phase D wrapper runs with the default fantasy Branch + existing review-gate selection. No dispatcher.
   - **Phase D flag on + Phase E flag on:** dispatcher picks next BranchTask. If dispatcher returns a task, its `branch_def_id` + `inputs` drive `compile_branch` + `execute_branch_async`. If dispatcher returns `None`, fallback to the fantasy universe-cycle Branch with default inputs (R7 fallback).

5. **MCP `queue` surface — new actions on existing `universe` tool.** Not a new tool. Add two actions to `universe`:
   - `queue_list` — reads `branch_tasks.json`, applies visibility (none in Phase E — queue is public within a universe), returns sorted+scored queue as JSON. Include `tier_status` map per R11.
   - `queue_cancel` — sets a task's status to `cancelled`. Authorization: task's `claimed_by == actor` OR actor is host OR actor is the universe owner. Pending tasks can be cancelled by anyone authorized; running tasks require host override (not implemented in Phase E; returns `{"status": "rejected", "error": "running_tasks_require_host_override"}`).
   - No `queue_submit` action — that's `submit_request`. No `queue_reprioritize` — Phase E uses `priority_weight` as set at submission; mid-queue reprioritization is deferred (Phase H dashboard).

6. **Universe config additions.** `<universe>/config.yaml` (or `rules.yaml` per existing naming) gains a `dispatcher` section with the same keys as `DispatcherConfig`. Missing section = defaults. Hand-edited for now; Phase H dashboards it.

7. **Tests** — see §4.4.

### 4.2 Flags

Two flags active in Phase E:

- `TINYASSETS_UNIFIED_EXECUTION` — inherited from Phase D. Gates whether the fantasy daemon goes through `compile_branch` (flag on) or direct (flag off). No changes to flag contract.
- `TINYASSETS_DISPATCHER_ENABLED` — new. Default `on` (once Phase E lands). Gates whether the dispatcher's output drives graph invocation OR is observational only. Read at the start of `_run_graph` and at each cycle boundary, same pattern as Phase D's flag read. Revert lever: setting this to `off` makes Phase E a no-op even under Phase D flag-on.

Flag-matrix table lives in §4.1 #4 deliverable above.

### 4.3 Invariants (must hold across all flag states)

1. **Foundation review's hard-block invariant is preserved.** Dispatcher does NOT skip foundation review. When the dispatcher selects the fantasy universe-cycle Branch (either explicitly or via R7 fallback), the Branch's internal `foundation_priority_review` node still runs. Unsynthesized uploads remain a hard block. Test (new): queue a `host_request` BranchTask in a universe with unsynthesized uploads; assert foundation review still blocks on the review-gate side; dispatcher doesn't bypass. Applies under flag-on only (flag-off has no dispatcher).

2. **WorkTarget vs BranchTask separation.** One WorkTarget may spawn zero, one, or many BranchTasks over its lifetime (retry, re-queue, cancel+resubmit). No BranchTask creates a WorkTarget as a side effect of queuing — producers own WorkTarget creation. Test (new): submit a request that creates both a WorkTarget and a BranchTask; assert queue + work_targets.json have consistent refs; cancel the BranchTask and assert the WorkTarget remains.

3. **Producer double-execution guard.** Producers run once per cycle (inside the graph's review gates, as today). Dispatcher does NOT call producers. Test (new, per R9): mock producer `produce` methods to count calls; run one dispatcher cycle + one graph cycle under flag-on + flag-on; assert each producer called exactly once.

4. **Queue file-lock atomicity.** Concurrent `append_task` + `claim_task` + `mark_status` on the same `branch_tasks.json` produce a consistent final state. Test (new): 5 threads each perform 20 mixed queue operations; assert no lost entries, no duplicate claims, no status regressions.

5. **Dispatcher determinism under fixed inputs.** Given the same queue contents + `now_iso` + config, `select_next_task` returns the same task. Test (new): call `select_next_task` twice with identical state; assert equal return.

6. **Phase D flag-off compatibility.** With `TINYASSETS_UNIFIED_EXECUTION=off`, existing direct-graph path behaves identically to pre-Phase-E (modulo the new observational dispatcher read, which must be side-effect-free from the daemon's perspective). Test (new, parameterized over Phase E flag values): flag-off Phase D + either Phase E flag value → same graph invocation signature, same activity.log shape (modulo added dispatcher-read log entry which is below the existing INFO level or tagged distinctly).

7. **Queue survives daemon restart.** `branch_tasks.json` is a durable file; a daemon restart reads it and resumes with its contents intact. Claimed-but-unfinished tasks at restart are re-marked `pending` by the restart-recovery path, same pattern as `recover_in_flight_runs` at `tinyassets/runs.py:1374+`. Test (new): populate queue with 3 pending + 1 claimed-running task; simulate restart; assert claimed-running → pending, pending → pending, queue readable.

8. **MCP surface exposure.** `universe queue_list` returns the same view the dispatcher sees. No hidden tiers, no filtered fields. Test (new): seed queue with one task per tier; `queue_list` response contains all of them with their tier labels.

9. **Non-host `priority_weight` is capped at 0 at the submission boundary.** `_action_submit_request` clamps `priority_weight` to `0` when `source != host`. Host submitters may pass any non-negative value. Negative values rejected for all actors (avoids "reverse boost" abuse). Rationale: without this cap, any MCP client could inflate a `user_request` BranchTask's score above `host_request` via `priority_weight` on the `user_boost` term in §4.3 priority math — reviewer flagged this as a real abuse vector, not a hypothetical. Test (new): submit as host with `priority_weight=50` → queued BranchTask has `priority_weight=50`; submit as non-host with `priority_weight=50` → queued BranchTask has `priority_weight=0` (silently clamped, not rejected — rejection would be chattier than needed); submit as either with `priority_weight=-10` → rejected.

10. **Queue GC runs at daemon startup.** `branch_tasks.json` entries with `status ∈ {succeeded, failed, cancelled}` AND `queued_at` older than `ARCHIVE_AFTER_DAYS` (default 30) are moved to `branch_tasks_archive.json` at the start of `_run_graph`. The archive file is append-only; never read by the dispatcher; inspectable via a future MCP action (not in Phase E scope — archive surface lives on disk only for now). Running tasks and pending tasks are never archived regardless of age. Rationale: `branch_tasks.json` could grow unbounded; unbounded append-only JSON breaks file-read performance and the file-lock holds longer as the file grows. GC at startup (once per daemon lifecycle) is cheap and self-maintaining; no background thread. Constant exposed in `tinyassets/branch_tasks.py` so tests can override. Test (new): populate queue with 3 old-succeeded, 2 old-pending, 1 new-succeeded; call GC; assert `branch_tasks.json` has 2 old-pending + 1 new-succeeded, `branch_tasks_archive.json` has 3 old-succeeded.

### 4.4 Test strategy

New file `tests/test_phase_e_dispatcher.py`. Structure:

- **Queue plumbing (8 tests):** `BranchTask` round-trip (dataclass ↔ JSON); `read_queue` on missing file returns `[]`; `read_queue` on corrupt JSON raises (loud-fail per Hard Rule 8); `append_task` preserves ordering; `claim_task` idempotency (double-claim returns None on second call); `mark_status` valid transitions; `mark_status` invalid transitions raise; file-lock race test (5 threads × 20 ops, see R3).
- **Dispatcher scoring (6 tests):** tier_weight dominance (host_request outscores higher-priority_weight user_request); recency_decay ordering within a tier; user_boost within a tier; deferred coefficients ignored (bid_term + goal_affinity + cost_penalty = 0); determinism (per §4.3 invariant 5); empty queue returns None.
- **Submission integration (7 tests):** `submit_request` as host queues `host_request` BranchTask; `submit_request` as non-host queues `user_request` BranchTask; submission creates both WorkTarget and BranchTask (or assert chosen shape is consistent); 8 KiB cap on submit still enforced; host with `priority_weight=50` persists (invariant 9); non-host with `priority_weight=50` clamps to 0 (invariant 9); any actor with `priority_weight=-10` rejected (invariant 9).
- **DaemonController integration (6 tests, parameterized over 4-cell flag matrix from §4.1 #4):** flag-off+off no dispatcher call; flag-off+on observational read only; flag-on+off legacy Phase D behavior; flag-on+on dispatcher drives invocation; fallback to fantasy universe-cycle Branch on empty queue (R7); stop/pause respected at dispatcher boundaries (R10).
- **Restart recovery + GC (4 tests):** claimed-running → pending on restart; `branch_tasks.json` survives a DaemonController lifecycle; startup-GC archives old terminal tasks per invariant 10 (3-in-3-out scenario); GC preserves pending and running tasks regardless of age.
- **Invariant 1 — foundation review preserved (1 test):** queue a host_request BranchTask in a universe with unsynthesized uploads; assert foundation_priority_review still blocks.
- **Invariant 2 — WorkTarget/BranchTask separation (1 test):** cancel a BranchTask; assert its WorkTarget survives.
- **Invariant 3 — producer double-execution guard (1 test):** patch `produce` at the registry boundary — iterate `workflow.producers.registered_producers()` and replace each returned producer's `produce` method with a counting wrapper that records `id(producer)` on every invocation. Do NOT patch `domains/fantasy_author/producers.SeedProducer.produce` at the module level — that misses registry reshuffles and re-registrations (same care as Phase D invariant 4's `id()`-based check). Assert `len(calls) == len({id(p) for p in registered_producers()})` — each registered producer called exactly once per cycle under flag-on+on.
- **MCP surface (4 tests):** `universe queue_list` returns sorted+scored queue; `queue_cancel` requires authorization; `queue_cancel` on running task rejects with `running_tasks_require_host_override`; `tier_status` map reflects stubbed vs live tiers (R11).

Aim: ~38 tests. If dev finds a test that's ambiguous about "identical behavior" across flags, raise it — the invariant list is the bar.

### 4.5 Rollback plan

**Landing state:** Phase E flag `TINYASSETS_DISPATCHER_ENABLED=on` by default once landed. Merging is safe because Phase D's flag is off by default — so the dispatcher is observational only at merge time. Takes effect as a live surface only when Phase D's flag flips on (scheduled after user-sim Mission 8 gate per STATUS.md 2026-04-14).

**If Phase E breaks in live after Phase D flag-on flip:**
1. **Immediate:** host sets `TINYASSETS_DISPATCHER_ENABLED=0` and restarts the TinyAssets daemon. Dispatcher short-circuits; fantasy daemon falls back to R7 (direct fantasy universe-cycle Branch with default inputs). Submitted requests continue to queue in `branch_tasks.json` but the daemon ignores the queue until flag flips back on. No data loss.
2. **Short-term:** if `TINYASSETS_DISPATCHER_ENABLED=off` doesn't stabilize, host sets `TINYASSETS_UNIFIED_EXECUTION=off` as the deeper revert. Everything falls back to pre-Phase-D direct-graph behavior; Phase E is inert.
3. **Full rollback:** revert Phase E landing commit. `branch_tasks.json` files remain on disk but unused; they're harmless. Phase E flag stays defined but unreferenced.

**Do NOT:** schema-migrate `work_targets.json`, rewrite existing `requests.json` entries, or touch Phase D's wrapper callable. If dev finds they need any of those, scope has drifted.

**Data considerations:** `branch_tasks.json` is a new file. Existing universes have none. On first dispatcher read, missing file = empty queue (R7 fallback). No migration step required.

### 4.6 Non-goals (explicit)

- **NodeBid executor.** Phase G. `execute_node_bid` stubbed as a function raising `NotImplementedError` if the dispatcher somehow routes to it (should not happen in Phase E since `paid_bids` tier is stubbed in `DispatcherConfig`).
- **Goal-pool subscription.** Phase F. No `subscribed_goals` field on universe metadata in Phase E.
- **Bid-weighted sorting.** Phase G. `bid_coefficient=0` in Phase E defaults.
- **Host-dashboard UI.** Phase H. Phase E is MCP + config-file only.
- **Mid-queue reprioritization.** Phase H. `priority_weight` is set at submission time; changing it means cancel + resubmit.
- **Multi-universe daemon identity.** Each universe still has its own daemon process. Memo §Q3 deferred.
- **BranchTask cross-universe reference.** A BranchTask's `universe_id` is a hard bind; a BranchTask cannot run against a different universe than the one it was queued in. (NodeBid will be cross-universe in Phase G; BranchTask stays universe-bound per memo §3.2.)
- **Producer changes.** Phase C producer contract is stable. No new producers, no changes to the registry, no changes to WorkTarget shape.
- **Fantasy-graph topology changes.** The wrapper inside Phase D stays as-is. No new graph nodes, no new phase-to-phase edges. Phase E wraps around, not inside.
- **Schema changes to `WorkTarget`, `BranchDefinition`, `NodeDefinition`.** Forbidden per Phase D §4.6 carry-forward.

### 4.7 Files touched

| File | Change | Size estimate |
|------|--------|---------------|
| `tinyassets/branch_tasks.py` | NEW — dataclass + queue plumbing + file lock | ~200 lines |
| `tinyassets/dispatcher.py` | NEW — scoring + selection + config loader | ~150 lines |
| `tinyassets/universe_server.py` | EDIT `_action_submit_request` — also queue BranchTask with host/user identity classification | ~40 lines added |
| `tinyassets/universe_server.py` | EDIT — add `queue_list` and `queue_cancel` actions to `universe` tool dispatcher | ~100 lines added |
| `fantasy_author/__main__.py` | EDIT `_run_graph` — dispatcher read at startup + cycle boundaries, flag-gated per §4.1 #4 matrix | ~60 lines changed |
| `tinyassets/branch_tasks.py` (or a new `tinyassets/branch_task_recovery.py`) | EDIT/NEW — restart-recovery helper that file-locks `branch_tasks.json`, resets claimed-running to pending, runs the startup GC per invariant 10. Called from `_run_graph` entry. Not in `tinyassets/runs.py` because BranchTasks live in JSON not SQLite; the `recover_in_flight_runs` precedent at `runs.py:1374` is a one-statement SQL UPDATE and doesn't translate to JSON-lock-read-modify-write semantics. Expect ~40-50 lines including GC branch. | ~50 lines |
| `tests/test_phase_e_dispatcher.py` | NEW — ~38 tests | ~800 lines |
| `docs/exec-plans/daemon_task_economy_rollout.md` | EDIT — mark Phase E done when landed | ~5 lines |
| `STATUS.md` | EDIT — delete Phase E row, note user-sim gate for `TINYASSETS_DISPATCHER_ENABLED` default-flip | ~3 lines |

No new MCP tools (both actions land on the existing `universe` tool). No storage-schema changes. No PLAN.md changes. ~1,280 lines net, heavily weighted toward tests.

### 4.8 Success criteria (for reviewer)

- All tests in §4.4 green across the 4-cell flag matrix.
- Existing full suite green with both flags at defaults (Phase D off, Phase E on) — the landing is a no-op at rest.
- Dispatcher extension is strictly additive — no existing producer or graph test regresses.
- Foundation review's hard-block invariant measurably preserved (R1, invariant 1 test).
- WorkTarget/BranchTask separation visible in stress-test: cancel a BranchTask; assert WorkTarget survives.
- File-lock race test passes under concurrency.
- One user-sim mission runs cleanly end-to-end with both flags on: queue a request as host, observe it in `queue_list`, see the daemon claim it, see it complete, see status in activity.log.
- `universe queue_list` returns accurate tier-status map (live vs stubbed) per R11.
- Reviewer sees no changes to `WorkTarget`, `BranchDefinition`, `NodeDefinition`, or storage schemas.

### 4.9 Decision log — scope questions answered

Recording the choices this preflight makes so future sessions can rederive or challenge them.

**Q1. BranchTask queue data model — per-universe, per-daemon, global? Where does it persist?**
A. Per-universe, sibling file to `work_targets.json`. `<universe>/branch_tasks.json`. File-locked JSON. Rationale: matches the memo §4.6 data-model ask, keeps universe isolation intact (PLAN.md hard boundary), uses the same storage pattern as work_targets which is already battle-tested for concurrent writes. Not per-daemon because daemon ≠ universe one-to-one in the long run (memo §Q3 allows future multi-universe daemons). Not global because cross-universe work is Phase F (goal pool) / Phase G (bids); BranchTasks stay universe-bound per memo §3.2.

**Q2. "Tier-aware" — what tiers, how does tier affect scheduling?**
A. Six tiers per memo §4.2, but only two are live in Phase E: `host_request` (weight 100) and `owner_queued` (weight 80), with `user_request` (weight 60) nearly-live (already queued via submit_request). `goal_pool` (weight 40), `paid_bids` (scored via bid within tier), and `opportunistic` (weight 10) are config-tracked but no producer populates them in Phase E. Tier dominates within-tier scoring: a host_request with age=7 days outscores a user_request with age=0 seconds because tier_weight difference (100-60=40) swamps recency decay. §4.3 priority function in Phase E is `tier_weight + recency_decay + user_boost`; other terms zeroed until later phases.

**Q3. DaemonController surface — new MCP tool, extension to `universe`, host-dashboard only?**
A. Extension to `universe` with two new actions: `queue_list` and `queue_cancel`. No new tool. Rationale: §3 PLAN.md "small number of reliable composable tools"; queue inspection is universe-scoped so it belongs on the universe tool; cancellation is a steering action on the same scope. Host dashboard comes in Phase H; Phase E is MCP + config-file.

**Q4. Interaction with Phase D flag — does queue feed the wrapped BranchDefinition or stay on the direct path? Phase E's call or Phase G's?**
A. Phase E's call. Flag matrix in §4.1 #4 spells out four cells. Under flag-on+on, dispatcher feeds the wrapped BranchDefinition. Under any flag-off combination, queue is observational only. Not Phase G's call — Phase G adds the paid-bid tier on top of Phase E's existing wiring.

**Q5. Observability — how does the user see what's in queue, what's running, what's blocked?**
A. `universe queue_list` MCP action. Returns all tasks with status + tier + queued_at + score + claimed_by. Plus a `tier_status` top-level field (R11) showing live-vs-stubbed per tier. Running tasks show their `run_id` if available so the user can cross-reference against `run get` (existing action). No new polling pattern — same user inspection flow as existing `universe inspect` + `run get`.

**Q6. UX — how does a user steer/cancel/reprioritize?**
A. Steer: `submit_request` with `priority_weight` (new optional param in `_action_submit_request`, passed through into the queued BranchTask). Cancel: `universe queue_cancel task_id=...`. Reprioritize: cancel + resubmit in Phase E; in-place reprioritization deferred to Phase H dashboard. Pause a whole tier: hand-edit `dispatcher_config.yaml`; dashboard UI in Phase H.

### 4.10 Pause/stop latency — inherits Phase D §4.10

Under flag-on+on, the outer `_run_graph` loop now has two potential wait points: the dispatcher's "pick next task" step and the wrapper's "run the picked Branch to completion" step. `_stop_event` checks happen at both boundaries:
- Dispatcher-pick boundary: cheap (~ms to score + pick); stop observed immediately.
- Wrapper-complete boundary: slow (~minutes for fantasy cycle); same latency as Phase D §4.10.

Net effect: stop latency under Phase E flag-on+on is no worse than Phase D flag-on. No new regression. Test (new): `_stop_event.set()` during dispatcher pick → daemon exits before next Branch invocation; `_stop_event.set()` during Branch execution → same latency as Phase D.

### 4.11 Checkpoint semantics — unchanged from Phase D

Phase E does not alter SqliteSaver or the outer Branch state_schema. The dispatcher sits BEFORE the `execute_branch_async` call; its state is transient (`select_next_task` picks a task, which is passed into the wrapper as `inputs`). The picked task's `branch_task_id` is recorded in `branch_tasks.json` as `status=running` + `claimed_by=<daemon_id>`. On daemon restart (invariant 7), claimed-running tasks are reset to pending so the next dispatcher cycle can re-pick them. This is the queue-durability equivalent of §4.11's "disk artifacts are the truth."

No new §Checkpoint asymmetry section needed. Phase D §4.11 still applies; Phase E adds `branch_tasks.json` to the "disk artifacts that survive crashes" list.

### 4.12 Open design questions (non-blocking, flag for host input if time permits)

1. **Host identity signal.** R4 resolution uses `UNIVERSE_SERVER_HOST_USER` env var (default "host"). Alternative: a config key on `config.yaml`. Recommend env var for now (matches existing `UNIVERSE_SERVER_USER` pattern); revisit if hosts want per-universe host identities.
2. **Reserved-field future-compat.** R8 pre-reserves `bid`, `goal_id`, `required_llm_type`, `evidence_url`. Double-check that these are the right names by cross-ref with memo §3.2 NodeBid shape (the bid market is Phase G's territory, so naming should match what G expects). Recommend: match memo §3.2 exactly; if G decides to rename, a JSON field rename is cheap.

None of these blocks Phase E dev work; defaults are safe. (Two prior entries — `priority_weight` cap and queue GC — were promoted to invariants 9 and 10 in §4.3.)

## 5. Handoff

Dev can claim Phase E whenever Phase D has at least one user-sim mission green under flag-on (current STATUS.md gate). Merging Phase E is safe before Phase D's default flips because both flags default to the safe state (Phase D off, Phase E on-but-observational-only).

Raise PLAN.md concerns via STATUS.md if the WorkTarget/BranchTask distinction looks shaky in live code — R2 is the conceptual keystone and the most likely place for a design bug to show up.

Reviewer: the load-bearing contracts are R1 (foundation review preserved), R2 (WorkTarget vs BranchTask), R9 (producer double-execution guard), and R10 (pause/stop semantics inheritance). If any of those slip in implementation, flag back to planner before landing.
