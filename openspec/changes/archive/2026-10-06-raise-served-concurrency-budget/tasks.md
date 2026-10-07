# Tasks — raise-served-concurrency-budget

## 1. Budget ceiling, per-call decoupling, stale-binding heal
- [x] 1.0 CORE: decouple per-call output from the aggregate ceiling in
      `tinyassets/providers/router.py` — when `cfg.max_tokens is None` reserve a
      bounded `_SERVED_PER_CALL_MAX_TOKENS` (65_536, capped to the ceiling), not
      the whole binding ceiling (Codex critical: the real prod-path brick).
- [x] 1.1 Raise `_MAX_TOKENS` (32_768 → 4M) and `_MAX_COST_MICROUNITS` (10M →
      400M) in `provider_serving_binding.py`; mirror into the packaging runtime.
- [x] 1.2 Heal stale bindings via re-bind (NOT an admission floor): make the
      same-provider replay in `bind_serving_provider` conditional on current
      policy — a binding whose signed ceiling is below `_MAX_TOKENS` falls through
      to the transactional rebind (advances generation/digest, persists the
      current ceiling). Keeps the ceiling digest-covered (Codex 2026-08-22).

## 2. Regression proof
- [x] 2.0 Router `max_tokens=None` reserves the bounded per-call amount, not the
      ceiling (`test_served_none_max_tokens_reserves_bounded_per_call_not_whole_ceiling`).
- [x] 2.1 Many concurrent in-flight reservations for one binding all admit.
- [x] 2.2 Oracle: a 32_768 ceiling bricks the 2nd turn AND a reservation never
      exceeds the binding's own stored ceiling (authority contract, no floor).
- [x] 2.3 Heal: a stale-low binding re-bind advances generation + persists the
      current ceiling without replay (`test_stale_binding_rebind_advances_generation_and_reflows_ceiling`).
- [x] 2.4 Linux/CI two-thread test (Windows-skipped) + full suite green, ruff clean.

## 3. Review + rollout
- [ ] 3.1 Opposite-provider (Codex) exact-head review returns `approve` (any
      `adapt` changes HEAD and requires a fresh review). Core approach approved
      2026-08-22 (digest-covered authority preserved, transactional generation
      advance); test-independence + doc polish applied post-approval.
- [ ] 3.2 Merge + deploy; confirm prod `release_state.git_sha` contains it.
- [ ] 3.3 Re-bind the founder binding (now actually lifts the ceiling) + live
      proof: concurrent turns across ≥2 surfaces with no "budget exhausted".

## Follow-ups (post-live hardening — prose, not tracked tasks)

Out of scope here: (a) divide the byte-based `estimated_input_tokens` proxy by a
bytes/token factor so reservations are not ~4× inflated; (b) if per-user host
capacity ever needs a hard bound, add explicit bounded host-capacity admission
with load proof rather than relying on the aggregate ceiling.
