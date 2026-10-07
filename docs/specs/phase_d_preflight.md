---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-14. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase D Pre-Flight — Fantasy Universe-Cycle as Registered Branch

Planner draft, 2026-04-14. For dev handoff on task #5.

This is the load-bearing phase of the daemon-task-economy rollout. It unifies the autonomous fantasy loop with the user-registered Branch executor so that the "builder surface and the autonomous surface have met" (memo §Phase D). It is also the highest-blast-radius change on the board: breaking the fantasy daemon loop breaks the only domain the project currently runs end-to-end. This document maps the risk, fixes a mismatch between the rollout plan and what the compiler actually allows, picks an implementation approach, and hands dev an explicit contract.

## 1. Source material

- `docs/exec-plans/daemon_task_economy_rollout.md` §Phase D (lines 103-127).
- `docs/planning/daemon_task_economy.md` §3.4 "What changes" (lines 196-204) and §3.2 "BranchTask vs NodeBid" (144-195).
- Current `fantasy_author/graphs/universe.py` — the existing StateGraph this phase wraps.
- Current `fantasy_author/__main__.py:402-548` — the DaemonController that builds and runs the graph today.
- `tinyassets/branches.py` `NodeDefinition` (101-196), `BranchDefinition` (339+).
- `tinyassets/graph_compiler.py` `compile_branch` (506-592), `_validate_source_code` (323-338), `_build_source_code_node` (341-415).
- `tinyassets/runs.py` `execute_branch_async` (1318-1371).
- C.5 landing `a228797` (authorial_priority_review wired through producer dispatcher) — confirms Phase C contract is live.

## 2. Risk map

| # | Risk | Blast radius | Reversible? | Mitigation |
|---|------|--------------|-------------|------------|
| R1 | Flag-on path invokes `execute_branch_async` which wraps the graph in a background thread, loses the `compiled.stream(...)` loop currently driving dashboard events and pause/stop handling | Daemon runs but the user sees zero liveness — dashboard frozen, pause breaks, stop breaks | Yes (flag off) | Do NOT route through `execute_branch_async` in v1. Wrap only the graph-construction + compilation path; keep `_run_graph`'s `compiled.stream(...)` loop intact. |
| R2 | SqliteSaver checkpointing is wired into `_run_graph` via `graph.compile(checkpointer=...)`. The Branch compiler does `StateGraph(state_type)` and returns `CompiledBranch.graph` uncompiled — caller must attach the checkpointer. If dev misses this, all daemon restarts lose resume capability | Silent regression: every restart starts at 0 words | Yes (flag off) | Contract §4 locks in: the wrapped path MUST preserve `SqliteSaver.from_conn_string(...)` + `graph.compile(checkpointer=...)`. Add a test that asserts checkpoint recovery works in the flag-on path. |
| R3 | `compile_branch` synthesizes a `state_type` TypedDict from `BranchDefinition.state_schema`. The fantasy `UniverseState` has ~20 fields with custom reducers (`workflow_instructions`, `quality_trace`, `health`, `cross_series_facts`, etc.). A schema mismatch silently drops state fields | Daemon runs but with degraded state propagation — feels like flaky memory | Partial (requires running both paths side-by-side to detect) | Hard contract: the opaque-wrap node's internal graph uses the real `UniverseState` TypedDict. The OUTER Branch's `state_schema` is minimal (just what the wrapper reads/writes at the boundary). Do NOT try to reconstruct `UniverseState` as a Branch `state_schema`. |
| R4 | `graph_compiler._validate_source_code` rejects any source containing `exec(`, `__import__`, `subprocess`, `os.system`, `eval(` (lines 108-110). A wrapper that imports `build_universe_graph` and invokes it cannot be expressed as `source_code` | Rollout-plan option (b) as written is uncompilable | N/A (caught at compile time) | See §3 decision. Solution: the wrapper node is NOT a `source_code` NodeDefinition. It is a Python callable registered via a new domain-graph escape hatch. |
| R5 | `NodeDefinition.approved=True` precedent at `tinyassets/universe_server.py:2577` is the `_ext_manage` MCP action `approve` — it flips a stored registry node's approved flag. The rollout plan's "set approved=True at registration time" is valid for source_code nodes, but if the wrapper is NOT source_code, the `approved` flag becomes irrelevant | None (the precedent simply doesn't apply) | N/A | Contract §4 clarifies: the domain-trusted wrapper is not a source_code node. No approval gate applies. |
| R6 | Two execution paths (flag-off direct StateGraph, flag-on wrapped-Branch) drift over time — bug fixes land in one but not the other | Medium — masked regressions only visible when flag state flips | Yes (delete flag-off path once flag-on is proven) | Test matrix: every Phase D test runs BOTH paths. Living together is explicitly temporary — STATUS.md gets a Concern entry to delete the flag-off path after one user-sim mission green on flag-on. |
| R7 | `DaemonController._run_graph` uses `compiled.stream(...)` iterator to handle pause/stop, dashboard events, heartbeat, and cross-universe switches (`fantasy_author/__main__.py:506-548`). Any wrapping that replaces `stream` breaks all of this | Catastrophic — daemon loses all operator controls | Yes (flag off) | The wrap is structural, not executional. `build_universe_graph()` stays the same. What changes is: the graph comes from `compile_branch(branch_def).graph` instead of `build_universe_graph()` — iff flag on AND the compile succeeds. Everything downstream of "graph in hand" is unchanged. |
| R8 | Feature flag `TINYASSETS_UNIFIED_EXECUTION` does not exist yet. Naming / placement matters for cross-session legibility | Low, but will frustrate future sessions if inconsistent | Yes | Contract §4: define the flag in a single location (`tinyassets/config.py` or `fantasy_author/__main__.py` module-level), read via `os.environ.get(...)`, document in STATUS.md when flag is referenced. |
| R9 | Live-toggle of the flag (user sets `TINYASSETS_UNIFIED_EXECUTION=1` on a running universe) — the daemon is already in `_run_graph`; flag read must be at graph-construction time, not per-node | Unclear UX — user may expect instant toggle | Yes (restart required) | Document: flag takes effect on next `_run_graph` call (i.e., next daemon restart or universe switch). No mid-run toggle. Matches how other flags behave. |
| R10 | Phase C.5 `authorial_priority_review` producer wiring is live. Phase D does not change producer semantics, but if the wrapped graph re-imports Phase C machinery through a different path, producer-registration singletons could double-register | Runtime RuntimeError or silent duplicate producer output | Yes (flag off) | Hard rule: wrapped graph imports from the SAME module paths as the direct graph. No copy-paste import aliases. One source of truth for `build_universe_graph`. |
| R11 | `compile_branch(branch)` raises under flag-on (registry miss, malformed seed YAML, state_schema validation error, etc.). If the error is caught and the daemon silently falls through to the direct path, flag-on becomes a no-op that masks broken config. Next user-sim mission passes "with flag on" while actually running flag-off | Silent regression — looks correct but isn't; subsequent tests pass on a lie | Yes (flag off) | Hard-fail. Any exception from `compile_branch` OR from the registry resolution re-raises out of `_run_graph` with a clear error message. The daemon stops (not degrades). Host sees the error, fixes the config or flips the flag off, and restarts. See §4.8 success criterion. Do NOT try/except around the wrapped path. |

### Reversibility summary

Everything on the list is reversible by flipping `TINYASSETS_UNIFIED_EXECUTION=off`. The only non-reversible action in this phase is the initial landing of the wrapper code itself — and that is inert when the flag is off. If the flag stays off indefinitely, Phase D is a no-op plus ~200 lines of dead-but-tested code, which is a survivable outcome.

## 3. Option (a) vs option (b) — decision

**Memo §3.4 lists two bridging options.** The rollout plan picked option (b); this preflight confirms the choice but REVISES the implementation shape because (b) as literally written is uncompilable (R4).

### Option (a) — Trusted-domain carve-out

Add a `trusted_domain: str = ""` field to `BranchDefinition`. `compile_branch` exempts nodes in trusted-domain branches from sandbox approval. Fantasy's full graph topology (foundation_priority_review → authorial_priority_review → dispatch_execution → run_book | worldbuild | reflect | idle → universe_cycle) becomes ~7 separate `NodeDefinition`s, each with their real Python callables surfaced through an import hook. Per-phase sandbox compatibility required for all 7 phases.

**Advantages:** Per-phase inspection. User can see the fantasy daemon's phases as first-class Branch nodes. Per-phase user extensions become possible ("swap reflect with my custom reflect").

**Disadvantages:** High migration cost. Every fantasy phase must be audited for sandbox-compatible signature. State schema for `UniverseState` must be expressed in `state_schema` JSON — the ~20 fields + custom reducers need faithful reconstruction. Conditional edges (`route_after_foundation_review`, `route_dispatched_task`, `should_continue_universe`) must round-trip through `ConditionalEdge` JSON representation. High chance of silent state-propagation bugs (R3).

### Option (b) — Opaque-node wrapping [PREFERRED, revised]

Express the fantasy universe-cycle as a single-node BranchDefinition whose one node invokes the existing `build_universe_graph()` StateGraph unchanged. The internal state machine is opaque to the Branch layer.

**Original framing (memo + rollout):** "set `NodeDefinition.approved=True` at registration time using the existing domain-trusted precedent at `tinyassets/universe_server.py:2577`" — implies a `source_code` node carrying code that calls `build_universe_graph()`.

**Problem:** `_validate_source_code` at `tinyassets/graph_compiler.py:108-110` blocks `__import__`, which means a `source_code` node cannot import the real `build_universe_graph`. The node would have to inline the entire fantasy graph as a literal source string. That defeats the "invokes the existing StateGraph unchanged" promise.

**Revised approach:** Introduce a **"domain-trusted node"** concept in `compile_branch`. A NodeDefinition with `domain_id` matching a registered trusted domain AND `source_code == ""` AND `prompt_template == ""` is resolved to a Python callable via a domain-registered callable lookup. This is cleaner than adding a `trusted_domain` field on `BranchDefinition` (option a) because:
- It's node-level, not branch-level — composes with future mixed branches.
- It requires zero changes to existing Branch storage (the JSON shape is unchanged; only the resolver is new).
- The callable is looked up by `(domain_id, node_id)` in a registry that lives in `fantasy_author/branch_registrations.py` — dev owns the registry, host controls what's in it.

**Advantages of revised (b):** Preserves the existing graph unchanged. Zero fantasy phase code moves. Unification lands at the queue + inspection layer (memo §3.4's explicit goal). Small, testable compiler change (one new node-resolution branch). Reversible via flag.

**Disadvantages:** Per-phase inspection stays where it is (no new user-facing legibility). Forks of the fantasy Branch are opaque — a user can see "universe-cycle" as a Branch but cannot see inside it. This is the deliberate tradeoff memo §3.4 already made.

### Decision: revised option (b), implemented as "domain-trusted opaque node"

Option (a) stays deferred to a future phase, as the rollout plan already anticipated. Revisit when (i) a second domain lands, or (ii) users actively want to extend fantasy at the phase level — whichever comes first.

## 4. Implementation contract

Scope boundaries, signatures, invariants, and non-goals. Dev fills in HOW.

### 4.1 Deliverables

1. **Two-module registry pattern — engine-agnostic dict + domain-local entries.** Decoupled to preserve PLAN.md's engine/domain separation. Engine side never imports domain side.

   - **New `tinyassets/domain_registry.py`** — domain-agnostic infrastructure. Holds the `(domain_id, node_id) → Callable[[dict], dict]` dict plus `register_domain_callable(domain_id, node_id, fn)` and `resolve_domain_callable(domain_id, node_id) -> Callable | None` helpers. Imported by `tinyassets/graph_compiler.py`. Knows nothing about fantasy.
   - **New `fantasy_author/branch_registrations.py`** — domain-side. On import, calls `register_domain_callable("fantasy_author", "universe_cycle_wrapper", _wrapper_fn)` with a thin wrapper that invokes `build_universe_graph()` and returns its compiled result. No classes, no registry state of its own — module-level side-effect registration.
   - **Side-effect import in `fantasy_author/__main__.py`** — `import fantasy_author.branch_registrations  # noqa: F401  # registers domain callables` near the other domain imports. Must run before the first `compile_branch` call under flag-on. `noqa: F401` to silence unused-import warnings; the import *is* the registration.

   This pattern resolves dev's correct concern that the original wording ("registry lives in `fantasy_author/branch_registrations.py`") would force `tinyassets/graph_compiler.py` to import from `fantasy_author/`, violating the engine/domain decoupling. Under this pattern, the dict + helpers are engine-agnostic; only the entries (the `(domain_id, node_id, callable)` triples) are domain-local.

2. **Compiler extension in `tinyassets/graph_compiler.py`** — `_build_node` gains a third branch (after `has_template` and `has_source`): if `(domain_id, node.node_id)` resolves to a registered opaque callable, use it. The callable receives the node's inputs as `state: dict` and returns `dict`. No approval gate (domain-trusted registry is host-controlled at registration time, not per-invocation).

   **Threading contract (Option B, reviewer-confirmed).** `NodeDefinition` does NOT have a `domain_id` field — `domain_id` lives on `BranchDefinition` (`tinyassets/branches.py:360`). Do not read `node.domain_id`; it will `AttributeError` at compile time. Instead, thread `domain_id` as an explicit parameter:
   - `_build_node(node, *, domain_id: str = "", provider_call, event_sink)` — new kwarg, default empty string.
   - `compile_branch(branch, ...)` passes `domain_id=branch.domain_id` into every `_build_node` call (one site, `tinyassets/graph_compiler.py:559-561`).
   - Opaque-node resolution: `if not has_template and not has_source and domain_id: lookup = resolve_domain_callable(domain_id, node.node_id); if lookup: return _build_opaque_node(lookup, node, event_sink=event_sink)`.
   - Empty `domain_id` OR unregistered `(domain_id, node_id)` with no source/template → existing `CompilerError("Node must have either prompt_template or source_code")`. Preserves the current error shape for user Branches that omit both.

   Option A (pass the full `BranchDefinition` into `_build_node`) was rejected — it creates new coupling between node-level build and branch-level shape when only one field is needed. Option C (add `domain_id` to `NodeDefinition`) is explicitly forbidden by §4.6 (no `BranchDefinition` or `NodeDefinition` dataclass field changes).

3. **New `BranchDefinition` seed at `fantasy_author/branches/universe_cycle.yaml`** — single node `universe_cycle_wrapper`, entry_point = that node, single edge to END. `domain_id = "fantasy_author"`. `published = False` (internal, not a user-facing template). State schema minimal — just the fields the wrapper reads/writes at the boundary (`universe_id`, `universe_path`, `premise_kernel`, `health`, counters for dashboard; the real `UniverseState` lives INSIDE the wrapped graph, not at the Branch layer).

4. **Flag-gated branch in `fantasy_author/__main__.py`** `_run_graph`. Default (flag off): `graph_builder = build_universe_graph()` as today. Flag on: load the registered Branch, call `compile_branch(branch)`, use `CompiledBranch.graph` as `graph_builder`. Everything after (SqliteSaver compile, initial_state, stream loop, pause/stop, heartbeat, dashboard events) is identical across both paths.

5. **Tests** under both flag settings (see §4.4).

### 4.2 Flag

- Name: `TINYASSETS_UNIFIED_EXECUTION`. Same name as the rollout plan so future sessions find it.
- Default: off (empty or `"0"` or `"false"`).
- Read: at the start of `_run_graph`, one call to `os.environ.get("TINYASSETS_UNIFIED_EXECUTION", "").strip().lower() in {"1", "true", "yes", "on"}`. Same pattern as `_gates_enabled()` at `tinyassets/universe_server.py:6621-6625`.
- Scope: process-level. Takes effect on next `_run_graph` call (daemon restart or universe switch). No mid-run toggle.

### 4.3 Invariants (must hold across both flag states)

1. **Checkpoint resume recovers boundary state + disk-persisted artifacts.** Daemon restart recovers the full 10-field boundary — `universe_id`, `universe_path`, `premise_kernel`, `health`, `total_words`, `total_chapters`, `world_state_version`, `canon_facts_count`, `active_series`, `series_completed` — from SqliteSaver, AND `_build_book_execution_seed` correctly re-derives work-target position from disk-persisted scenes/notes/canon. The 10 fields match `_BOUNDARY_FIELDS` in `fantasy_author/branch_registrations.py` and the `state_schema` of `fantasy_author/branches/universe_cycle.yaml`. Under flag-on, mid-cycle TypedDict reducer contents (`workflow_instructions`, `quality_trace`, in-flight dispatch state) are explicitly NOT part of the resume guarantee — see §4.11. Test: simulate a restart with a populated checkpoint at a cycle boundary and assert the 6 restore-critical counters (`total_words`, `total_chapters`, `world_state_version`, `canon_facts_count`, `active_series`, `series_completed`) + on-disk-derived position carry over under both flag states. (Restore test covers the 6 counters for practical resume verification; the full 10-field boundary round-trips the wrapper boundary. The YAML seed's `input_keys` is 9 — the 10 boundary fields minus `health`, which the wrapper returns rather than consumes — and `output_keys` is 7, the non-identity fields the wrapper actively writes back.)
2. **Dashboard events fire.** Every phase entry produces a `phase_start` event visible to `_handle_node_output`. Test: run one cycle, assert the dashboard received events for foundation_priority_review, dispatch_execution, and universe_cycle.
3. **Pause/stop/universe-switch controls respond at wrapper-call granularity under flag-on.** Setting `_stop_event` during inner-graph execution is observed by `_run_graph` when the wrapper returns — NOT during inner-phase execution. `.pause` file is checked at the outer stream-loop level (which under flag-on iterates once per wrapper invocation, not once per inner phase). Cross-universe switch triggers in `finally` block as before. Response latency is bounded by the slowest inner phase (typically `run_book` = minutes, not seconds). See §4.10 for the full acceptance rationale. Test (new, wrapped-path only): `_stop_event.set()` between two wrapper invocations → daemon exits at next iteration. Test (new, both paths): pause file halts the outer stream loop under both flag states.
4. **Producer-registration singletons are not double-registered.** No duplicate producer instance in the Phase C registry after the wrapped graph is built. Test (new, both paths): after `_run_graph` setup, introspect the producer registry and assert `len({id(p) for p in registry}) == len(registry)` — i.e., count distinct producer function objects by `id()`, not by tag or display name (identical re-registrations share a tag but have different `id()`, which would hide duplicates the other way around; `id()` catches true double-registration).
5. **No hidden state field drops across the wrapper boundary.** Every field written by a node inside the wrapped graph is readable by the next node inside the wrapped graph. The OUTER Branch's state_schema only covers the boundary. Test (new, wrapped-path only, new file `tests/test_phase_d_unified_execution.py`): drive one full universe cycle with flag on, assert `workflow_instructions.selected_target_id` set by `dispatch_execution` is readable by `run_book`, AND the boundary-returned state (what the wrapper returns to the outer Branch layer) contains the expected counters (`total_words`, `total_chapters`, `health`). Not an extension of an existing test — the wrapped-path boundary doesn't exist under flag-off, so this is new coverage.
6. **Activity log matches flag-off.** `.agents/activity.log` entries for a single cycle are byte-identical (modulo timestamps and random IDs) across both flag states. Test: diff log output.

### 4.4 Test strategy

New file `tests/test_phase_d_unified_execution.py`. Structure:

- **Compiler-extension tests (5):** domain-trusted opaque node resolves via registry; unregistered `(domain_id, node_id)` with no source/template raises clear `CompilerError`; opaque node bypasses `_validate_source_code`; `approved` flag is ignored for opaque nodes; empty `domain_id` on a node with no source/template raises `CompilerError` (preserves existing error for user Branches that omit both).
- **Wrapper-registration tests (2):** `universe_cycle_wrapper` is registered under `("fantasy_author", "universe_cycle_wrapper")`; the wrapper callable returns a dict with expected boundary fields.
- **Flag-gated dispatch tests (3):** flag-off path calls `build_universe_graph()` directly; flag-on path calls `compile_branch(...)` then uses its `graph`; flag parsing accepts `"1"`, `"true"`, `"yes"`, `"on"` (mirrors `_gates_enabled`).
- **Flag-on compile-failure test (1, R11):** monkey-patch the domain registry to return `None` for the wrapper lookup; flag-on `_run_graph` raises (does not silently fall through to direct path).
- **Pause/stop latency tests (2, per §4.3 invariant 3 and §4.10):** under flag-on, `_stop_event` set during wrapper execution is observed at next wrapper boundary (NOT mid-inner-phase); `.pause` file halts the outer stream loop under both flag states.
- **Producer no-double-register test (1, per §4.3 invariant 4):** after `_run_graph` setup under each flag state, assert `len({id(p) for p in registry}) == len(registry)`.
- **State-field boundary test (1, per §4.3 invariant 5, wrapped-path only):** drive one universe cycle with flag on; assert `workflow_instructions.selected_target_id` survives `dispatch_execution` → `run_book` inside the wrapper, and the returned boundary state contains expected counters.
- **Other parity tests (4, parameterized over flag states → 8 actual tests):** checkpoint resume, dashboard events, universe switch, activity.log byte-parity.
- **Regression safety (2):** with flag off, full existing `test_universe_graph.py` suite still green; with flag on, same suite green when opted in via env in the test runner.

Aim: ~24 new tests. If dev hits a test that's ambiguous about what "identical behavior" means, raise it — the invariant list in §4.3 is the bar. Notable asymmetry: §4.3 invariant 3 is explicitly NOT parity-asserted (see §4.10); flag-on responds at wrapper-boundary granularity, flag-off at inner-node granularity. Tests must respect that split.

### 4.5 Rollback plan

**Landing state:** flag off by default. Merging is safe. If the flag is never flipped, nothing changes for any user.

**Flag-on rollout gate:** user-sim must complete one full mission (write at least one scene, restart at least once, see dashboard liveness) with `TINYASSETS_UNIFIED_EXECUTION=1` before the flag default flips to on. Lead enforces this gate.

**If flag-on breaks in live:** three escalating steps, in order:
1. **Immediate:** host sets `TINYASSETS_UNIFIED_EXECUTION=0` (or unsets it) in the tray/environment and restarts the TinyAssets daemon. Daemon resumes on the direct path. No data loss; SqliteSaver checkpoint is shared between paths by design.
2. **Short-term:** git revert of the `fantasy_author/__main__.py` flag-gate edit if the flag itself is not holding. Wrapper module + compiler extension stay in place — they're inert without the caller.
3. **Full rollback:** revert the Phase D landing commit entirely. Reverts wrapper, compiler extension, seed YAML, tests. Flag stays defined but unreferenced — no harm.

The SqliteSaver database format is unchanged by Phase D, so downgrade safety is automatic **under flag-off**: a daemon running the old code reads a flag-off checkpoint written by the new code just fine (the checkpoint is the UniverseState payload, and UniverseState itself is unchanged). **Under flag-on**, the checkpoint payload is different in shape — see §4.11 for the implication. A cross-flag downgrade on a checkpoint written under flag-on is not a supported path; the clean-resume story is "flip the flag off, restart, daemon rebuilds from current files + notes."

**Flag-on hard-fail paths share the same contract, different exception types.** Per R11, the wrapped-Branch path propagates failure out of `_run_graph` without falling back to the direct path. Two distinct failure modes raise distinct exception types but both trigger the same "daemon stops, host fixes config, restarts" recovery: a missing seed YAML (`fantasy_author/branches/universe_cycle.yaml` absent) raises `FileNotFoundError`; a registry miss (no `(domain_id, node_id)` match when the YAML loads but the domain-trusted callable isn't registered) raises `CompilerError`. Either error surfaces clearly in the tray/log — flip flag off, restart, investigate.

**Do NOT:** schema-migrate anything, rewrite any existing checkpoint, touch `tinyassets/branches.py`'s `BranchDefinition` dataclass fields, or change any storage-layer shape. If dev finds they need any of those, the scope has drifted — raise it before implementing.

### 4.6 Non-goals (explicit)

- Per-phase inspection of the fantasy graph from the Branch layer. That's option (a), future work.
- Moving `fantasy_author/` to `domains/fantasy_author/`. The rollout plan's path assumes a rename that hasn't happened. Preserve current paths; STATUS.md work-row should be edited to reflect `fantasy_author/__main__.py` (no `domains/` prefix).
- Changing `UniverseState` shape, fields, or reducers.
- Routing through `execute_branch_async`. Explicitly rejected per R1.
- Touching `tinyassets/branches.py` (no new fields on `BranchDefinition`).
- Modifying Phase C producer contract. Phase D is a wrapping layer, not a producer-path change.
- Exposing the wrapped universe_cycle Branch to user-registered fork paths. The seed YAML is `published=False`. If users accidentally fork it, they get an opaque-node-with-no-registry entry — a clean error, not a silent failure.
- NodeBid path (§3.2). That's Phase G.
- DaemonController tier-aware dispatch (that's Phase E).
- **Adding `domain_id` (or any other field) to `NodeDefinition`.** Option C from reviewer's three-way ranking. Forbidden here. `domain_id` is threaded as a `_build_node` kwarg (Option B), not as a new dataclass field. Dev: if you find yourself wanting to stash `domain_id` on the node, stop — the threading-through-`compile_branch` path in §4.1 #2 is the agreed contract.
- Adding `trusted_domain` (or any other field) to `BranchDefinition`. That's option (a), future work per §3.

### 4.7 Files touched

| File | Change | Size estimate |
|------|--------|---------------|
| `tinyassets/domain_registry.py` | NEW — engine-agnostic dict + `register_domain_callable` + `resolve_domain_callable` helpers. Knows nothing about fantasy. | ~40 lines |
| `fantasy_author/branch_registrations.py` | NEW — module-level side-effect registration: imports `register_domain_callable` + the fantasy wrapper callable, calls register at import time | ~40 lines |
| `fantasy_author/branches/universe_cycle.yaml` | NEW — single-node Branch seed | ~30 lines |
| `tinyassets/graph_compiler.py` | EDIT `_build_node` signature (`domain_id` kwarg) + add opaque-node branch via `resolve_domain_callable` + pass `domain_id=branch.domain_id` at the single `_build_node` call site in `compile_branch` (~line 559) | ~45 lines added / 2 edited |
| `fantasy_author/__main__.py` | EDIT `_run_graph` — flag gate on graph construction + side-effect import of `fantasy_author.branch_registrations` | ~22 lines changed |
| `tests/test_phase_d_unified_execution.py` | NEW — ~20 tests | ~400 lines |
| `docs/exec-plans/daemon_task_economy_rollout.md` | EDIT — mark Phase D done when landed, note the revised option-(b) shape | ~10 lines |
| `STATUS.md` | EDIT — delete Phase D row, add "Phase D landed; user-sim gate pending flag flip" concern | 2 lines |

No new subsystems, no new schemas, no new MCP tools. ~550 lines net, most of which is tests.

### 4.8 Success criteria (for reviewer)

- All tests in §4.4 green on both flag values.
- Existing full suite green on default flag off (the landing is a no-op at rest).
- Compiler extension is strictly additive — no existing compile_branch test regresses.
- One user-sim mission runs cleanly end-to-end with flag on: premise set, at least one scene written, daemon restarted mid-mission, dashboard events observed, no double-produced tasks, activity.log byte-identical to flag-off reference run (modulo timestamps).
- Reviewer sees no change to `UniverseState`, `BranchDefinition`, `NodeDefinition`, or storage schemas.
- **Compile failure on the wrapped Branch is observable** (R11). `compile_branch` exceptions or registry misses under flag-on propagate up out of `_run_graph` with a clear error, rather than silently falling through to the direct-graph path. Test (new): monkey-patch the registry to return `None` for the wrapper lookup under flag-on, assert `_run_graph` raises and the daemon stops (does not degrade to direct path).

### 4.9 Open design questions (non-blocking, flag up as they surface)

1. **Registry location — resolved.** Engine-agnostic dict + helpers live in `tinyassets/domain_registry.py`; domain-local entries live in `fantasy_author/branch_registrations.py` via module-level side-effect registration. This preserves PLAN.md's engine/domain decoupling: `tinyassets/graph_compiler.py` imports `resolve_domain_callable` from `tinyassets/domain_registry.py`, never from any domain. See §4.1 #1 for the full pattern.
2. **Should the wrapper callable emit per-node events into the Branch layer's `event_sink` so the Branch inspection surface sees "universe_cycle is running"?** Recommend: v1 emit a single `running` event at wrapper start and a single `completed` event at wrapper end. Richer per-phase event surfacing is option (a) territory — don't overreach here.
3. **Does the seed YAML go under VCS, or is it generated at startup?** Recommend: VCS. It's a tiny static file; keeping it in-repo makes the "fantasy universe-cycle is a registered Branch" claim inspectable in the tree.

None block implementation; defaults are safe.

### 4.10 Pause/stop latency regression under flag-on (accepted for v1)

Reviewer flagged this on the first audit pass; confirming acceptance here so dev doesn't treat it as a defect.

**What changes.** Under flag-off, `_run_graph` calls `compiled.stream(initial_state, ...)` against the real universe-cycle StateGraph and iterates per inner phase. `_stop_event` and the `.pause` file are checked between each node output (every few seconds during normal operation, up to minutes during `run_book`). Under flag-on, the graph the outer loop sees has one node — the opaque wrapper. The wrapper internally builds and runs the full fantasy graph to a stopping point, then returns. The outer stream loop checks `_stop_event` / `.pause` only at wrapper boundaries (once per universe cycle), so response latency is bounded by the slowest inner phase.

**Practical impact.** Existing latency characteristics:
- Stop signal: currently a few seconds (handled between any two inner node outputs). Under flag-on, matches the slowest inner phase — minutes for `run_book`, seconds for `reflect` / `worldbuild` / `idle`.
- Pause: same story. A `.pause` file dropped mid-`run_book` is not honored until `run_book` returns.
- Cross-universe switch: unchanged — still handled in the outer `finally` block after the stream loop exits.

**Why accept it for v1.** Fixing this properly means making the opaque wrapper stream-iterate the inner graph and re-check `_stop_event` / `.pause` between inner steps — which requires either (a) passing the outer controls down into the wrapper, breaking the opacity that makes the wrap small and testable, OR (b) giving the wrapper its own `stream` iterator that the outer loop consumes, which roughly doubles the wrapper size and reintroduces the `execute_branch_async` concerns listed in R1/R7. Neither is worth the complexity when the flag is off-by-default and the flag-on path is opted into knowingly by the host.

**What dev owes.**
- §4.3 invariant 3 tests pause/stop at wrapper-boundary granularity under flag-on, not per-inner-phase. Do not write a test that asserts stop responds within one inner phase under flag-on; that test would pin a promise we're not making.
- Document the regression in the commit message so future sessions can find it.
- If a future user complaint surfaces (e.g., "stop takes too long under flag-on during long book writes"), the fix is the stream-iterating wrapper. File it as a follow-up at that point; don't preemptively build it.

This acceptance is scoped to v1. Option (a) from §3 (per-phase inspection) would supersede it — per-phase becomes per-node at the outer stream layer, and the regression resolves naturally.

### 4.11 Checkpoint-state asymmetry under flag-on (accepted for v1)

Surfaced by dev mid-implementation. Documenting the accepted resolution so future sessions don't re-litigate.

**What's different.** Under flag-off, SqliteSaver checkpoints the full `UniverseState` TypedDict — ~20 fields including `workflow_instructions`, `quality_trace`, `health`, `cross_series_facts`, inner counters, and the transient work-target state. A mid-cycle crash restores the daemon to within one node of where it was. Under flag-on, the outer `StateGraph` checkpointer only sees the outer Branch's state_schema — the 10 boundary fields (`universe_id`, `universe_path`, `premise_kernel`, `health`, `total_words`, `total_chapters`, `world_state_version`, `canon_facts_count`, `active_series`, `series_completed`). The inner `build_universe_graph()` StateGraph, compiled freshly inside the opaque wrapper for each invocation, has its own ephemeral in-memory state that is discarded when the wrapper returns. A mid-cycle crash under flag-on restores boundary state only — `workflow_instructions.selected_target_id`, `quality_trace`, in-flight dispatch state, and any partial subgraph progress are gone.

**Practical impact.** Clean-completion semantics are identical across flag states — the wrapper returns the counters that matter, the outer checkpoint stores them, next `_run_graph` resumes from those. Mid-cycle crash recovery diverges:
- Flag-off mid-cycle crash: resume mid-node (within one `compiled.stream(...)` step of the failure point).
- Flag-on mid-cycle crash: resume at the start of a universe cycle — work-target selection re-runs, dispatch re-decides, any partial scene/chapter/book in flight is restarted from its last disk-persisted state (scene files, notes, knowledge graph).

**Why accept it for v1.** Three alternatives were considered and rejected:
1. **(Chosen) Accept the regression.** Disk-persisted artifacts (scene files, notes.json, knowledge graph, work_targets) ARE the durable truth. The in-memory `UniverseState` reducer blob is transient metadata — losing it costs one re-dispatched cycle, not hours of work. The existing `_build_book_execution_seed` at `fantasy_author/graphs/universe.py:117-255` already re-reads from disk to seed each cycle, precisely so restarts are robust. The daemon is designed to treat disk as authoritative.
2. **Inflate the outer state_schema to carry the full UniverseState.** Breaks the opacity that makes the wrap small and testable. Forces round-tripping the ~20 fields + custom reducers through `state_schema` JSON (R3 concern). Defeats the rationale for option (b).
3. **Nest the inner StateGraph's compiled checkpointer under the outer SqliteSaver.** LangGraph doesn't cleanly support nested checkpointers with shared SqliteSaver thread_ids — doable with significant machinery, but reintroduces the `execute_branch_async` complexity from R1/R7 and multiplies the surface area of the wrap.

**What dev owes.**
- Test (new, flag-on): crash-simulation between wrapper invocations → assert boundary state (counters, universe_id) recovers correctly, disk-persisted scenes/notes/canon survive the crash and feed the next `_build_book_execution_seed`.
- Test (new, flag-on, negative): do NOT assert mid-cycle `workflow_instructions.selected_target_id` survives a crash under flag-on. A test that does so pins a promise we're not making; explicitly note in §4.3 invariant 1 that checkpoint-resume parity applies to boundary state and disk artifacts, not to mid-cycle TypedDict reducer contents.
- Document the asymmetry in the commit message.
- Mention `_build_book_execution_seed` in the commit message as the reason the crash is recoverable — future sessions should understand why this works.

**Follow-up pointer.** Option (a) resolves this too: per-phase inspection means the outer StateGraph has all the fantasy phases as first-class nodes, and the full UniverseState becomes the outer state_schema. Checkpoint asymmetry disappears when per-phase asymmetry does. Don't preemptively build the workaround.

## 5. Handoff

Dev can claim task #5 when C.5 and the in-flight Sporemarch b/dispatch-routing fixes have landed (they have, per session wrap). Feature flag off means merging is safe. Ask lead before flipping the default to on.

Raise any cross-session concerns via STATUS.md. Raise any compiler-contract concerns directly with reviewer before coding the `_build_node` branch — a bug there regresses every user-registered Branch.
