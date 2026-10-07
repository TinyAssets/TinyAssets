---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-14. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase F Pre-Flight — Goal Subscription + Pool Producer

Planner draft, 2026-04-14. For dev handoff on task #5 Phase F rollout row.

Phase F is where the daemon first leaves the universe boundary. Phases C-E proved the daemon can run ANY Branch against ONE universe with a tier-aware queue. Phase F wires a cross-universe signal: one user posts a BranchTask to a public Goal pool; another user's daemon, subscribed to that Goal, picks it up. This is the "global goals engine" memo thesis in its minimum operational form — crude, file-backed, one-direction — and it's the first phase where a design bug leaks across universe boundaries. Designing carefully here sets the pattern for Phase G (bid market) and the multi-host future.

## 1. Source material

- `docs/exec-plans/daemon_task_economy_rollout.md` §Phase F (lines 154-180).
- `docs/planning/daemon_task_economy.md` §4.1 cascade item 5 (pool tier), §4.6 data-model asks, §3.2 BranchTask shape.
- `docs/specs/phase_e_preflight.md` §4.1 #4 flag matrix + §R9 producer double-execution guard + §4.3 invariant 9 priority_weight cap.
- Live Phase E code: `tinyassets/dispatcher.py` (scoring + selection), `tinyassets/branch_tasks.py` (queue primitives + file lock), `tinyassets/producers/__init__.py` (TaskProducer protocol).
- Live submission precedent: `tinyassets/universe_server.py:_action_submit_request` at ~1303 + `append_task` call at ~1419.
- STATUS.md 2026-04-14 Phase E follow-up #3 — "dispatcher still runs observational-only (not feeding `_run_graph`'s `initial_state`). Wire-up lands when user-sim Mission 8 gates the Phase D default flip, or as Phase F scope." This preflight's answer: Phase F scope. Load-bearing resolution.
- PLAN.md cross-cutting principle: "Universe = single consistent reality. Data isolation between universes is the only hard boundary." (§Design Decisions.)
- PLAN.md: "GitHub as canonical shared state. Public catalog of Goals, Branches, Nodes lives in the repo. Users clone, run locally, contribute via PR. No multi-tenant hosted runtime."

## 2. What exists vs. what Phase F adds

Before pinning the new surface, enumerate what's already live so Phase F composes existing pieces rather than rebuilding.

**Already live (after Phase E):**
- Six tier names are defined and the dispatcher's `DispatcherConfig` has `accept_goal_pool: bool = False` plus a `tier_weights["goal_pool"] = 40.0` default (`tinyassets/dispatcher.py:38-92`). Phase E reserved the tier; Phase F activates it.
- BranchTask dataclass has reserved fields `goal_id: str = ""` and `bid: float = 0.0` and `required_llm_type: str = ""` (`tinyassets/branch_tasks.py:68-90`). Phase F is the first consumer of `goal_id`; `bid` and `required_llm_type` stay empty until Phase G.
- `TaskProducer` protocol at `tinyassets/producers/__init__.py:21-65` with registry-based discovery. Three fantasy producers already register. Protocol docstring already mentions "Phase F may introduce an async variant" — that flag is called in now.
- `append_task` + `read_queue` + file-lock primitive ready for any submission path to write into `branch_tasks.json`. Phase F's pool-post action writes via this same API.
- Dispatcher's `tier_enabled` check routes `goal_pool` via `accept_goal_pool` flag (`tinyassets/dispatcher.py:67-68`). Phase F just needs a valid source of `goal_pool`-origin tasks for the dispatcher to pick up.
- Phase E dispatcher is observational-only — it logs picks but doesn't drive `_run_graph`'s `initial_state`. See §3 R1 for resolution.

**Phase F adds:**
- A per-universe `subscribed_goals: list[str]` durable record (memo §4.6 data ask).
- A repo-root `goal_pool/<goal_slug>/<branch_task_id>.yaml` convention — the cross-universe, public, pull-based substrate.
- `GoalPoolProducer` — new TaskProducer variant that reads the repo-root pool, filters to subscribed Goals, and emits BranchTasks into the per-universe `branch_tasks.json` queue (not WorkTargets — see R2).
- MCP actions: `subscribe_goal`, `unsubscribe_goal`, `list_subscriptions`, `post_to_goal_pool`.
- A curated "maintenance pool" (default subscription on fresh install) so idle daemons do useful cross-universe housekeeping out of the box.
- Feature flag `TINYASSETS_GOAL_POOL=off` (default). Flips on when the host opts their daemon into external work.
- **Dispatcher-to-invocation wire-up** (STATUS.md Phase E follow-up #3) — Phase F resolves the deferred plumbing so picked `goal_pool` tasks actually run.

**Explicitly NOT in Phase F:**
- Bid-weighted sorting within the pool tier (Phase G). Pool tasks use Phase E tier_weight + recency; `bid_coefficient` stays 0.
- NodeBid path (Phase G). NodeBids live at repo-root `bids/`, not `goal_pool/`.
- Cross-universe side effects. A BranchTask posted to `goal_pool/<goal>/` by universe A and executed by universe B's daemon runs in universe B, reads/writes B's state. B never reaches into A. See R4.
- Push notifications. Git-backed pull-only per PLAN.md.
- Goal pool moderation / vote-to-remove. v1 has no moderation primitive; any post is visible to any subscriber. Revisit when abuse appears.
- Evidence gates on pool-task completion. Outcome-gates #56 is the existing surface for that; a daemon completing a pool task may optionally claim a gate rung, but that coupling is Phase G's concern alongside bid receipts.
- Server-mediated pool. Everything is files in a git repo; no web service sits between post and pick.

## 3. Risk map

| # | Risk | Blast radius | Reversible? | Mitigation |
|---|------|--------------|-------------|------------|
| R1 | **Dispatcher-to-invocation gap (inherited from Phase E).** Under D-on+E-on the dispatcher picks a task but `_run_graph` ignores the pick and runs the default fantasy Branch with its own boundary-state seed. Phase F's whole premise (pool tasks get executed) fails silently | Catastrophic for Phase F — the pool producer emits tasks that never run. User-observable: subscribed daemon claims to have work but makes no progress on it | Yes (Phase F flag off) | Phase F resolves the plumbing. Contract in §4.1 #1: under D-on+E-on+F-on, `_run_graph` reads the dispatcher's pick, maps its `(branch_def_id, inputs)` into the wrapper's boundary-state seed, and runs that. Under any flag off, behavior reverts to current. Section §4.10 documents the plumbing shape. If this slips, Phase F is inert and §4.8 success criterion fails — must verify before landing. |
| R2 | Pool producer emits WorkTargets (the natural fit for TaskProducer protocol) but pool tasks are execution intents across universes — they ARE BranchTasks, not content units. Using WorkTargets collapses the §R2 WorkTarget/BranchTask separation Phase E just cemented | High — re-litigates Phase E's keystone distinction. Every future multi-phase decision then has to unwind the confusion | Yes | `GoalPoolProducer` does NOT fit the in-universe `TaskProducer` contract (which returns `list[WorkTarget]`). It's a new surface: `BranchTaskProducer` or equivalent — reads repo-root pool, returns `list[BranchTask]`, called by the dispatcher at cycle boundaries (NOT inside the graph's review gates). Distinct registry, distinct protocol. In-universe producers continue to emit WorkTargets and run inside review gates exactly as today. See §4.1 #2. |
| R3 | Pool producer running at every cycle re-reads `goal_pool/<goal>/*.yaml` — for N subscribed goals and M posts per goal, that's N×M YAML reads every dispatcher cycle. Over time this becomes the dominant cycle cost | Medium — user observes slower dispatcher responsiveness. With enough goals/posts, daemons appear to stall | Yes (file count is under user control — unsubscribe from noisy goals) | Read-once-per-cycle + cache invalidated on mtime. The producer stats the `goal_pool/<goal>/` directory's mtime; if unchanged since last scan, re-use last-scan result. Claim tracking uses the queue's existing dedupe (`branch_task_id` uniqueness) — a post re-seen on a second scan that's already in the queue is a no-op append. Stress-test target: 10 goals × 50 posts each = 500 YAMLs scanned in <50ms. |
| R4 | Cross-universe isolation leak. A pool task posted by universe A references universe-A state in its `inputs` (canon, work targets, premise kernel). When universe B's daemon executes it, B tries to read A's state — either explicitly (if `inputs.universe_id = A`) or implicitly (if the Branch code does `from runtime import universe_config` and gets B's config). Breaks PLAN.md's hard isolation boundary | Catastrophic — cross-universe contamination. Sporemarch-class bug resurfaces through a new vector | Partial (unpickable tasks can be left in pool; already-executed contamination is not reversible without universe rollback) | Pool tasks are **Branch-only** — no WorkTarget refs, no universe-specific `_universe_path` in inputs. The Branch must be self-contained (readable state from its own `inputs`, no global lookups). Producer rejects any pool YAML whose `inputs` contain `_universe_path`, `_db_path`, `_kg_path`, `work_target_ref`, or any key starting with `_`. Add a validator `validate_pool_task_inputs` at the producer boundary. Post-side (MCP `post_to_goal_pool`) strips these at serialization. Both sides enforce the invariant. §4.3 invariant 5 pins this. |
| R5 | `subscribed_goals` data model: is it a universe-level field or a daemon-level field? Memo §4.6 says "universe-metadata field"; that assumes 1:1 universe:daemon. But memo Q3 allows future multi-universe daemons. Picking the wrong scope now forces a migration later | Low-medium — data model choice | Yes (YAML field rename is cheap) | Land on universe-scope for v1 (matches §4.6 ask, matches current 1:1 reality). Store in `<universe>/subscriptions.json` — sibling to `branch_tasks.json`, same file-lock pattern. If a future daemon serves multiple universes, each universe brings its own subscription list; the daemon unions them at dispatch time. No migration needed; the storage shape is forward-compatible. Decision justified in §4.9 Q1. |
| R6 | Pool tasks land in `branch_tasks.json` from the GoalPoolProducer, but `branch_tasks.json` is the submission queue for `submit_request`-origin tasks too. Two write paths into the same file — same file-lock surface, but different write cadences. Pool producer writes (via append) on every cycle it sees a new post; submit_request writes on user action. Contention is rare but real | Low (file lock serializes); documented | Yes | File lock handles serialization cleanly. Idempotency via `branch_task_id` uniqueness — a pool producer re-reading the same YAML on a later cycle finds the task already in the queue and skips append. Contract: GoalPoolProducer uses the pool YAML's filename (`<branch_task_id>.yaml`) as the task_id so local idempotency is disk-natural. Test: seed pool with 5 posts, run producer twice, assert queue has exactly 5 tasks with matching IDs. |
| R7 | Phase E's §R9 invariant — producers don't run inside the dispatcher. GoalPoolProducer IS called from the dispatcher boundary (§4.1 #2), which appears to violate the invariant | Medium — breaks a crisp separation Phase E just established; invites re-litigation | Yes | The invariant is more precise than the shorthand. §R9 originally scopes to *in-universe TaskProducers* which emit WorkTargets (content) — they stay in review gates because WorkTarget materialization interacts with review logic. `BranchTaskProducer`s (the new surface Phase F introduces) emit BranchTasks (execution intents) — their natural home IS the dispatcher boundary. The distinction maps to R2: WorkTargets → review gates; BranchTasks → dispatcher. Phase E's invariant 3 remains valid with the clarification: "in-universe `TaskProducer.produce` is called exactly once per cycle at the review-gate entry; `BranchTaskProducer.produce` is called exactly once per cycle at dispatch boundaries." Rephrased clearly in §4.3 invariant 3. |
| R8 | Default "maintenance pool" subscription on fresh install. If the maintenance pool doesn't exist or contains broken tasks, fresh daemons idle/error on first boot | Medium — first-boot UX regression | Yes | The maintenance pool is an empty directory in v1. `goal_pool/maintenance/` ships in the repo with a README but zero YAMLs. Producer returns `[]` for empty directories. No bootstrapping content. The default subscription is live but the inbox is empty until someone posts actual maintenance tasks. §4.1 #3 documents. |
| R9 | `post_to_goal_pool` MCP action requires the poster to name a `branch_def_id` that the target daemon can actually run. If the poster's Branch doesn't exist in the subscriber's registry (because the registry is per-universe and public Branches haven't been cross-universe discoverable until now), the subscribed daemon claims the task and immediately fails compile | Medium — user confusion, stuck queue items | Yes | Pool tasks reference `branch_def_id` by **slug** (e.g. `research-paper/peer-review-v1`) not opaque ID. Subscriber-side resolution: GoalPoolProducer rejects posts whose slug doesn't resolve against the subscriber's accessible Branch slugs (public Branches + subscriber's own Branches). Rejected tasks stay in pool (the poster's problem to fix); they don't enter subscriber's queue. This is defense-in-depth on top of the dispatcher's own compile-failure hard-fail (Phase D R11). §4.3 invariant 6. |
| R10 | Fresh clone of the repo sees no `goal_pool/` directory. First boot of a daemon subscribing to any goal finds nothing to read | Low — matches R8; empty pool is expected | Yes | Producer treats missing `goal_pool/` directory as empty — returns `[]` with a single INFO log. Not a warning, not an error. Documented. |
| R11 | Post deletion. A user who posts a task and then decides to retract has no deletion surface in Phase F. The task stays in the pool until a subscriber claims it (at which point their queue has it) or forever (if nobody claims). Pool-level rotation/GC not in scope | Low (users post cautiously) | Partial (users can hand-delete the YAML from the repo) | v1 accepts this. `post_to_goal_pool` returns the YAML path so the poster knows where to hand-delete. Pool-level GC (archive old posts, delete orphaned claims) is Phase H dashboard territory. §4.6 non-goal. |
| R12 | Git-backed pool means "post" is implicitly a PR-to-repo action — but Phase F's `post_to_goal_pool` MCP action writes a local file. For single-host / local-only installs, that IS the authoritative post. For multi-host, the user must push the file for subscribers to see it | Medium — UX confusion about when a post is "live" | Yes | v1 documents: `post_to_goal_pool` writes locally. For a post to reach cross-host subscribers, the poster runs `git push` (or the future Phase H dashboard auto-pushes). The MCP response includes an explicit `"next_step": "git add goal_pool/<goal>/<id>.yaml && git commit && git push"` hint. §4.1 #4 spells out. |
| R13 | Race: subscriber A's daemon and subscriber B's daemon both see a pool task simultaneously (both pull from git at the same time, both producer-cycle at the same time). Both enqueue it, both claim it, both execute — double work | Medium — duplicated work, possibly inconsistent outputs | Partial (git conflict on completion-marker push; only first writer wins) | v1 accepts double-execution as a minor correctness cost. The pool task YAML has no "claimed" marker in the repo — claims are local to each subscriber's `branch_tasks.json`. On completion, the subscriber may optionally write a completion marker (Phase G evidence_url ties into this); first-push-wins on the git side. Two subscribers both completing is a forked-history merge conflict the user resolves. §4.12 open question — formal claim semantics tracked for Phase G. |

### Reversibility summary

Everything is reversible by `TINYASSETS_GOAL_POOL=off`. Phase F's flag is default off. With the flag off, the GoalPoolProducer is not registered, `post_to_goal_pool` / `subscribe_goal` actions return `{"status": "not_available", "hint": "TINYASSETS_GOAL_POOL=on required"}`, and existing universe behavior is unchanged. Phase E's dispatcher-to-invocation wire-up (§R1) lands under this flag too — if it destabilizes the direct path, flag off reverts both Phase F AND the wire-up simultaneously. A merged-but-flag-off Phase F is inert.

Secondary safety: the repo-root `goal_pool/` directory is public-by-construction (it's in the repo) but disposable. A host who gets a bad post in the pool can `git reset` the file locally; they don't need to do anything if they don't subscribe to that Goal.

## 4. Implementation contract

Scope boundaries, signatures, invariants, and non-goals. Dev fills in HOW.

### 4.1 Deliverables

1. **Dispatcher-to-invocation wire-up (resolves Phase E follow-up #3).** This is prerequisite for Phase F but lands in this phase. `fantasy_author/__main__.py:_run_graph` changes under D-on+E-on+F-on (or D-on+E-on even without F, if the plumbing lands):
   - At cycle boundary, call `select_next_task(universe_path, config=cfg)`. If the result is non-None, extract `(branch_def_id, inputs)` from the `BranchTask`.
   - If `branch_def_id == "fantasy_author/universe-cycle"` (the default Branch): use the existing wrapper invocation path but set the wrapper's boundary-state seed from `inputs` where `inputs` provides fields (e.g. a goal-pool task with `inputs.target_series = "foo"` overrides the wrapper's default).
   - If `branch_def_id != "fantasy_author/universe-cycle"`: load the named Branch (defensively — not all subscriber daemons serve all Branches), compile via `compile_branch`, invoke via the same wrapper pattern.
   - Mark the picked task `status=running` via `mark_status` before invocation. On completion, `status=succeeded` or `status=failed` with error text. Use the existing file-locked `mark_status` from `tinyassets/branch_tasks.py`.
   - The wire-up is strictly additive — if dispatcher returns None or if Phase F flag is off, `_run_graph` follows the existing D-on path unchanged.
   - If dispatcher returns a task whose `branch_def_id` doesn't resolve, mark it `failed` with `error="branch_not_available"` and continue with the default path for THIS cycle — don't loop on failure.

2. **`tinyassets/producers/branch_task.py` — new `BranchTaskProducer` protocol.** Distinct from the in-universe `TaskProducer`:
   - `BranchTaskProducer(Protocol)` — fields `name: str`, `origin: str` (matches `BranchTask.trigger_source`). Method `produce(universe_path: Path, *, subscribed_goals: list[str], config: dict | None = None) -> list[BranchTask]`. Returns the BranchTasks this producer wants to add to the queue; the dispatcher appends them (idempotent on `branch_task_id`) via existing `append_task`.
   - Module-level registry + `register_branch_task_producer(p) -> None` + `registered_branch_task_producers() -> tuple` + `run_branch_task_producers(universe_path, subscribed_goals) -> list[BranchTask]`. Mirrors the existing `tinyassets/producers/__init__.py` shape.
   - Called from the dispatcher's `select_next_task` orchestration at cycle boundaries (see R7 — this is WHERE the BranchTaskProducer lives, distinct from in-universe WorkTarget producers).
   - Synchronous protocol for v1. Memo §3.2 suggested async for "I/O-heavy cross-universe producers"; the pool producer's I/O is local file reads + optional `git fetch` — milliseconds. Keep sync; revisit if a producer ever does HTTP.

3. **`tinyassets/producers/goal_pool.py` — `GoalPoolProducer`.** Concrete implementation of `BranchTaskProducer`. Reads `<repo_root>/goal_pool/<goal_slug>/*.yaml` for each goal in `subscribed_goals`. Each YAML file parses into a BranchTask via `BranchTask.from_dict` with validation:
   - Required fields: `branch_task_id` (must match filename stem), `branch_def_id` (slug form), `goal_id` (must match containing directory slug), `inputs` (dict).
   - Rejected fields in `inputs` (R4): `_universe_path`, `_db_path`, `_kg_path`, `work_target_ref`, any key starting with `_`.
   - Stamp `trigger_source="goal_pool"`, `queued_at=<file mtime>`, `claimed_by=""`, `status="pending"`, `priority_weight=0.0`. (Pool tasks have no user_boost signal in v1.)
   - Subscriber-side Branch slug resolution per R9: if `branch_def_id` doesn't resolve against this daemon's accessible Branches, log at INFO and skip (don't emit). Rejected tasks do NOT land in queue.
   - Producer name = `"goal_pool"`. Origin = `"goal_pool"`.
   - Registered at module-import time via side-effect pattern (Phase D precedent).

4. **MCP actions on `universe` tool.** Add four actions:
   - `subscribe_goal(goal_id: str)` — append to `<universe>/subscriptions.json`. Idempotent.
   - `unsubscribe_goal(goal_id: str)` — remove. Silent on not-present.
   - `list_subscriptions()` — read current subscriptions. Include `pool_status_per_goal: dict[str, int]` showing count of pending posts per subscribed goal. **Drift detection:** response also includes `config_vs_subscriptions_drift: str` — one of `"ok"`, `"pool_enabled_no_subs"` (F on + `accept_goal_pool=true` but zero subscriptions), `"subs_but_pool_disabled"` (subscriptions exist but `accept_goal_pool=false` — host will receive no pool tasks despite subscribing). Prevents the "I enabled pool, why no tasks?" UX confusion. Chose drift-flagging over auto-flipping `accept_goal_pool=true` on first subscribe — auto-flip surprises users who didn't intend the config change; drift-flag gives them a one-line nudge they can act on.
   - `post_to_goal_pool(goal_id: str, branch_def_id: str, inputs_json: str, priority_weight: float = 0.0)` — write a YAML to `<repo_root>/goal_pool/<goal_id>/<new_task_id>.yaml`. Response includes the file path AND a hint string: `"To make this post visible to cross-host subscribers, run: git add goal_pool/<goal>/<id>.yaml && git commit && git push"`.
   - Authorization: any authenticated actor can post; host identity signal from `UNIVERSE_SERVER_USER` is recorded in the YAML's `posted_by` field for attribution. Non-host posters have `priority_weight` clamped to 0 (inherits Phase E §4.3 invariant 9 across the new submission path).

5. **Subscription durability.** `<universe>/subscriptions.json`. Shape: `{"goals": ["research-paper", "fantasy-novel", "maintenance"], "updated_at": "<iso>"}`. File-locked via the same `_file_lock` helper imported from `tinyassets/branch_tasks.py`, but using a **separate sidecar lock file** at `<universe>/subscriptions.json.lock` — NOT the same lock as `<universe>/branch_tasks.json.lock`. Subscription mutations and queue mutations share zero contention surface; a long-held subscription read does not block dispatcher-cycle queue writes, and vice versa. Missing file treated as empty list with automatic default-maintenance-subscription added on first read (the "fresh install subscribes to maintenance" default — see invariant 10 + §4.12 Q2).

6. **Universe config additions.** `<universe>/dispatcher_config.yaml` gains a `goal_pool` section with:
   - `accept_goal_pool: true` (flips the existing DispatcherConfig field from default false to true when host opts in).
   - `max_pool_tasks_per_cycle: 5` — cap on how many pool tasks the producer returns per cycle (prevents a huge pool from flooding the queue).
   - `goal_affinity_coefficient: 1.0` — bumps goal_pool-tier scoring per §4.3 priority math. Non-zero in Phase F for the first time (was 0 in Phase E reservation).

7. **Repo-root discovery (contract).** `workflow.goal_pool.repo_root_path() -> Path` resolves the shared pool location in this order:
   1. `TINYASSETS_REPO_ROOT` env var — explicit host control. Matches the `UNIVERSE_SERVER_USER` / `UNIVERSE_SERVER_HOST_USER` pattern. Takes precedence over everything.
   2. Git-detect upward from `<universe_path>` — walk parents looking for `.git/`. Matches the "clone the repo, run locally" PLAN.md mental model.
   3. `RuntimeError` if neither resolves. Pool producer treats this as "pool not available, empty result, INFO log"; MCP post action returns `{"status": "rejected", "error": "repo_root_not_resolvable", "hint": "Set TINYASSETS_REPO_ROOT or run the daemon from inside a git checkout"}`.

   Fallback order deliberately NOT "always parent of universe" — some installs keep universes under `~/universes/<name>/` with no git at the parent level. Fallback order deliberately NOT "git-detect only" — pytest tmpdir tests need a fixed env-var pin to work without a fake .git scaffold. This resolves §4.9 Q3 as a contract, not an open question.

8. **Tests** — see §4.4.

9. **Documentation:** `docs/planning/goal_pool_conventions.md` — repo-root pool directory layout, YAML shape, filename rules, push workflow, repo-root resolution contract. Public-facing; users writing a `post_to_goal_pool` MCP call reference it.

### 4.2 Flags

Three flags active in Phase F:

- `TINYASSETS_UNIFIED_EXECUTION` (Phase D) — required ON for pool tasks to actually execute (via the wrapped BranchDefinition path). If OFF, pool producer still emits into queue but dispatcher-observational-only (inherits Phase E matrix).
- `TINYASSETS_DISPATCHER_ENABLED` (Phase E) — required ON. If OFF, dispatcher short-circuits; pool tasks sit in queue unseen.
- `TINYASSETS_GOAL_POOL` (Phase F, new) — default OFF. When ON: GoalPoolProducer registers; MCP pool actions functional. When OFF: GoalPoolProducer not registered; MCP pool actions return `{"status": "not_available", "hint": "..."}`.

**Three-flag matrix** — table summarizes behavior. Dev should pin this in comments somewhere (probably `tinyassets/producers/goal_pool.py` docstring):

| D | E | F | Behavior |
|---|---|---|----------|
| off | * | * | Phase D not live; pool irrelevant. F-flag actions still work (post to pool, subscribe), but no daemon executes pool tasks. |
| on | off | * | Dispatcher disabled; pool tasks remain in queue; pool producer no-op. |
| on | on | off | Dispatcher observational-only (Phase E matrix); pool producer not registered; no pool tasks enter queue. |
| on | on | on | **Full Phase F.** Pool producer registers, emits into queue at cycle boundaries; dispatcher picks pool tasks subject to `accept_goal_pool`; wrapper invocation executes them. |

### 4.3 Invariants (must hold across all flag states)

1. **Universe isolation preserved across pool traversal.** A pool task posted by universe A and executed by universe B's daemon reads zero state from universe A during its execution. Enforced by a **flat-dict invariant on `inputs`**: pool-task `inputs` must be a flat dict whose values are primitives (`str`, `int`, `float`, `bool`, `None`). Nested dicts and lists are rejected at BOTH the post-side (`post_to_goal_pool` MCP action) and the producer-side (`GoalPoolProducer` read). Rationale: a recursive strip of `_`-prefixed keys is hard to get right (skip-one-level bugs smuggle `{"outer": {"_universe_path": "..."}}` past the top-level strip). Flat-only enforcement is trivially correct and sufficient for all Phase F use cases — pool tasks carry execution-intent scalars (target series, chapter target, premise override string), not structured data. If a poster needs structured inputs, they serialize them into a single string field and the downstream Branch parses it. Test (new): post with `inputs={"_universe_path": "..."}` → post rejected; post with `inputs={"outer": {"_universe_path": "..."}}` → post rejected on nested-dict, not on `_`-prefix; post with `inputs={"clean": "value"}` → accepted; round-trip through producer preserves flat shape. See R4.

2. **WorkTarget vs BranchTask separation (carry-forward from Phase E §R2).** Pool producer emits BranchTasks, not WorkTargets. In-universe producers emit WorkTargets, not BranchTasks. Two distinct protocols, two distinct registries. Test (new): introspect registries; assert `workflow.producers` registry contains only `TaskProducer`; assert `workflow.producers.branch_task` registry contains only `BranchTaskProducer`; assert no overlap by instance `id()`.

3. **Producer-call boundary (refines Phase E §R9).** In-universe `TaskProducer.produce` is called exactly once per graph cycle inside review gates. `BranchTaskProducer.produce` is called exactly once per dispatcher cycle at the boundary. Producers do NOT cross surfaces. Test (new): patch both registries' `produce` at their registry boundaries (id-counted per Phase D/E pattern); run one combined cycle; assert each in-universe producer called 1×, each BranchTaskProducer called 1×, no cross-calls.

4. **Pool idempotency.** Re-scanning a pool that hasn't changed produces no queue growth. Test (new): post 3 tasks to pool; run producer 5×; assert queue has exactly 3 tasks with the 3 expected IDs; assert no log warnings about duplicate appends.

5. **Subscribe → post → pick round-trip.** A subscriber who subscribes to goal X, a poster who posts a valid task to goal X, the subscriber's next cycle sees the task in its queue as `pending` with `origin=goal_pool`. Test (new, end-to-end): spin up two universes in pytest temp dirs sharing a repo_root; subscribe uni-B to "test_goal"; post from uni-A to "test_goal"; run uni-B's dispatcher one cycle; assert uni-B's queue has the task.

6. **Branch-slug resolution at producer boundary (R9).** Pool tasks referencing a branch_def_id that doesn't resolve locally are skipped, not enqueued-and-failed. Test (new): post task with `branch_def_id="nonexistent/branch"`; run producer; assert queue does not contain task; assert log entry at INFO level.

7. **priority_weight cap extends to pool posts.** Non-host posters clamp to 0. Test (new): post as non-host with priority_weight=50 → posted YAML has `priority_weight=0`; post as host with priority_weight=50 → posted YAML has `priority_weight=50`.

8. **Dispatcher wire-up hard-fail + cancel-during-claim race (R1 + Phase D §R11).** Two sub-cases, both tested:
   - **Compile failure.** Picked task whose Branch fails to compile under the wrapper is marked `failed` with clear error; daemon does NOT silently fall back to default path without logging. Test (new): seed queue with a task referencing a registered-but-invalid Branch; assert `_run_graph` marks it failed; assert one ERROR log line identifying the branch_def_id; assert daemon continues with default path next cycle.
   - **Cancel-during-claim race.** If a user cancels a task (transitions `pending → cancelled` via `queue_cancel`) between the dispatcher's `select_next_task` pick and the wire-up's claim attempt, the claim must fail cleanly — NOT raise `ValueError` from `_VALID_TRANSITIONS` at `tinyassets/branch_tasks.py:59-65` (which rejects `cancelled → running`). Use `claim_task(universe_path, task_id, claimer)` which returns `None` on not-pending rather than `mark_status(..., status="running")` which would raise. Pseudocode in §4.10 reflects this. Test (new): seed queue with pending task; patch `select_next_task` to return it; race: cancel the task before invocation; assert daemon logs an INFO line `claim_lost_to_cancel` and falls through to default path without raising.

9. **Pool-task execution writes to subscriber universe only.** After pool-task completion, subscriber universe's state (canon, notes, work_targets) may show writes; poster universe's state is byte-identical to pre-execution. Test (new, end-to-end extension of invariant 5): post a task that writes to canon; subscriber executes; assert subscriber's canon has the write; assert poster's canon has no change.

10. **Fresh-install default subscription.** First-boot daemons subscribe to `maintenance` automatically. Test (new): fresh universe directory with no `subscriptions.json`; read subscriptions; assert `["maintenance"]`.

### 4.4 Test strategy

New file `tests/test_phase_f_goal_pool.py`. Structure:

- **BranchTaskProducer protocol (4 tests):** registry register/unregister/reset_registry; duplicate `name` replaces prior instance; `run_branch_task_producers` returns empty list with no producers; producer raising is caught and logged.
- **GoalPoolProducer (8 tests):** empty `goal_pool/` directory returns `[]`; malformed YAML returns `[]` with WARNING log (loud-fail contract per Hard Rule 8 but don't kill the cycle); task with `_universe_path` in inputs rejected (R4); task with missing required fields rejected; valid task emits matching BranchTask with correct origin; re-scan idempotency (invariant 4); branch_def_id slug resolution failure skips task (invariant 6, R9); mtime-based cache invalidation (R3).
- **Subscription data model (6 tests):** missing `subscriptions.json` returns `["maintenance"]` default (invariant 10); subscribe_goal appends + is idempotent; unsubscribe_goal removes + silent-on-missing; list_subscriptions returns current set with pool_status_per_goal populated; drift flag `pool_enabled_no_subs` returned when `accept_goal_pool=true` + zero subs; drift flag `subs_but_pool_disabled` returned when subs exist + `accept_goal_pool=false`.
- **MCP post_to_goal_pool (5 tests):** valid post writes YAML to expected path; returns hint string with git push instructions (R12); host posts with priority_weight=50 → YAML has 50; non-host posts with priority_weight=50 → YAML has 0 (invariant 7); inputs containing `_universe_path` rejected server-side (symmetry with producer-side per R4).
- **Flag matrix (5 tests, one per distinct behavior cell):**
  1. **D-off + E-any + F-any** — fantasy direct-graph path; pool producer not registered; pool MCP actions respect F flag (work when F-on, return not_available when F-off). Most collapsed of the matrix; one test covers all 4 D-off combinations since D gates everything.
  2. **D-on + E-off + F-any** — dispatcher disabled; pool producer not registered if F-off, registered-but-unread if F-on. Queue items stay pending regardless.
  3. **D-on + E-on + F-off** — Phase E behavior preserved; dispatcher observational-only for non-pool tiers; pool producer NOT registered; post_to_goal_pool MCP returns not_available.
  4. **D-off + E-on + F-on** — staging state (reviewer flagged as important). Dispatcher-observational-only-but-F-live: post actions work, subscribe works, pool YAMLs land on disk, but no daemon executes pool tasks because D-off means the wrapper path isn't in use. Important because host may enable Phase F before flipping Phase D default, expecting posts to queue up for when D flips. Test that this staging state behaves cleanly.
  5. **D-on + E-on + F-on** — full Phase F. Pool producer emits; dispatcher selects; wrapper invokes; completion transitions mark_status. This is the only cell where pool tasks execute end-to-end.

  (The 3 remaining D×E×F cells collapse into test 1 — once D is off, the downstream flags have no effect on daemon execution, though F-on still gates MCP post actions, covered in test 1 as a sub-assertion.)
- **Dispatcher wire-up (5 tests, R1):** picked task runs via wrapper and writes its inputs-derived state to boundary (test under D-on+E-on+F-on); picked task marks `running` before invocation and `succeeded` after; failed compile marks `failed` with error text (invariant 8); default Branch preserved when dispatcher returns None; pick-then-no-op-fallback when Branch doesn't resolve.
- **Invariants (6 tests):** 1 (universe isolation); 2 (registry separation); 3 (producer-call boundary, id-counted); 5 (subscribe→post→pick round-trip — end-to-end); 9 (write-scoping); 10 (fresh-install default).
- **R13 race test (1 test):** two daemons simultaneously see the same pool YAML; assert both enqueue locally; assert completion of one doesn't prevent completion of the other. Verifies the accepted double-execution risk is visible, not hidden.

Aim: ~41 tests. Many share fixtures — a `_two_universe_fixture` helper providing two tmpdir universes sharing a repo_root is worth writing once and reusing across invariants 1, 2, 5, 9. The `TINYASSETS_REPO_ROOT` env var (§4.1 #7) is the fixture's pinning mechanism — no git scaffolding required.

### 4.5 Rollback plan

**Landing state:** `TINYASSETS_GOAL_POOL=off` by default. Merging safe; existing universes unaffected. The dispatcher-to-invocation wire-up (§4.1 #1) is gated under D-on+E-on — with Phase D default still off, it's still inert at merge time.

**Flag-on rollout gate:** user-sim Mission 9 (after Mission 8 flips Phase D default on) runs an end-to-end pool post-and-pick between two pytest temp universes. When that passes, flag can default to on.

**If Phase F breaks in live after flag on:**
1. **Immediate:** `TINYASSETS_GOAL_POOL=0` + TinyAssets daemon restart. Pool producer unregisters; subscriber queues stop receiving new pool tasks; existing queued pool tasks get cancelled OR execute normally (dev picks: recommend "remain queued, marked cancelled on next cycle under flag-off"). No data loss.
2. **Short-term:** if flag-off doesn't stabilize, set `TINYASSETS_DISPATCHER_ENABLED=0`. Now all tier-aware selection is off; daemon falls back to pre-Phase-E default fantasy selection. Pool tasks stay in files but nothing reads them.
3. **Full rollback:** revert the Phase F commit. Files in `goal_pool/` remain harmlessly in repo. Subscription files in `<universe>/subscriptions.json` remain. Nothing reads them.

**Data considerations:**
- `goal_pool/` directory is new. Empty by default (except the maintenance README). No migration.
- `subscriptions.json` is new per-universe. Missing → defaults to `["maintenance"]` on first read (invariant 10). No migration.
- `branch_tasks.json` may contain `goal_pool`-origin entries after Phase F. If Phase F is rolled back, those entries remain valid-shaped BranchTasks with an unknown trigger_source from the reverted code's perspective — dispatcher ignores them (`tier_enabled` returns False for unknown sources, Phase E dispatcher.py:73). No crash, no data loss; they sit in queue inert until re-enabled or hand-archived.

**Do NOT:** rewrite `branch_tasks.json` on rollback, delete `goal_pool/` directory, touch Phase D wrapper, or change Phase E dispatcher scoring. If dev finds they need any of these, scope has drifted.

**STATUS.md Concerns to file on land (durable, so they survive session gaps):**
- **R13 double-execution accepted for v1 — revisit when maintenance pool has real content and 2+ subscribers.** Current preflight accepts two subscribers racing on the same pool YAML. Accepted cost: duplicate work, first-push-wins on completion markers. File as a dated Concern so a future session picks it up when the maintenance pool stops being empty and a second host subscribes — at that point, observed-double-execution becomes the data needed to formalize claim semantics (§4.12 Q2 territory, full resolution in Phase G bid atomicity).
- **Dispatcher-to-invocation wire-up resolved (retire Phase E follow-up #3).** Phase E's deferred follow-up #3 is fixed by Phase F §4.1 #1. On land, delete the corresponding line from STATUS.md Phase E follow-ups.

### 4.6 Non-goals (explicit)

- NodeBid path. Phase G. NodeBids live at repo-root `bids/`, not `goal_pool/`.
- Bid-weighted pool scoring. Phase G. Phase F pool tasks have `bid=0.0` in v1.
- Push-based subscription notifications. PLAN.md "local-first, git-native" pins us to pull.
- Server-mediated pool. Same reason.
- Pool moderation / vote-to-remove / reputation scoring. Revisit in Phase H / I.
- Pool-level GC or archive. Old posts stay forever in v1.
- Outcome-gate claims on pool-task completion. Phase G couples bid receipts + gate claims; Phase F does not.
- Multi-universe daemon subscription unioning. Deferred to memo Q3 resolution.
- Async TaskProducer protocol variant. Phase F's I/O is local-file-only; sync is fine.
- Schema changes to `BranchTask`, `WorkTarget`, `BranchDefinition`, `NodeDefinition`. Forbidden per Phase D §4.6 / Phase E §4.6 carry-forward.
- New tiers beyond the six defined in Phase E.

### 4.7 Files touched

| File | Change | Size estimate |
|------|--------|---------------|
| `tinyassets/producers/branch_task.py` | NEW — `BranchTaskProducer` protocol + registry + helpers | ~90 lines |
| `tinyassets/producers/goal_pool.py` | NEW — `GoalPoolProducer` + YAML reader + input validator | ~150 lines |
| `tinyassets/universe_server.py` | EDIT — add `subscribe_goal` / `unsubscribe_goal` / `list_subscriptions` / `post_to_goal_pool` actions to `universe` tool dispatcher | ~200 lines added |
| `tinyassets/subscriptions.py` | NEW — per-universe subscriptions file I/O + default-maintenance logic + file lock | ~80 lines |
| `fantasy_author/__main__.py` | EDIT `_run_graph` — dispatcher-to-invocation wire-up (§4.1 #1). Strictly additive; existing path preserved | ~80 lines changed |
| `tinyassets/dispatcher.py` | EDIT — add BranchTaskProducer run-step at cycle boundary (writes queue before selecting). No change to scoring | ~30 lines added |
| `goal_pool/maintenance/README.md` | NEW — explains maintenance pool, post conventions, example YAML | ~40 lines |
| `docs/planning/goal_pool_conventions.md` | NEW — public-facing pool directory layout + YAML shape + git push workflow | ~80 lines |
| `tests/test_phase_f_goal_pool.py` | NEW — ~41 tests | ~950 lines |
| `docs/exec-plans/daemon_task_economy_rollout.md` | EDIT — mark Phase F done when landed | ~5 lines |
| `STATUS.md` | EDIT — delete Phase F row, retire Phase E follow-up #3 (dispatcher-invocation gap resolved), document Phase F flag gate | ~5 lines |

No schema changes to any existing dataclass. No new MCP tools (4 actions land on existing `universe` tool). ~1,650 lines net — heavily weighted toward tests and protocol-layer scaffolding.

### 4.8 Success criteria (for reviewer)

- All tests in §4.4 green across the D×E×F flag matrix cells that have distinct live behavior.
- Existing full suite green with all three flags at defaults (all off) — the landing is a no-op at rest.
- No changes to `BranchTask`, `WorkTarget`, `BranchDefinition`, `NodeDefinition`, or storage schemas.
- Universe isolation invariant measurable — invariant 1 test exercises validator at BOTH producer-read and post-write boundaries.
- WorkTarget/BranchTask separation measurably preserved — invariant 2 registry separation test passes.
- Dispatcher wire-up (§R1) is strictly additive — Phase D+E tests still green when Phase F flag is off.
- One user-sim mission runs end-to-end with all three flags on: host subscribes to a pool Goal in uni-B, another user posts from uni-A, uni-B's daemon picks up and executes, queue_list reflects the running→succeeded transition.
- Pool producer idempotency measurable under stress (invariant 4 + R3 cache-invalidation test).
- Subscribe/unsubscribe/list_subscriptions MCP actions work against the live daemon via Claude.ai MCP client.
- `goal_pool/maintenance/README.md` accurately describes the default pool's purpose (currently: empty; future: housekeeping tasks).

### 4.9 Decision log

**Q1. Subscription primitive — per-universe opt-in list, per-daemon tier policy, or mixed?**
A. Per-universe opt-in list of Goal IDs. `<universe>/subscriptions.json`. Rationale: matches memo §4.6 data-model ask; matches current 1:1 universe:daemon reality; forward-compatible with multi-universe-daemon future (daemon unions lists). Per-daemon tier policy (memo Q3 territory) is orthogonal — a daemon's tier switches are already in `dispatcher_config.yaml`; subscription is who-you-care-about, tier policy is what-you-pay-attention-to. Keep them separate surfaces.

**Q2. Pool producer protocol — extend `TaskProducer` or new protocol?**
A. New protocol — `BranchTaskProducer`. Lives at `tinyassets/producers/branch_task.py`. Produces `list[BranchTask]`, not `list[WorkTarget]`. Rationale: R2 + Phase E's keystone distinction. In-universe producers emit content (WorkTargets) inside review gates; cross-universe producers emit execution intents (BranchTasks) at dispatcher boundaries. Two distinct registries, two distinct surfaces. Synchronous protocol for v1; memo §3.2's "async variant" deferred until a real async producer appears (HTTP-based cross-project signals, maybe Phase I).

**Q3. Fetching model — poll vs push; file-backed vs server-mediated?**
A. Pull from git-backed repo-root directory. `goal_pool/<goal_slug>/*.yaml`. Rationale: PLAN.md "local-first, git-native" + "GitHub as canonical shared state" settle this. Users clone the repo, their daemons see the pool. Posts are `git push` actions (the MCP action writes local; the user or Phase H dashboard pushes). No web service. No push notifications. Single-host / local-only installs work entirely locally — the pool dir is real; push is a no-op because no remote cares.

**Q4. Cross-universe isolation — how enforced?**
A. Two-boundary validation with a **flat-dict invariant** on `inputs`. Both `post_to_goal_pool` (write-side) and `GoalPoolProducer` (read-side) reject pool tasks whose `inputs` is not a flat dict of primitive values (`str`/`int`/`float`/`bool`/`None`). Nested dicts, nested lists, and `_`-prefixed keys all rejected at both boundaries. Rationale: recursive strip (Option A) is error-prone — a single missed recursion depth smuggles `_universe_path` past the check. Flat-only (Option B, picked) is trivially correct, easy to test, and sufficient for Phase F's execution-intent payloads. If a future phase needs structured inputs, the Branch parses a serialized string field; the pool schema itself stays flat. Invariant 1 + invariant 9 pin both sides. See R4.

**Q5. Interaction with Phase E queue — pool tasks enter same `branch_tasks.json`?**
A. Yes. Pool producer writes into the same per-universe `branch_tasks.json` via existing `append_task`. Dispatcher then scores and picks via existing `select_next_task`. The `trigger_source="goal_pool"` + `accept_goal_pool=true` config path are already wired (Phase E reservation). File-lock handles concurrent producer + submit_request writes (R6).

**Q6. Interaction with Phase D flag — pool execution requires wrapped BranchDefinition?**
A. Yes. Under D-off, the fantasy daemon uses the direct-graph path which hard-codes the fantasy universe-cycle Branch with boundary-state-from-config; it cannot execute a pool task whose `branch_def_id != fantasy_author/universe-cycle`. Under D-on, the wrapper path dispatches any registered Branch with `inputs`-derived boundary state — which is what pool tasks need. Phase F's full behavior gates on D-on. Under D-off + F-on: post actions still work (writes to pool YAMLs), subscribe actions still work, but no daemon executes pool tasks. The F-flag is fully active on the write side regardless of D; only execution depends on D.

**Q7. Dispatcher-to-invocation wire-up — solve now or defer?**
A. Solve now. STATUS.md Phase E follow-up #3 deferred this, intending "when Phase D default flips OR Phase F needs it" — and Phase F needs it. Without the wire-up, the pool producer writes into queue but the dispatcher still returns observational picks; nothing executes. Phase F's whole premise fails silently (R1). Wire-up in §4.1 #1. Strictly additive — the existing D-on path (wrapper with default boundary-state seed) is preserved when dispatcher returns None or flag-off.

**Q8. Subscription durability — config extension or new file?**
A. New file `<universe>/subscriptions.json`. Rationale: subscription list changes more frequently than dispatcher config (users subscribe/unsubscribe as their interests shift; they rarely retune tier weights). Separate file → separate file-lock contention surface. Same `_file_lock` primitive Phase E introduced. Also matches the established pattern: `branch_tasks.json`, `work_targets.json`, `requests.json` are all per-universe sibling files; `subscriptions.json` fits the neighborhood.

### 4.10 Dispatcher-to-invocation plumbing shape (§R1 concrete)

Because this is the load-bearing resolution, pinning the shape precisely.

`fantasy_author/__main__.py:_run_graph` currently builds `initial_state` from config + checkpoint resume, then invokes the wrapped graph. Phase F change:

```text
(pseudocode, not committed shape)
at cycle boundary inside _run_graph loop:
    if dispatcher_enabled() and unified_execution_enabled():
        cfg = load_dispatcher_config(universe_path)
        picked = select_next_task(universe_path, config=cfg)
        if picked is not None:
            # Use claim_task (returns None on not-pending) rather than
            # mark_status(running) — handles the cancel-during-claim race
            # cleanly. _VALID_TRANSITIONS rejects cancelled→running with
            # ValueError; claim_task's return-None contract is cheaper
            # than catching the exception. See invariant 8.
            claimed = claim_task(universe_path, picked.branch_task_id,
                                 claimer=daemon_id)
            if claimed is None:
                logger.info(
                    "claim_lost_to_cancel: %s", picked.branch_task_id,
                )
                # fall through to default path this cycle
            else:
                try:
                    boundary_state = _boundary_seed_from_task(claimed, initial_state)
                    result = _invoke_wrapped_branch(claimed.branch_def_id, boundary_state)
                    mark_status(universe_path, claimed.branch_task_id, status="succeeded")
                    continue  # next cycle
                except BranchNotAvailableError:
                    mark_status(universe_path, claimed.branch_task_id, status="failed",
                                error="branch_not_available")
                    # fall through to default path
                except CompilerError as e:
                    mark_status(universe_path, claimed.branch_task_id, status="failed",
                                error=f"compile_error: {e}")
                    # fall through to default path
    # default path: existing wrapper with default boundary_state_from_config
```

Key invariants of this shape:
- `claim_task` is the ONE transition from pending→running. A concurrent `queue_cancel` that lands between `select_next_task` and the claim returns `None` cleanly — no exception path. Invariant 8 tests this.
- `mark_status` transitions the task AFTER invocation (to succeeded or failed). A crash during invocation leaves the task in `running` with `claimed_by=<daemon_id>`, and restart-recovery (Phase E invariant 7) resets it to pending.
- Compile or load failures fall through to the default path — a bad queued task doesn't starve the daemon's default work.
- `_boundary_seed_from_task` merges `claimed.inputs` into the existing config-derived boundary state, with `inputs` winning on overlapping keys. **`inputs` fields outside the 10 boundary fields are passed through for downstream consumers; the wrapper ignores them harmlessly** — the wrapper only reads the documented 10 boundary fields from its state, and the runtime StateGraph initial-state dict tolerates extras. This is where pool-task-specific context (which series, which chapter, which premise override) lives; a pool task can smuggle `inputs.active_series = "foo"` and the wrapper will seed with it even though `active_series` isn't in the 10-field boundary — LangGraph's initial_state accepts unknown keys, the inner `UniverseState` reducers pick them up downstream.
- If dispatcher returns None, code flows into the default path unchanged — no behavioral change from Phase E.
- Works for all tier origins, not just `goal_pool`. `host_request` and `user_request` BranchTasks queued by `submit_request` finally execute via this path — resolving the submit_request user-facing promise that STATUS.md 2026-04-14 flagged.

### 4.11 Pause/stop/checkpoint — inherits Phase D §4.10 + §4.11

No new pause/stop regression in Phase F. Dispatcher pick happens at cycle boundaries (same as Phase E); wrapper invocation still runs to completion within one cycle (same as Phase D). Pool-task execution is just another cycle's worth of wrapper work.

Checkpoint asymmetry: a pool task's `inputs` live in the BranchTask row on `branch_tasks.json`, not in the wrapper's outer StateGraph checkpoint. A mid-cycle crash during pool-task execution resets the BranchTask to `pending` (invariant 7) and loses the transient boundary-state merge. Pool task re-picked on next cycle restarts from scratch — matches the "disk artifacts are the truth" Phase D §4.11 resolution. No new asymmetry; just another instance of the existing one.

### 4.12 Open design questions (non-blocking, flag for host input)

1. **Maintenance pool content.** Phase F ships an empty `goal_pool/maintenance/` directory. What ARE maintenance tasks when we have them? Memo suggested "index rebuilds, KG consolidations, canon audits on public Branches the user has forked." Recommend: first wave lands as separate tasks post-F (one per maintenance-task type), populating the pool with operator-curated BranchTasks. Not blocking F.
2. **Pool claim semantics (R13).** Two subscribers race. v1 accepts double-execution. Alternatives: (a) advisory `claimed_by` field in the pool YAML, git-push-to-claim wins; (b) centralized claim registry at repo-root. Both re-introduce coordination complexity. Recommend: defer until R13 is observed in live. Most pools will be low-volume; most hosts won't subscribe to the same pools simultaneously. Formal claim semantics land in Phase G alongside bid atomicity.
3. **Slug collision.** Two Goals with similar slugs (`research-paper` vs `research-papers`) are distinct directories; posts to the wrong one silently go unnoticed by intended subscribers. Recommend: `list_subscriptions` includes post counts per subscribed goal so users notice drift. Already in §4.1 #4 (`pool_status_per_goal`). Not blocking.

(Q3 "repo-root discovery" was promoted to §4.1 #7 as a contract.)

None blocks Phase F dev. Defaults are safe.

## 5. Handoff

Dev can claim Phase F whenever Phase E is fully stable under user-sim Mission 8 (Phase D flag default flip gate). Merging is safe at any point because Phase F flag defaults to off.

Load-bearing contracts reviewer should focus on:
- **R1** dispatcher-to-invocation wire-up (§4.10 plumbing shape) — biggest correctness risk; the whole phase hinges on this being right.
- **R2 + invariant 2** registry separation — the WorkTarget vs BranchTask distinction gets re-stressed by Phase F; a wrong move here collapses Phase E's keystone.
- **R4 + invariants 1, 9** cross-universe isolation — PLAN.md's hard boundary; two-boundary validation is defense-in-depth on the most dangerous leak path.
- **R7 + invariant 3** producer-call boundary — clarifies Phase E §R9 without contradicting it; reviewer should confirm the rephrased invariant reads cleanly in context.

If any of those slip in implementation, flag back to planner before landing. Phase F is the phase where the "global goals engine" becomes real infrastructure; design errors here compound across Phases G-I.
