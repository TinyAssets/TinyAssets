# Tasks — Stream and classify provider attempts (Slice 1)

## 1. Types + config (base)
- [x] 1.1 `providers/base.py`: add a timeout PROFILE to ModelConfig (idle_timeout_s, first_progress_s, init_timeout_s, soft_slo_s, absolute_cap_s) with the design defaults + backward-compat from legacy `timeout` (fields default None → `stream_timeout_profile()` fills the design defaults). Add normalized `StreamEventKind`/`LIVENESS_EVENT_KINDS` + `FailureClass` enum + `StreamTimeoutProfile`.
- [x] 1.2 Keep `ProviderResponse` as the terminal outcome; add optional attempt-telemetry fields (failure_class, ttft_ms, last_progress_age_ms, tool_phase, exit_code, side_effect_state).

## 2. Streaming reader (claude provider)
- [x] 2.1 `claude_provider.py::complete`: launch `-p --output-format stream-json --verbose --include-partial-messages`; read stdout NDJSON line-by-line (`_read_stream`), drain stderr concurrently; assemble final text from assistant text deltas; terminal `result` = canonical response. No `communicate()`.
- [x] 2.2 Idle watchdog: reset deadline only on real protocol events; fire `provider_idle_timeout` on idle; `interactive_deadline` on the absolute cap. Kill the process on either. Keep exit-1-<5s / crash-code / bwrap handling.
- [x] 2.3 Classify `provider_rate_limited`/`provider_overloaded`/`authority_held`/`provider_protocol_error` from `system/api_retry` + exit.
- [x] 2.4 Do NOT relay internal reasoning/thinking to the assembled reply.

## 3. Router health/cooldown (quota + router)
- [x] 3.1 `router.py` + `quota.py`: cooldown map by failure_class — idle/deadline → NO provider-wide cooldown; rate_limited/overloaded → cooldown until retry_after (`_rate_limit_cooldown_s`); authority_held → re-raised (quarantine). (Circuit-breaker for repeated launch failure is a later hardening — see design.md; single idle timeout is the scope here.)
- [x] 3.2 Remove synchronous interactive sleeps: `universe_intelligence._call_writer` (was 30/60s backoff) + `call.py` gains `retry_on_exhaustion=False` for the interactive path. Sole-writer: one immediate fresh-process retry only if all providers were skipped (nothing ran / no side-effect); else end honestly.

## 4. Honest notice
- [x] 4.1 `app_ingress.py::_failure_notice`: map `failure_class` → truthful text (timeout ≠ capacity). An UNCLASSIFIED (`failure_class is None`) exhaustion renders an honest GENERIC error — it must NOT substring-guess "capacity"/"rate limit" (the exact mislabel this change removes). Real rate-limit/overload now arrives WITH its class via the router aggregate.

## 5. Tests
- [x] 5.1 Stream-fixture behavior parity: recorded stream-json → same final text (terminal result canonical + assistant-delta fallback).
- [x] 5.2 Idle watchdog: streams with gaps → alive while progressing; idle → `provider_idle_timeout`; long-but-progressing → not failed; absolute cap → `interactive_deadline`.
- [x] 5.3 Failure taxonomy from api_retry/exit/malformed fixtures.
- [x] 5.4 Router: idle/deadline → no cooldown (next turn eligible); rate_limited/overloaded → cooldown w/ retry_after.
- [x] 5.5 No-sleep assertion on the interactive path (`_call_writer` + `call_provider(retry_on_exhaustion=False)`).
- [x] 5.6 `_failure_notice` mapping (timeout ≠ capacity; retry_after surfaced).
- [x] 5.7 Backward-safe: a non-streaming/other provider (codex, claude `complete_json`) still returns a terminal ProviderResponse.

## 6. Review + land
- [x] 6.1 Codex cross-family review of the implementation; resolve approve/adapt. -- done: two Codex adversarial reviews on #2440 (first REJECT, all fixed; re-review in `REVIEW.md`)
- [x] 6.2 ruff, targeted pytest green (Linux CI authoritative), plugin mirror parity. -- done: #2440 landed green 2026-08-19 (Linux CI)
- [x] 6.3 Land to main (own PR); sync delta + archive. -- landed as #2440 (2026-08-19); delta synced into `openspec/specs/provider-routing/spec.md` and archived 2026-08-29 together with slice 2 (#2674)
- [x] 6.4 Durable deploy to prod (founder decision) with `TINYASSETS_ALLOW_CLAUDE_SERVING=1`; verify the live capacity-mislabel is gone. -- not a build task: filed as a host-decision row in `docs/host-actions.md` 2026-08-29 ("Decide whether Claude may serve turns in production")

## 7. Slice 2 — codex parity (founder 2026-08-29: "a turn should continue till finished unless interrupted by the user or should stop for some other reason")
- [x] 7.1 `codex_provider.py`: read `codex exec --json` line-by-line under the idle-watchdog profile (`_stream_codex_exec`); no `communicate()`; raise `provider_idle_timeout` / `interactive_deadline`, never the generic timeout the router cools on. A turn waiting on its OWN tool (`item.started` without `item.completed`) is not idle.
- [x] 7.2 `universe_intelligence._sandboxed_config`: the served absolute cap is a generous runaway backstop (3600s), not a deadline; per-universe `absolute_cap_s` / `idle_timeout_s` overrides; nonsense falls back rather than disabling the cap.
- [x] 7.3 Tests: tool-wait longer than idle survives; idle fires with nothing running; idle resumes after the tool; cap while progressing → `interactive_deadline`; EOF returns bytes unchanged; served cap + overrides.
- [x] 7.4 Codex cross-family review (refute, not approve); land; sync delta + archive together with slice 1.
- [x] 7.5 Follow-ups filed, not widened here: Claude's tool-wait gap was later resolved by PR3905 (see `2026-09-21-provider-tool-wait-deployment.md (archived in git)`); a user **Stop** for a running turn remains tracked in `docs/concerns/2026-08-29-no-user-stop-for-a-running-turn.md`, cap-as-user-budget in `ideas/INBOX.md`.
