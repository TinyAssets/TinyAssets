# Tasks — Deliver async action results (Slice 3)

## 1. Outbox store
- [x] 1.1 `tinyassets/storage/action_result_outbox.py`: `action_result_outbox` table (content-free, keyed by run_id) + `record()` (INSERT OR IGNORE), `list_pending()`, `mark_delivered(run_id, revision)`, `mark_failed_final(run_id)` — atomic under BEGIN IMMEDIATE. NO credential/body stored.
- [x] 1.2 Tests: record→list_pending; dedup on run_id; state transitions.

## 2. Delivery
- [x] 2.1 `tinyassets/action_result_delivery.py::deliver_pending_action_results(base, *, get_run, authorize, adapter)`: for each pending entry — get_run; skip if non-terminal; compose truthful success/failure summary (no internal leak, never success-on-failed); re-resolve authority fresh; deliver via injected adapter; mark delivered. Fail-closed on unauthorized/failed delivery (hold, log, never post/drop).
- [x] 2.2 Tests (inject adapter + get_run seams): skip-running; deliver-completed-once; deliver-failed-honest; idempotency (state guard); fail-closed on unauthorized; fail-closed on transport failure; content-safety (no credential/body columns, no leak).

## 3. Wiring — BLOCKED by a real cross-boundary seam (mapped 2026-08-19)
The record-side wiring cannot land as originally drafted. Enumerated map:
`docs/audits/2026-08-19-slice3-record-wiring-seam.md` (or the Explore report inline
in this change's design.md).

- **The seam:** `run_id` and the Slack conversation origin are NEVER co-present.
  - At `app_ingress.deliver_app_event` the Slack origin (workspace/channel/thread/
    event_id/app_binding + `routed.universe_id`) is in scope, but the `run_id` is
    not — it is minted deep inside the universe's LLM turn (`runs.py:787`, via the
    model calling the `run_graph` engine tool) and `converse` returns only `reply:
    str`.
  - At the enqueue site (`api/runs.py::_action_run_branch` → `execute_branch_async`
    → `_execute_branch_core`) the `run_id` + `universe_id` exist, but NO Slack
    field does — the call crossed `converse` (no origin params) and a fresh engine-
    MCP-identity tool turn. So original task 3.1's premise ("origin present at
    `_action_run_branch`") is FALSE.
- [ ] 3.1 (REVISED — needs its own change + Codex review) Bring enqueued `run_id`s
  UP to `deliver_app_event`, not origin DOWN to the engine. Recommended: `converse`
  captures the `run_graph` engine-tool results (each carries a `run_id`) produced
  during the writer turn and returns the enqueued run_ids alongside `reply`;
  `deliver_app_event` (which already has the Slack origin) then calls
  `outbox.record(run_id, origin…)` for each. This avoids threading origin through
  the security-sensitive engine-MCP identity boundary. It touches the writer-turn
  stream capture, so it is its own OpenSpec change, not a minimal wire.
- [ ] 3.2 Call `deliver_pending_action_results` from a daemon-thread cadence tick in
  `universe_server.main()` (sibling to `_served_budget_lease_loop`), with real
  seams: `get_run = runs.get_run` (adapted to surface a deliberately-populated
  public result ref from `run["output"]` and a revision — NOT auto-dumping output,
  to preserve content-safety), `authorize` = fresh app-authority re-resolution for
  the outbox entry's conversation, `adapter` = the governed Slack post (the
  `deliver_app_notice` routing path). Inert until 3.1 records entries.
- [x] 3.3 Plugin mirror rebuild + parity (core modules mirrored).

## 4. Review + land
- [x] 4.1 ruff; targeted pytest green (core: 7 delivery + outbox tests; Linux CI authoritative).
- [ ] 4.2 Codex cross-family review of the CORE (idempotency + fail-closed + content-safety) — core is landable independently of the blocked wiring.
- [ ] 4.3 Land the CORE to main (own PR); sync delta + archive. Wiring (3.1/3.2) tracked as a follow-on change once scoped.
