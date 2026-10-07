# Raise served-provider in-flight budget for real concurrency

## Why

A user must be able to drive their universe from **many surfaces at once**
(connector, web app, desktop app, phone) **while concurrent LangGraph
automations run**, and **many users** must do the same simultaneously. Live
testing on 2026-08-23 showed the opposite: a burst of turns across surfaces
returned `Provider authority budget is exhausted; reconnect or rebind your
provider` after only ~2 concurrent turns.

Root cause: the serving binding's in-flight ceiling `_MAX_TOKENS = 32_768`
(`tinyassets/provider_serving_binding.py`). A served turn reserves
`len(system+prompt bytes)` of budget (`providers/router.py` — a rebuilt
persona/brain system prompt is ~15–30 KB) plus its output, so the SECOND
simultaneous turn across any surface gets `output_tokens < 1` and is held. This
ceiling bounds only UNSETTLED (concurrent) reservations — a settled turn
RELEASES — so it is a **concurrency runaway guard, not a cumulative spend
limit** (the user's own deposited subscription meters real spend upstream). It
was sized like a spend cap, which is the wrong shape for the required
concurrency.

## What Changes

- **Decouple the per-call output reservation from the aggregate in-flight
  ceiling (the core fix).** The production converse path (`_sandboxed_config`)
  leaves `max_tokens=None`; the router substituted the *entire* binding ceiling
  and then reserved that whole amount per turn, so the first turn reserved the
  full budget and the second concurrent turn bricked no matter how high the
  ceiling was raised (Codex 2026-08-22). The router now reserves a bounded
  per-call default (`_SERVED_PER_CALL_MAX_TOKENS = 65_536`, capped to the
  ceiling) when no explicit `max_tokens` is set.
- Raise `_MAX_TOKENS` 32_768 → 4_000_000 and `_MAX_COST_MICROUNITS`
  10_000_000 → 400_000_000 on the serving binding (kept consistent so the
  token cap, not the cost cap, is the effective ceiling). Sizing rationale:
  budget is reserved in `router.call` **before** the process-wide provider
  worker pool (8 workers), so in-flight reservations equal the number of
  concurrent *requests* (surfaces + automations), not the number of executing
  turns. 4M / (~65 KB per-call output + ~15–30 KB input) ≈ 40+ concurrent
  requests can hold reservations while the 8-worker pool serializes their actual
  provider execution. The ceiling is a runaway backstop above that, not a
  concurrency or spend control (the worker pool bounds execution; the user's own
  subscription meters spend).
- The true runaway backstops are unchanged: the rolling per-hour invocation cap
  (`_MAX_BINDING_INVOCATIONS = 10_000`), the engine-run rate limit (20/hr), and
  the user's metered subscription. The two independent guards in
  `reserve_served_provider_budget` (invocation-runaway + in-flight token) are
  untouched — only the token ceiling magnitude changes.
- Per-binding isolation is preserved: each universe's binding has its own
  ceiling, so many users concurrently do not contend (cross-user isolation is
  inherent, not an allowlist).

## Impact

- Affected specs: `provider-routing` (served-provider budget admission).
- Affected code: `tinyassets/providers/router.py`,
  `tinyassets/provider_serving_binding.py`, `tinyassets/provider_assignment.py`
  + packaging mirrors.
- New bindings pick up the ceiling at creation. A pre-existing binding is healed
  by a re-bind: `bind_serving_provider` no longer *replays* a same-provider
  binding whose signed ceilings DIFFER from current policy (either direction) —
  it advances the
  generation/digest and persists the current ceiling (the error's own "reconnect
  or rebind" recovery, now real instead of a no-op). The ceiling stays
  digest-covered; there is deliberately NO admission-time floor (that would
  bypass the signed authority — Codex 2026-08-22).
- No change to spend enforcement (there is none here by design — spend is on the
  user's own subscription); this is a runaway-guard magnitude correction.
