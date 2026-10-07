---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-14. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase H Pre-Flight — Host Dashboard + MCP Inspect Surfaces

Planner draft, 2026-04-14. For dev handoff on Phase H rollout row.

Phase H is where the user first *feels* everything Phases C-G built. Until now, the dispatcher scores tasks, the pool producer pulls cross-universe work, the bid market routes economic signal, the outcome-gate ladder tracks real-world impact — and none of it is visible in one place. A user who turns their daemon on and watches it for ten minutes currently sees: an MCP `queue_list` snapshot, an MCP `list_subscriptions` snapshot, an MCP `list_open_bids` snapshot, activity.log tail, and whatever they can piece together from individual Goal/Branch `get` calls. Each snapshot is accurate; none is operational. Phase H closes this gap with a single aggregated view (`daemon_overview` MCP action) and extends the existing tray + per-universe dashboards to surface the full tier ladder with toggles. This is also the phase that consolidates ~15 accumulated non-blocking Concerns from Phases D-G into observable surfaces instead of STATUS.md backlog lines. Flag defaults for D/F/G flip as part of Phase H's rollout gate after user-sim Mission validates the integrated UX.

## 1. Source material

- `docs/exec-plans/daemon_task_economy_rollout.md` §Phase H (lines 208-233) + §Cross-phase notes + §What-does-not + §Reviewer-audit checklist.
- All four prior preflights (`phase_d_preflight.md`, `phase_e_preflight.md`, `phase_f_preflight.md`, `phase_g_preflight.md`) — invariant continuity, flag-matrix discipline, load-bearing-contracts pattern.
- Live code: `tinyassets/desktop/dashboard.py` (`DashboardMetrics`, `DashboardHandler` at lines 20 + 123) + `tinyassets/desktop/tray.py` (`TrayApp`). Both exist and are wired through `fantasy_author/__main__.py:DaemonController._tray` + `_dashboard`. Phase H EXTENDS these; it does not rebuild them from zero.
- `fantasy_author/__main__.py:_run_graph` — dispatcher wire-up from Phase F + G routing. Phase H adds observability taps, not new routing.
- `tinyassets/dispatcher.py` — `tier_status_map()` already returns self-documenting tier states (Phase F).
- `tinyassets/branch_tasks.py` — `read_queue`, `read_archive`, `get_archive_summary` helpers exist (Phase E GC landing).
- `tinyassets/subscriptions.py` — `list_subscriptions_with_drift` exists (Phase F drift detection).
- `tinyassets/universe_server.py` — existing MCP actions to consolidate: `queue_list` + `queue_cancel` (Phase E), `subscribe_goal` / `unsubscribe_goal` / `list_subscriptions` / `post_to_goal_pool` (Phase F), `submit_node_bid` / `list_open_bids` / `cancel_node_bid` / `list_daemon_llm_capabilities` (Phase G).
- `memory/project_paid_requests_model.md` — earnings dashboard implication: "a host running a daemon sees a dashboard of incoming offers, earnings per hour, and can promote/demote request classes."
- STATUS.md Concerns (lines 37-85) — ~19 active Concerns across Phases D/E/F/G/6.x. Phase H audits which become observable surfaces versus remain backlog (§4.9 Q9 decision log).

## 2. What exists vs. what Phase H adds

Understanding existing surfaces matters more for Phase H than prior phases because it's mostly extension work.

**Already live (after Phase G.2):**
- `tinyassets/desktop/dashboard.py:DashboardHandler` — receives `{"type": "phase_start", ...}`/`{"type": "draft_progress", ...}`/etc. events from `_run_graph` stream loop. Tracks `DashboardMetrics` (words/hour, chapters complete, current scene, last verdict).
- `tinyassets/desktop/tray.py:TrayApp` — system tray icon with Start/Pause/Resume/Restart/Exit menu. Per-daemon instance; host can run multiple tray apps for multiple universes (one tray per daemon process).
- Runtime status JSON at `<universe>/.runtime_status.json` — heartbeat file the tray polls (~5s cadence). Contains `{universe_id, phase, status, last_verdict, ...}`.
- Activity log at `<universe>/activity.log` — per-node event stream. Append-only. Tail-readable.
- `status.json` at `<universe>/status.json` — slower-cadence (~30s) summary file.
- 15 MCP read actions across `universe` tool: queue_list, list_subscriptions, list_open_bids, list_daemon_llm_capabilities, gates get_ladder / list_claims / leaderboard, goals get / leaderboard, branch get, inspect, list_runs, run get, get_progress, get_status.

**Phase H adds:**
- **`daemon_overview` MCP action on `universe` tool** — the aggregated view. One call returns: dispatcher config + tier_status_map, top-N pending BranchTasks from queue with tier labels, subscriptions list with drift flag, active bids summary (count per status), recent settlements summary, gate claims on this universe's bound Goal (if any), active-Branch identity + run state, recent activity.log tail (last N lines). Designed to render as one glance-view in any MCP client.
- **Tier-toggle MCP actions** — `set_tier_config(tier, enabled)` covering `accept_external_requests`, `accept_goal_pool`, `accept_paid_bids`, `allow_opportunistic`. Writes `<universe>/dispatcher_config.yaml`. Read-through via `daemon_overview`.
- **Dashboard extensions** (`tinyassets/desktop/dashboard.py`): add `DispatcherPane` (tier status + toggles) + `QueuePane` (top-N pending with tier badges) + `EarningsPane` (settlements count + bid outcomes, gated by paid market flag). Feed from the same `daemon_overview` response.
- **Tray menu additions** (`tinyassets/desktop/tray.py`): "Show Dashboard" submenu per-universe (matches PLAN.md "one tray, many dashboards"), "Toggle tier X" submenu per tier, "Pause all tiers" emergency switch. Emergency pause sets all four accept flags to false via `set_tier_config`.
- **`node_lookup_fn` wiring for G.1 follow-up #1** — Phase G's `NodeBidProducer(node_lookup_fn=None)` default means producer-side sandbox layers 1+2 are no-ops (executor catches at run time). Phase H wires the lookup function at daemon startup so the producer rejects pre-pick — as originally intended. See R4.
- **Activity.log byte-parity automation (Phase D follow-up #4)** — a pytest fixture that runs a cycle under flag-off and flag-on in the same universe and diffs activity.log. Manual verification becomes automated; Phase D's flag-default-flip gate becomes re-runnable. See R6.
- **Multi-daemon git race stress test (G.1 follow-up #3)** — adversarial claim race test harness. Two daemon processes target the same bid via real git worktrees. Not a user-sim mission — an engineering test. See R5.
- **`bid_ledger.py` → `bid_execution_log.py` cosmetic rename (G.2 #4)** — bundled into the Phase H cleanup sweep alongside docstring reconcile for `list_subscriptions` (Phase F follow-up).
- **Flag-default-flip rollout plan** — Phase H preflight DOCUMENTS the flip criteria + sequencing but does NOT perform the flip in its landing commit. The flip is a separate follow-up commit gated on user-sim mission green. See §4.5.
- **Private-Branch visibility surface in dashboard** (task #8 adjacent) — not resolving the design question; giving the host a UI location to see private branches and SELECT a visibility model conversationally with a future chat client. See §4.9 Q8.
- **"Concerns audit" doc pass** — all 19 STATUS Concerns get classified: *observable via dashboard in H* / *surfaced as daemon_overview field* / *backlog with explicit tracking ticket* / *ready to resolve/delete*. Touch every Concern; improve legibility or archive. See §4.9 Q9.

**Explicitly NOT in Phase H:**
- Full conversational chat surface for tier steering. Memo §4.4 UX check mentions "promote/demote Task Y, subscribe to Goal Z" as chatbot affordances; Phase H surfaces the MCP actions but doesn't add natural-language-to-action parsing. Phase I territory.
- Settlement token migration. `settled: false` records sit; token-launch phase handles.
- Distributed multi-host dashboard. Each host still sees their own universes; cross-host federation is multi-host phase, not H.
- New execution paths. No new producers, no new executors, no schema changes.
- Evidence URL fetching. `daemon_overview` shows bid's `evidence_url` but doesn't probe it.
- Full claim-atomicity upgrade beyond G.2. Phase G's partial resolution stands.
- Web/remote dashboard. Local-first; dashboard surfaces are desktop-native.
- Advanced earnings projections, per-LLM profitability analysis, bid-market statistics. Phase H ships the raw count/status surface; analytics is a later phase.

## 3. Risk map

| # | Risk | Blast radius | Reversible? | Mitigation |
|---|------|--------------|-------------|------------|
| R1 | `daemon_overview` aggregation becomes a hot-path performance bottleneck — the MCP action reads queue + subs + bids + settlements + gates + runs on every call; at scale (100+ queue entries, 50+ bids) each call does ~6 disk reads + JSON parses. Clients polling `daemon_overview` every few seconds spike daemon CPU | Low-medium — user-perceived sluggishness, possible dispatcher-cycle jitter | Yes (cache) | Cheap mtime-cache on each underlying read (read_queue, read_subs, read_bids, read_settlements) — already the pattern in F's GoalPoolProducer. `daemon_overview` reads are idempotent; 1s TTL on the aggregated response is plenty. Cap top-N in response bodies (default N=20 queue items, 20 bids, 10 settlements). `limit` parameter for callers that want more. |
| R2 | Tier-toggle race — user flips `accept_goal_pool` via MCP; dispatcher is mid-cycle and has already selected a pool task; now what? Two answers: (a) let the running task complete; (b) abort. Undocumented = inconsistent UX across providers | Low (specific task run) | Yes | (a). Flip affects the NEXT cycle's `select_next_task`. In-flight task completes normally. Mirrors Phase D pause/stop semantics (§4.10 there) — flag reads happen at cycle boundaries, not mid-cycle. Test (new, invariant 3): seed queue with pool task; start execution; flip `accept_goal_pool=false` during execution; assert task completes + dispatcher skips pool tier next cycle. |
| R3 | Dashboard pane corruption — Phase H extends existing `DashboardHandler` with new panes. A bad event shape from one pane crashes the PyQt/tkinter UI thread, takes out ALL dashboard panes (not just the misbehaving one) | Medium — daemon keeps running (graph thread separate) but user loses visibility | Yes (tray "Restart Dashboard" button already exists) | Pane-level exception isolation: every new pane's `handle_event` wrapped in try/except that logs + returns. Existing `DashboardHandler.handle_event` should adopt the same pattern (small refactor, separate commit). Dashboard-thread crash surfaces as "Dashboard not responding" tray badge; daemon keeps writing status.json + activity.log so MCP surfaces stay live. Invariant 2 tests pane isolation. |
| R4 | **`node_lookup_fn` wiring for G.1 follow-up #1** — producer-side sandbox layers 1+2 are currently no-ops because `NodeBidProducer(node_lookup_fn=None)` default was accepted to keep G.1 shippable. Phase H wires it. If wired wrong (fn returns wrong node, or raises), producer silently drops ALL bids | Medium — paid market appears dead under flag-on; user-perceived "submitted bid but daemon never picks" | Yes | Narrow contract: `node_lookup_fn: Callable[[str], NodeDefinition | None]` — returns None on missing (don't raise). Producer catches exceptions: on any raise, log WARN + skip the bid (fail-CLOSED, reason=`"node_lookup_error"`). **Spec updated 2026-04-14 post-Phase-H reviewer audit:** shipped behavior is fail-closed. Reviewer + lead concur: under disk-walk lookup, transient failures are rare, and dropping the bid until recovery is preferable to flooding the executor with unvalidated bids during a transient outage. Test (updated, invariant 5): seed bids; `node_lookup_fn` raises for one; assert producer SKIPS the raiser with `reason="node_lookup_error"` and emits the others. |
| R5 | **Multi-daemon git race stress (G.1 follow-up #3)** — claim atomicity is "partially resolved" via git-rename + push. Stress test needs TWO daemon processes on the same repo via real git worktrees racing real pushes. Previous coverage was single-process push-fail simulation only. If Phase H's stress test finds a real race the simulation missed, R5 regresses to full R13 unresolved | Medium-high (reveals a market-trust bug) — not a blast-on-land risk, but a confidence-in-landing risk | Yes (flag off on discovery) | Stress test in `tests/test_phase_h_claim_stress.py` using pytest `tmp_path_factory` + two subprocess daemons + same repo checkout via two worktrees. Race semantics tested: both start → both local-rename → both attempt push → one wins, other reverts. Subprocess harness pattern (won't be fast, ~10s per test). Accept slow — the correctness is worth it. Invariant 6. |
| R6 | **Activity.log byte-parity automation (Phase D follow-up #4)** — current gate is manual user-sim mission verification. Phase H automates it so flag-default flip doesn't regress under future refactor | Low (existing gate is manual) | Yes | Pytest fixture: seed a universe, run one cycle under `TINYASSETS_UNIFIED_EXECUTION=off` capturing activity.log, restart fresh, run the same cycle under `TINYASSETS_UNIFIED_EXECUTION=on`, diff the activity.log with timestamp/UUID normalization. Normalization regex list in fixture (timestamps `\d{4}-\d{2}-\d{2}T...`, UUIDs, branch_task_id hashes). Document the acceptable diff surface (heartbeats can differ in count; `dispatcher_observational:` lines only under flag-on). Test assertion is "no non-normalized differences." Invariant 7. |
| R7 | Tier-toggle persistence — a user toggles `accept_paid_bids` via dashboard; daemon restarts; toggle state resets to YAML config file value. User's toggle "didn't stick." | Medium — UX confusion ("I turned it off, why did it come back?") | Yes | `set_tier_config` MCP action WRITES the YAML, not just in-memory state. Toggle persists across restarts because it's the config file. Dashboard tier pane reads from `load_dispatcher_config` at refresh, shows the on-disk state. Invariant 4 tests persistence across synthetic "restart." |
| R8 | Concerns audit drift — §4.9 Q9 classifies all 19 STATUS Concerns. If the audit happens once in preflight then never revisited, Concerns accumulate during Phase H landing itself and become un-audited | Low (document hygiene) | Yes (re-audit cadence) | Preflight §4.9 Q9 table classifies each Concern at preflight-write time. Dev re-audits at land time (preflight explicitly instructs: "re-audit Concerns at land, remove any addressed, add any new from H landing itself"). Reviewer verifies the audit happened. Explicit reviewer-checklist item. |
| R9 | MCP client overload — adding `daemon_overview` + `set_tier_config` + dashboard WebSocket events creates 3 new MCP actions AND the aggregated view is 6+ disparate data sources bundled into one JSON. Claude.ai dialogs re-prompting tool approvals, UX thrash | Low (first-time approval tax) | Yes | Consolidate: `daemon_overview` is ONE new action, not six. `set_tier_config` is ONE new action, not four per-tier. Keep MCP surface minimal. "Always allow" toggle (already live, STATUS Concern "MCP always-allow toggle") makes first-time tax a one-time event. No new tools, just new actions on existing `universe` tool. §4.1 #1 + #2. |
| R10 | Flag-default-flip done wrong — if Phase H's flag-flip commit lands without user-sim mission green, a fresh-install daemon starts with paid market on + goal pool on + wrapper on and may encounter real-world regressions before the host notices | High (fresh-install default regression) | Yes (flag-off revert) | Flip is a SEPARATE commit, not part of Phase H landing. Phase H preflight + landing document the flip criteria (§4.5) but do not perform it. Host or lead flips defaults after Mission X passes. Audit of Mission X exit-criteria lives in the Concern that gates the flip. |
| R11 | `bid_ledger.py` rename to `bid_execution_log.py` breaks imports in places we don't expect | Low (find-and-replace) | Yes (git revert) | Rename lands in its own commit, not part of the dashboard work. Imports swept via `grep -rn "bid_ledger"`; tests reveal missed sites; reviewer spot-checks. Same discipline as prior rename work. §4.7. |
| R12 | Private-branch visibility surfacing in dashboard gives the illusion of solving task #8 when the design question is still open | Low (UX tidiness, not correctness) | Yes | Dashboard shows "private branches: N (see host settings for visibility policy)" WITHOUT resolving the Goal-vs-Branch inheritance question. The surface exists; the design decision still waits for host input. §4.9 Q8. |
| R14 | **`daemon_overview` response exceeds MCP token budget at high queue/bid counts.** Aggregation bundles queue + bids + settlements + gates + activity.log tail + run state; a daemon with 500 queue items + 200 bids + 50 settlements + 100 gate claims + 50-line activity tail can serialize to >40KB. MCP clients (Claude.ai) truncate responses that exceed their token budget — response arrives corrupted or with a `[truncated]` marker that loses structured data. | Medium — unreliable observability; user sees partial truth | Yes (response-size cap) | **`DAEMON_OVERVIEW_MAX_BYTES = 32_768` constant** in `tinyassets/universe_server.py`. Serialization is staged: build response dict, `json.dumps` it, check byte length, trim in priority order if over cap — **activity_tail first** (most verbose, least structured), then **settlements.recent** (keep counts), then **bids.top_open** (keep counts), then **queue.top** (keep counts). Gates + dispatcher + subscriptions never trimmed — they are load-bearing per reviewer. When any trim lands, the response gets a single top-level `truncated: true` marker (counters like `*_count` remain authoritative over the trimmed lists); client falls back to per-surface MCP actions if it needs untrimmed data. **Spec updated 2026-04-15 post-Phase-H landing:** shipped behavior uses one top-level `truncated` flag rather than per-field `_trimmed` + `_response_too_large` markers — simpler contract, same information (counters disambiguate which fields were trimmed). Test (new, invariant 10): seed daemon with 1000+ items across every surface; call `daemon_overview`; assert response ≤ cap; assert `truncated: true` when any trim occurred; assert counts correct despite trimmed arrays. |

### Reversibility summary

Everything in Phase H is additive or cosmetic. No new flag. No new execution path. No schema changes. The riskiest surface is R10 (flag-default-flip), and that's intentionally NOT part of Phase H's own landing — it's a follow-up commit gated on user-sim green. If Phase H dashboard extensions misbehave, `tray → Restart Dashboard` gets the user back to a working state; if `daemon_overview` MCP action is buggy, clients fall back to existing per-surface MCP actions (queue_list, etc. — all still live). No revert is load-bearing.

## 4. Implementation contract

### 4.1 Deliverables

1. **`tinyassets/universe_server.py` — `daemon_overview` action.** Aggregated read-through. Response shape:
   ```
   {
     "universe_id": <str>,
     "dispatcher": {
       "tier_status_map": { ... },  # from tier_status_map()
       "config": { accept_external_requests, accept_goal_pool, accept_paid_bids, allow_opportunistic, bid_coefficient, bid_term_cap, ... }
     },
     "queue": {
       "pending_count": <int>,
       "top": [ { branch_task_id, trigger_source, priority_weight, score, queued_at, ... }, ... ],  # top N by score
       "archived_recent_count": <int>
     },
     "subscriptions": {
       "goals": [...],
       "drift_flag": <str>,  # ok | pool_enabled_no_subs | subs_but_pool_disabled
       "pool_status_per_goal": { goal_id: count_pending, ... }
     },
     "bids": {
       "open_count": <int>, "claimed_count": <int>, "top_open": [...],
       "daemon_capabilities": { serves_llm_types, paid_market_enabled, bid_coefficient }
     },
     "settlements": { "count_total": <int>, "count_unsettled": <int>, "recent": [...] },
     "gates": { "ladder_count_on_bound_goal": <int>, "claims_on_this_universe": <int>, "recent_claims": [...] },
     "activity_tail": [ <last N activity.log lines> ],
     "run_state": { "current_phase": <str>, "status": "running|paused|idle|...", "last_verdict": <str>, "total_words": <int>, "total_chapters": <int> }
   }
   ```
   - Optional `limit` parameter (default N=20 for top queue / bids, N=10 for settlements / claims, N=30 for activity tail). `limit=full` returns all.
   - 1s mtime-cache on underlying reads (R1).
   - Gracefully handles absent features: flag-off tiers show config but no top-N.

2. **`set_tier_config(tier: str, enabled: bool)` MCP action on `universe` tool.** Writes `<universe>/dispatcher_config.yaml` with the toggle applied.
   - `tier` valid values: `external_requests`, `goal_pool`, `paid_bids`, `opportunistic` — maps to `accept_*` / `allow_*` fields.
   - Unknown tier → `{"status": "rejected", "error": "unknown_tier", "available_tiers": [...]}`.
   - Writes YAML (via existing config-writer pattern). Auto-commits (Phase 7 commit-per-mutation).
   - Authorization: host or universe owner (same pattern as `queue_cancel`).
   - Returns `{"status": "ok", "tier": <tier>, "enabled": <bool>, "takes_effect": "next_dispatcher_cycle"}`.
   - R2 invariant: does not abort in-flight tasks.

3. **Dashboard pane extensions (`tinyassets/desktop/dashboard.py`).**
   - `DispatcherPane` — shows tier_status_map as row list (tier / status / toggle button). Toggle calls `set_tier_config` via subprocess-MCP-client OR via direct function import (see §4.9 Q1).
   - `QueuePane` — top-10 pending BranchTasks; tier color-coded; click-to-expand shows full BranchTask details.
   - `EarningsPane` — visible only when `TINYASSETS_PAID_MARKET=on`; shows settlements count, open bids count, recent completion rate. Hidden pane when flag off.
   - `SettlementsPane` (optional, or merge with EarningsPane): settlements table with `settled: false / true` flag, so user can see the audit trail.
   - All panes consume the same `daemon_overview` response via a 2s refresh timer.
   - Every pane's event handler wrapped in try/except (R3); logs + returns, never propagates up to the UI thread.

4. **Tray menu extensions (`tinyassets/desktop/tray.py`).**
   - "Show Dashboard" entry — opens per-universe dashboard window. Multiple universes → multiple dashboards. Matches PLAN.md "one tray, many dashboards."
   - "Toggle Tier" submenu — 4 entries (external / pool / paid / opportunistic); click calls `set_tier_config`. Visually shows ON/OFF state.
   - "Pause All Tiers" emergency switch — calls `set_tier_config` for all four in sequence. Visual "EMERGENCY OFF" badge on tray icon.
   - Existing Start/Pause/Resume/Restart/Exit entries unchanged.

5. **`node_lookup_fn` wiring (G.1 follow-up #1, R4).** In `fantasy_author/__main__.py:_run_graph` (or earlier at `DaemonController.start`), construct the lookup function and pass it to `NodeBidProducer`:
   - `def _make_node_lookup_fn() -> Callable[[str], NodeDefinition | None]`: reads from `<repo_root>/branches/*.yaml` + extracts node_defs, OR delegates to the existing SQLite registry in `tinyassets/author_server.py` (same disk-walk path the executor uses, per Phase G Concern "node lookup uses disk walk, not SQLite registry").
   - Attach to `NodeBidProducer(node_lookup_fn=_make_node_lookup_fn())` at registration time (when `TINYASSETS_PAID_MARKET=on`).
   - Exception isolation: lookup raises → WARN log + bid skipped (fail-CLOSED, reason=`"node_lookup_error"`, R4).
   - Test: seeded bid with unknown node_def_id → producer rejects pre-pick; seeded bid with known node → emits.

6. **Multi-daemon git race stress test (G.1 follow-up #3, R5).** `tests/test_phase_h_claim_stress.py`:
   - Pytest fixture sets up a `tmp_path` git repo with a bare origin + two worktree checkouts.
   - Spawns two pytest-xdist or subprocess daemon loops (minimal daemons, not full `DaemonController` — a test harness class that runs `claim_node_bid` in a tight loop).
   - Both target the same bid YAML; assert exactly one succeeds (returns NodeBid) and the other fails with `error="claim_race_lost"`.
   - **Six scenarios** (originally 5; stale-origin-ref added per reviewer): both-race, serial-retry, rebase-midway, push-failure-revert-verify, archive-race (claimed bid marked expired while second tries to claim), **stale-origin-ref** — daemon B's fetch-ref is stale when it does `git reset --hard origin/<branch>`; bid file reverts to `open` locally; B's subsequent execute path MUST NOT proceed (asserts a check on post-revert bid status before execution). Pins the exact race G.2 closed so it can't regress silently.
   - **Deterministic race triggers, NOT `time.sleep`.** `multiprocessing.Barrier(2)` synchronizes the two daemon processes at each race point — both reach local-rename → release barrier → both attempt push. Or subprocess-stdin/stdout lockstep if Barrier doesn't compose with the subprocess harness. `time.sleep`-based timing is explicitly forbidden — produces flaky CI. Fixture documents the barrier sync points with comments.
   - Expected runtime ~30s for the whole stress file — this is git-subprocess cost (init, commit, push, fetch across two worktrees), not wait cost. Marked `@pytest.mark.slow` so it's optional in quick-loop dev.
   - **`@pytest.mark.slow` declaration (reviewer point 1):** mark registered in `pyproject.toml` under `[tool.pytest.ini_options]` `markers = ["slow: stress/race tests; run on-merge in CI as separate step"]`. Without the declaration, pytest emits `PytestUnknownMarkWarning` + runs the test anyway; a CI misconfigured to `pytest -m 'not slow'` might then silently SKIP it. Declaration + CI discipline go together. CI runs TWO pytest steps: `pytest -m 'not slow'` (fast, blocks PR) and `pytest -m slow` (on merge, blocks deploy). Document in `docs/planning/test_discipline.md` (new file).

7. **Activity.log byte-parity automation (R6, Phase D follow-up #4).** `tests/test_phase_h_activity_log_parity.py`:
   - Pytest fixture seeds a universe, runs one cycle under flag-off, captures activity.log, restarts fresh, runs one cycle under flag-on, captures activity.log.
   - Normalization regex list: timestamps, UUIDs, branch_task_ids, hash-like IDs. Defined as a module-level constant `_ACTIVITY_LOG_NORMALIZATION_PATTERNS` so future additions are visible in diff.
   - **Acceptable-diff allow-list defined as a module-level constant** `_ACTIVITY_LOG_ACCEPTABLE_DIFF_PATTERNS` — each pattern paired with a comment explaining why it's acceptable. Initial entries:
     - `r"^dispatcher_observational:"` — Phase E observational-only log line; only fires under flag-on. Acceptable because Phase D preflight §4.10 documented this as a flag-on-only surface.
     - (Future entries require code review — see governance below.)
   - **Allow-list extension policy (reviewer point 6).** Expanding `_ACTIVITY_LOG_ACCEPTABLE_DIFF_PATTERNS` requires:
     (a) A new pattern + explanatory comment explaining WHY the divergence is acceptable (what feature produced it, why flag-on differs).
     (b) Code review by a reviewer who confirms the divergence is intended behavior, not a bug.
     (c) STATUS.md Concern entry if the divergence reflects an accepted regression.
     Constant's docstring says this explicitly: `"""Patterns documenting acceptable flag-off/flag-on activity.log divergences. DO NOT expand blindly — see allow-list extension policy in docs/planning/test_discipline.md."""`. Extension attempts without review are visible as unreviewed commits touching this constant; CI can optionally lint-warn on changes to it.
   - Assertion: diff of normalized logs contains zero lines not matching allow-list patterns. Regression on activity.log format (e.g. a new log line added without allow-list entry) fails the test — loud by design.
   - Replaces the manual Phase D follow-up #4 gate with an automated one. Flag-default-flip (§4.5) references this test passing as a precondition.

8. **`bid_ledger.py` → `bid_execution_log.py` rename (G.2 #4).** Separate commit. File rename + import sweep + test renames. No behavior change.

9. **Docstring reconcile for `list_subscriptions` (Phase F follow-up).** Fix `tinyassets/subscriptions.py:123` docstring — actual behavior is insertion-order-preserving dedupe, not sorted. Preflight calls for docstring fix; implementation unchanged.

10. **Concerns audit doc pass (R8, §4.9 Q9).** Preflight §4.9 Q9 classifies all 19 Concerns; dev re-audits at land. Classification categories: *dashboard-surfaced* / *daemon_overview-field* / *backlog-tracked* / *resolved-delete*. Every Concern touched during Phase H.

11. **Tests** — see §4.4.

12. **Documentation:**
    - `docs/planning/dashboard_conventions.md` — layout, pane contracts, refresh cadence, tier-toggle semantics.
    - `docs/planning/daemon_overview_response_shape.md` — the aggregated-view schema. Public-facing for MCP clients.
    - Update `bids/README.md` + `node_bid_conventions.md` with G.1 follow-up #2 note (claim_node_bid's `git reset --hard` implies worktree discipline).

### 4.2 Flags

No new flag in Phase H. Existing flags unchanged:
- `TINYASSETS_UNIFIED_EXECUTION` (Phase D) — defaults off, flip target for §4.5 rollout after user-sim Mission green.
- `TINYASSETS_DISPATCHER_ENABLED` (Phase E) — defaults on.
- `TINYASSETS_GOAL_POOL` (Phase F) — defaults off, flip target.
- `TINYASSETS_PAID_MARKET` (Phase G) — defaults off, flip target.

Phase H rollout plan (§4.5) is the sequencing doc for flipping all three defaults. Phase H LANDING does not include the flips; flips are separate commits.

### 4.3 Invariants

1. **`daemon_overview` is read-only.** No action in the response body mutates state. Cached reads are consistent within the 1s TTL. Test (new): call `daemon_overview` twice within 1s; assert cached response; call outside TTL; assert fresh read.

2. **Dashboard pane exception isolation (R3).** A raise in one pane's `handle_event` does not propagate to the UI thread. Test (new, parameterized over pane types): inject a malformed event for each pane; assert pane logs + returns; assert other panes unaffected; assert UI thread alive.

3. **Tier-toggle takes effect at cycle boundary, not mid-cycle (R2).** In-flight task completes; next cycle respects new config. Test (new): seed queue with pool task; start execution; flip `accept_goal_pool=false` mid-execution; assert in-flight completes; assert next cycle does NOT pick more pool tasks.

4. **Tier-toggle persistence (R7).** `set_tier_config` writes YAML; restart reads same state. Test (new): toggle via MCP; construct fresh `load_dispatcher_config`; assert state matches.

5. **`node_lookup_fn` fail-CLOSED on exception (R4, spec updated 2026-04-14).** Lookup raises → producer logs WARN + SKIPS the bid with `reason="node_lookup_error"`. Rationale: under disk-walk lookup, transient failures are rare; dropping bids until recovery is preferable to flooding the executor with unvalidated bids during a transient outage. Test (updated): patch `node_lookup_fn` to raise; assert producer returns `"node_lookup_error"` and the bid is skipped.

6. **Multi-daemon git race single-winner (R5 stress).** At most one daemon succeeds in claim push. Test (new, `@pytest.mark.slow`): two subprocess daemons race on same bid; exactly one returns NodeBid; other returns None with `claim_race_lost`.

7. **Activity.log normalized-diff within bounded allow-list (R6).** Flag-off vs flag-on logs diff only in patterns enumerated in the module-level constant `_ACTIVITY_LOG_ACCEPTABLE_DIFF_PATTERNS` (§4.1 #7). Each pattern carries a comment explaining why the divergence is acceptable. Extension requires code review + explanatory comment + STATUS.md entry if the divergence reflects an accepted regression. Test (new): run fixtures; assert normalized diff contains zero lines not matching allow-list patterns; new unaccounted log lines fail loudly.

8. **MCP surface consolidation.** All Phase H additions live on the `universe` tool. No new top-level MCP tools. Test (introspective): count MCP-registered tools; assert Phase H adds zero tools + exactly 2 actions to `universe`.

9. **Emergency pause all-tiers atomicity.** Tray "Pause All Tiers" toggles all four flags. Partial failure (e.g. 2 of 4 set) rolls back via best-effort retry. Test (new): mock one `set_tier_config` call to fail; assert the other three are not left in inconsistent state (either all 4 or revert the 3 that did succeed).

10. **`daemon_overview` response bounded (R14).** Response size ≤ `DAEMON_OVERVIEW_MAX_BYTES` (default 32,768). Over-size responses trim in priority order (activity_tail → settlements → bids → queue); gates + dispatcher + subscriptions never trimmed. When any trim lands, response carries a single top-level `truncated: true` marker (counters `*_count` remain authoritative). Test (new, §4.4): seed with 1000+ items across every surface; assert response ≤ cap + `truncated: true` when trim occurred + counts accurate.

### 4.4 Test strategy

New file `tests/test_phase_h_dashboard.py` for the MCP + aggregation work. Plus two dedicated files for the G.1 stress test + D activity.log parity:

- **`daemon_overview` action (12 tests):** response shape matches schema; top-N limit honored; 1s cache TTL; large queue handled (100+ items); flag-off gracefully shows config-only (no top-N); drift flag surfaces correctly per Phase F invariant; settlements count accurate; flag-off paid market hides EarningsPane fields; **response-size cap enforced at 32KB** (R14 + invariant 10); **trim priority order** (activity_tail first, then settlements, then bids, then queue); **gates + dispatcher + subscriptions never trimmed** regardless of total size; **single top-level `truncated: true` marker set when any trim lands** (counters remain accurate).
- **`set_tier_config` action (6 tests):** valid tier name updates YAML; invalid tier rejects; persistence across restart (invariant 4); takes-effect-next-cycle (invariant 3); authorization boundary; `takes_effect` response field.
- **Dashboard pane isolation (invariant 2, 5 tests):** each new pane type with malformed event injection; UI thread survives; other panes survive.
- **Node lookup wiring (invariant 5, 4 tests):** lookup fn returns None → producer skips bid pre-pick; fn raises → producer fails CLOSED (`reason="node_lookup_error"`, spec updated 2026-04-14); fn returns valid → producer emits; fn not configured → producer defaults to no-op behavior.
- **Claim stress (invariant 6, `tests/test_phase_h_claim_stress.py`, 6 `@pytest.mark.slow` tests):** both-race, serial-retry, rebase-midway, push-failure-revert, archive-race, **stale-origin-ref** (pins G.2 regression; deterministic via `multiprocessing.Barrier`, no `time.sleep`).
- **Activity.log parity (invariant 7, `tests/test_phase_h_activity_log_parity.py`, 3 tests):** normalized diff within allow-list; acceptable-diff-surface documented; regression on activity.log format visible as diff expansion.
- **Emergency pause (invariant 9, 3 tests):** all-4-tiers toggled; partial failure rolls back; tray event fires.
- **`bid_ledger.py` rename (R11, 2 tests):** all imports updated (introspective test); all test-module references updated.
- **Concerns audit doc (R8, 1 test):** preflight §4.9 Q9 table is preserved as reviewer checklist; test asserts the file exists + has expected structure (markdown table format).
- **MCP surface non-inflation (invariant 8, 2 tests):** count registered MCP tools before/after Phase H; assert no new tools; assert exactly 2 actions added to `universe`.

Aim: ~43 tests. Stress + parity tests explicitly slow-marked (declared in `pyproject.toml` per §4.1 #6); CI runs `pytest -m 'not slow'` for fast-loop + `pytest -m slow` on merge. See `docs/planning/test_discipline.md` for the full policy.

### 4.5 Rollback plan + flag-default-flip sequencing

**Landing state:** Phase H lands with no flag changes. Dashboard + MCP additions are additive. The landing commit is inert for anyone not running a daemon with a dashboard attached.

**Flag-default-flip as separate commits, gated on user-sim missions:**
1. **`TINYASSETS_UNIFIED_EXECUTION=on` (Phase D flip)** — gated on user-sim Mission 8 (Sporemarch queue drainage + dispatch routing) AND activity.log parity test (invariant 7) green in CI. The parity test automates what was previously manual (Phase D follow-up #4).
2. **`TINYASSETS_GOAL_POOL=on` (Phase F flip)** — gated on user-sim Mission 9 (end-to-end pool post-and-pick) passing.
3. **`TINYASSETS_PAID_MARKET=on` (Phase G flip)** — gated on claim-race stress test (invariant 6) passing + user-sim Mission 10 adversarial bid-market test.

Each flip is a ~3-line commit: the `os.environ.get("...", "on")` default changes in the flag-reader. Commit message: "Flip Phase X default to on after Mission Y green."

**If Phase H dashboard breaks in live:**
1. **Immediate:** tray → Close Dashboard → daemon keeps running, MCP surfaces keep working. User drops to `daemon_overview` in MCP client for visibility.
2. **Short-term:** revert Phase H dashboard extension commits. Existing `DashboardHandler` behavior unchanged — dashboard returns to Phase G state.
3. **Full rollback:** revert Phase H landing commit. MCP actions `daemon_overview` + `set_tier_config` go away; existing per-surface actions (queue_list, list_subscriptions, list_open_bids) remain.

**If a flag-flip destabilizes in live:**
1. **Immediate:** revert the flag-flip commit (3-line change). Restart the TinyAssets daemon. Flag reverts to off.
2. Re-run user-sim mission with the failing fix applied.

**Do NOT:**
- Delete `<universe>/dispatcher_config.yaml` on rollback — it contains user's tier-toggle state. Preserve across revert.
- Delete `bid_ledger.py`-renamed files (if rename landed) — revert the rename instead.

**Data considerations:**
- `dispatcher_config.yaml` is user-editable; Phase H extends its schema (adds `bid_term_cap` field visibility — Phase G already defined the field). Missing field = existing defaults per Phase E/F/G. No migration required.
- Activity.log format unchanged in Phase H beyond existing `dispatcher_observational:` tags.

### 4.6 Non-goals

- Web/remote dashboard. Local desktop only.
- Mobile dashboard.
- Natural-language chat-to-action parsing (memo §4.4 chatbot affordances). Phase I.
- Advanced analytics (earnings projections, per-LLM profit, historical trend graphs). Later phases.
- Full conversational steering (`subscribe to Goal X`, `demote all paid work`). Phase I.
- Settlement token migration or currency integration. Token launch is a separate phase.
- New execution paths, producers, executors. Phase H is additive observability.
- Schema changes to `WorkTarget`, `BranchDefinition`, `NodeDefinition`, `BranchTask`, `NodeBid`. Forbidden per D-G carry-forward.
- Private-branch visibility design resolution (task #8). Phase H gives a UI surface to see private branches; actual visibility-model choice is host direction.
- Multi-host cross-subscription federation.
- Sandbox isolation beyond producer-side `node_lookup_fn` wiring (G.1 #1). True process-level isolation for NodeBid executor stays future work.

### 4.7 Files touched

| File | Change | Size estimate |
|------|--------|---------------|
| `tinyassets/universe_server.py` | EDIT — add `daemon_overview` + `set_tier_config` actions to `universe` tool dispatcher | ~300 lines added |
| `tinyassets/desktop/dashboard.py` | EDIT — add DispatcherPane, QueuePane, EarningsPane, SettlementsPane; exception-wrap existing handle_event | ~250 lines added |
| `tinyassets/desktop/tray.py` | EDIT — add Show Dashboard, Toggle Tier submenu, Pause All Tiers; multi-universe support | ~150 lines added |
| `tinyassets/producers/node_bid.py` | EDIT — `node_lookup_fn` wired into startup path; fail-CLOSED on exception (spec updated 2026-04-14) | ~20 lines |
| `fantasy_author/__main__.py` | EDIT — pass `node_lookup_fn` into `NodeBidProducer`; wire up dashboard multi-pane refresh | ~40 lines |
| `tinyassets/bid_ledger.py` → `tinyassets/bid_execution_log.py` | RENAME (G.2 #4) + import sweep | small |
| `tinyassets/subscriptions.py:123` | EDIT — docstring reconcile (Phase F follow-up) | 2 lines |
| `tinyassets/config.py` or dispatcher_config loader | EDIT — `set_tier_config` calls into this; round-trip YAML preserving other fields | ~30 lines |
| `tests/test_phase_h_dashboard.py` | NEW — `daemon_overview`, `set_tier_config`, pane isolation, node_lookup, concerns audit, MCP surface tests | ~600 lines |
| `tests/test_phase_h_claim_stress.py` | NEW — multi-daemon race stress (6 scenarios, `@pytest.mark.slow`, `multiprocessing.Barrier`-based deterministic sync) | ~240 lines |
| `pyproject.toml` | EDIT — declare `slow` marker under `[tool.pytest.ini_options]` | ~3 lines |
| `tests/test_phase_h_activity_log_parity.py` | NEW — flag-off/flag-on activity.log byte-parity automation | ~150 lines |
| `docs/planning/dashboard_conventions.md` | NEW — pane contracts, refresh cadence, tier-toggle semantics, dashboard-to-daemon direct-import channel + ledger discipline (§4.12 Q1) | ~110 lines |
| `docs/planning/test_discipline.md` | NEW — `@pytest.mark.slow` policy, CI two-step discipline, activity.log allow-list extension policy | ~60 lines |
| `docs/planning/daemon_overview_response_shape.md` | NEW — aggregated-view schema for MCP clients | ~80 lines |
| `bids/README.md` + `docs/planning/node_bid_conventions.md` | EDIT — G.1 follow-up #2 worktree-discipline note | small |
| `docs/exec-plans/daemon_task_economy_rollout.md` | EDIT — mark Phase H done when landed; document flag-flip sequencing plan | ~10 lines |
| `STATUS.md` | EDIT — Concerns audit pass (all 19 classified/surfaced/resolved); delete Phase H row | extensive |

No schema changes. No new MCP tools. No new flags. Net ~1,800 lines, heavily weighted toward tests (~950) and documentation (~180).

### 4.8 Success criteria

- All tests in §4.4 green.
- Existing full suite green.
- `daemon_overview` MCP action returns aggregated response for live daemon; schema matches `docs/planning/daemon_overview_response_shape.md`.
- `set_tier_config` persists across simulated restart (invariant 4).
- Tier-toggle takes effect at cycle boundary (invariant 3).
- Dashboard pane crash doesn't take out UI thread (invariant 2).
- `node_lookup_fn` wired + fail-CLOSED (invariant 5, spec updated 2026-04-14). G.1 follow-up #1 resolved.
- Multi-daemon race stress passes (invariant 6). G.1 follow-up #3 resolved.
- Activity.log parity test passes (invariant 7). Phase D follow-up #4 resolved.
- Concerns audit doc pass landed (§4.9 Q9 table). R8 cadence established.
- Reviewer verifies the three §4.5 flag-flip gates (Missions 8, 9, 10) are documented + CI-automated for flip #1 (parity test).
- One user-sim mission runs end-to-end: user observes full daemon operation via dashboard during a cycle, toggles a tier, observes change on next cycle, pause-all-tiers emergency switch responds.

### 4.9 Decision log

**Q1. Dashboard form factor — tray + per-universe dashboards, or MCP inspect only + GUI later?**
A. Both. Tray + per-universe dashboards ALREADY EXIST (`tinyassets/desktop/tray.py`, `tinyassets/desktop/dashboard.py`); Phase H extends them with new panes (DispatcherPane, QueuePane, EarningsPane, SettlementsPane). MCP inspect surfaces land in parallel via `daemon_overview` + `set_tier_config`. Rationale: PLAN.md "one tray, many dashboards" is already scaffolded; separating them creates drift. The tray/dashboard remains optional (`no_tray=True` still works); MCP surfaces work regardless.

**Q2. MCP inspect consolidation — separate per-surface actions or one aggregated `daemon_overview`?**
A. `daemon_overview` aggregated + retain per-surface actions. Rationale: aggregated view is the UX payoff (one glance); individual actions remain for programmatic use and backward compatibility. No deprecation of per-surface actions. `daemon_overview` is ONE new action, not 6+ (R9 scope discipline).

**Q3. `node_lookup_fn` wiring — where does it live?**
A. `fantasy_author/__main__.py:DaemonController.start` or `_run_graph`. Constructs function from disk walk OR SQLite registry (same path as the executor's existing lookup). Passes into `NodeBidProducer(node_lookup_fn=...)` at registration. Fail-CLOSED on exception (R4 invariant 5, spec updated 2026-04-14 post-audit — reviewer + lead concur dropping bids during transient lookup outage is preferable to flooding executor with unvalidated bids). Rationale: daemon-lifecycle code already owns producer registration (Phase F + G). Injecting the lookup there keeps the wiring in one place. SQLite-vs-disk choice inherits Phase G Concern ("node lookup uses disk walk, not SQLite registry") — keep disk walk for v1, add SQLite awareness later if needed.

**Q4. Multi-daemon git race stress test — Phase H test suite or user-sim mission?**
A. Phase H test suite (`tests/test_phase_h_claim_stress.py`) with `@pytest.mark.slow`. NOT a user-sim mission. Rationale: claim race is a timing-sensitive correctness property, not a user-observable UX property. Test-suite stress is reproducible; user-sim is flaky for timing. Marking `@pytest.mark.slow` keeps it out of quick-loop dev cycles; CI runs it on-merge.

**Q5. `bid_ledger.py` → `bid_execution_log.py` rename — Phase H or separate commit?**
A. Separate commit within Phase H landing sequence. Pure rename + import sweep, zero behavior change. Rationale: rename is disambiguation work flagged by G.2; folding into the dashboard commit conflates cosmetic-with-behavioral. Separate commit = clean git blame trail.

**Q6. Activity.log byte-parity automation — Phase H or defer?**
A. Phase H. The manual gate at Phase D follow-up #4 blocks the `TINYASSETS_UNIFIED_EXECUTION` default flip; automating removes a human step from the flag-flip rollout. Test file: `tests/test_phase_h_activity_log_parity.py`. Rationale: flag-flip rollout (§4.5) is itself scope-adjacent to Phase H; finishing the gate's automation there keeps it synchronous.

**Q7. Flag defaults flip — Phase H responsibility or separate?**
A. Phase H preflight DOCUMENTS the sequencing (§4.5); flip COMMITS are separate, each gated on a specific user-sim mission. Rationale: mixing flag-flips with Phase H landing creates blast-radius the preflight's "no new flags" principle explicitly avoids. Each flip is ~3 lines; commits them one at a time with clear rollback per R10.

**Q8. Private-Branch visibility filter (task #8) — Phase H surface?**
A. Surface only; no design resolution. Dashboard shows "Private branches: N (visibility model: host default)" with a link to documentation. The actual design question (Option A schema migration / B Goal-gated inheritance / C other) stays blocked on host direction. Rationale: giving the host a visual location to see the issue helps them decide; resolving it without host input would re-introduce a PLAN.md tension.

**Q9. Concerns audit doc pass — which STATUS Concerns get surfaced, deferred, resolved?**

Classification table (preflight-time; dev re-audits at land):

| # | STATUS Concern (summary) | Phase H action |
|---|-----|---|
| 1 | Worktree `inspiring-newton` retire | backlog-tracked (not a Phase H surface) |
| 2 | `default-universe` daemon stuck pre-guardrail | **dashboard-surfaced** via QueuePane showing "idle_reason" |
| 3 | Sporemarch fix (b) multi-scene overshoot | backlog-tracked (live verification at Mission 8) |
| 4 | Memory scoping defense-in-depth | backlog-tracked (medium design task) |
| 5 | Packaging mirror sync (host) | backlog-tracked (host decision needed) |
| 6 | Phase 6.2 follow-ups (private-Branch filter) | §4.9 Q8 handled via surface-only |
| 7 | Phase D landed (PASS-WITH-NOTES) | **daemon_overview field** (tier_status map shows D flag state) |
| 8 | Phase E follow-ups (queue_cancel + producer-registry test) | E#1 queue_cancel in-flight interrupt: **defer-to-Phase-I**; E#2 producer-registry-boundary test: **done** (task #16 completed) |
| 9 | Commit-failure-after-YAML-write divergence | backlog-tracked (platform-wide invariant) |
| 10 | Phase D follow-ups (4 items) | D#1-3: **backlog-tracked doc-pass**; D#4 activity.log parity: **automated by this phase** (R6, invariant 7) |
| 11 | MCP always-allow toggle | backlog-tracked (ui-test skill update) |
| 12 | Phase F R13 pool double-execution | **daemon_overview field** (settlements count makes duplicates visible) |
| 13 | Phase F follow-ups (3 items) | F#1-3: **backlog-tracked** or done in Phase G context |
| 14 | `list_subscriptions` docstring | **resolved-delete** (Phase H cleanup sweep) |
| 15 | Private-Branch visibility gap | §4.9 Q8 surface-only |
| 16 | Phase G follow-ups (4 items) | G#1 node_lookup_fn: **resolved by this phase** (R4, invariant 5); G#3 multi-daemon race: **resolved by this phase** (R5, invariant 6); G#4 rename: **resolved by this phase** (R11, §4.7) |
| 17 | Phase G R5 partial resolution | G.2 upgraded to fully-resolved on remote-configured repos; **daemon_overview field** shows settlements for audit |
| 18 | Phase G flag-restart requirement | backlog-tracked (documented in release notes) |
| 19 | Phase G node lookup disk walk | backlog-tracked (SQLite awareness is future) |

Summary: 5 Concerns resolved in Phase H; 4 Concerns surfaced via dashboard/daemon_overview; 10 remain backlog-tracked with explicit tracking. Zero Concerns lost.

### 4.10 Pause/stop/checkpoint — inherits D §4.10 + F §4.11

No new pause/stop regression. Tier-toggle takes effect at cycle boundary (invariant 3), same cadence as Phase D/F flag reads. Dashboard refresh cadence (2s timer) doesn't affect daemon pause/stop. Emergency pause-all-tiers is additive — sets four config fields; dispatcher reads them next cycle.

Checkpoint: no new checkpoint state. `set_tier_config` writes YAML on disk; daemon restart reads from YAML; no SqliteSaver involvement. Dashboard state is transient (2s refresh); on restart, dashboard reloads from current disk state.

### 4.11 Economic-layer + security carry-forward from G

Phase G's three structural safeguards (flag kill switch, no-real-money, public audit) continue to apply. Phase H adds:
- **Dashboard makes the audit trail visible.** SettlementsPane shows `settled: false` count prominently so the host sees the pre-token ledger growing.
- **Emergency pause-all-tiers extends the kill switch.** Host can disable all tiers in one click without restarting the daemon. Faster response than `TINYASSETS_PAID_MARKET=off` + restart.
- **`node_lookup_fn` wiring completes the three-layer sandbox defense** that was partially stubbed in G.1. Defense-in-depth now complete at production level.

Reviewer audit items from Phase G §4.13 remain: all three safeguards should still be visible in the codebase post-Phase-H.

### 4.12 Open design questions (non-blocking)

1. **Dashboard MCP client channel — PINNED (reviewer point 4).** Dashboard panes call `set_tier_config` via **direct function import**, NOT subprocess MCP client. Rationale: dashboard is same-process; subprocess MCP would double the cost + add auth boilerplate for no isolation benefit. BUT — to preserve audit-trail consistency with external MCP callers, **every dashboard-initiated `set_tier_config` call writes a ledger entry at `<universe>/set_tier_config.log`** with the same shape as an MCP-call ledger entry: `{timestamp, actor="dashboard", tier, old_value, new_value, daemon_id}`. External callers via MCP get the identical ledger entry (written by `_action_set_tier_config`, not by the dashboard). Both channels land in the same file; both carry `actor` so attribution is preserved. Test: toggle via direct import, assert ledger entry; toggle via MCP, assert ledger entry matches shape. Document in `dashboard_conventions.md` + `daemon_overview_response_shape.md`. Without the ledger, audit trails diverge between dashboard and MCP — reviewer's exact concern.

2. **`daemon_overview` cache TTL.** Default 1s. Hosts running many dashboards may want shorter/longer; configurable in `dispatcher_config.yaml`? Recommend: config field `daemon_overview_cache_ttl_seconds: float = 1.0`. Doesn't block; default works.

3. **Tray multi-universe UX.** When a host runs N daemons for N universes, tray shows N tray icons OR one tray icon with N submenus? PLAN.md implies N tray icons ("one tray, many dashboards" — singular tray, plural dashboards). Recommend: one tray icon per daemon process (matches existing pattern). Multi-universe per daemon (memo Q3 future) would unify later.

4. **`emergency_pause_all_tiers` persistence semantics.** If set, should it persist across restart or be a one-shot? Recommend: persistent (writes to YAML). Host explicitly turns tiers back on when ready. Aligns with "explicit control" principle.

5. **Concerns audit re-cadence.** §4.9 Q9 happens once at preflight + once at land. Should it be a recurring lead discipline? Recommend: every N phases (after Phase I, J, ...). Lead's call.

None blocks Phase H dev.

## 5. Handoff

Dev can claim Phase H immediately — no upstream dependencies blocking. Existing dashboard + tray code is already in place; Phase H extends it. All Phase G surfaces are live and consumable.

Reviewer focus areas:
- **R1 performance characteristic** — `daemon_overview` with 100+ queue items + 50+ bids should respond in <100ms on typical hardware.
- **R3 pane isolation** — extend the exception-wrap pattern to the existing `DashboardHandler.handle_event` in the same commit, not a separate one. Reviewer verifies.
- **R4 `node_lookup_fn` fail-CLOSED** (spec updated 2026-04-14): if lookup raises, producer SKIPS the bid (`reason="node_lookup_error"`) and WARN-logs. Reviewer tests with a raising-lookup mock that the bid is excluded, not emitted.
- **R5 claim stress test** — genuinely two processes, not two threads. `pytest-xdist` or subprocess discipline; no mock fakes.
- **R6 activity.log parity** — diff-allow-list is documented in the test fixture so regressions are visible.
- **§4.9 Q9 Concerns audit** — dev completes at land time; reviewer verifies all 19 touched.

If any of these slip, flag back to planner. Phase H is the UX payoff phase — getting the observability surfaces right matters as much as the architectural invariants.
