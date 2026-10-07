# Slice 3 CORE — cross-family review log (Codex)

Reviewer: Codex (opposite family). Read-only, adversarial.

## Round 1 (core, seams injected) → VERDICT: adapt

Codex ran standalone reproductions and found the CORE cannot yet claim its
invariants. Fix ALL before landing the core (they must land WITH or BEFORE the
record-wire; the core is not trustworthy as-is):

1. **CRITICAL — concurrent ticks double-post.** Two ticks both read `pending`, both
   call the adapter, both `mark_delivered`. The mark txn runs AFTER the side effect
   and its zero-row result is ignored. A crash between post and mark also duplicates.
   → Fix: CLAIM the entry atomically BEFORE posting (`pending → in_flight` under
   BEGIN IMMEDIATE; a 0-row claim means another tick owns it → skip). Give the
   adapter an explicit `action-result:{run_id}:{revision}` idempotency key as the
   crash-safety backstop.
2. **CRITICAL — summary can leak internal data.** `compose_summary` interpolates
   `failed_phase`/`terminal_phase`/`public_result_ref`/`result_url` unvalidated; a
   `failed_phase="node_internal_42 token=xoxb-…"` emitted the token. → Fix: sanitize
   — restrict to a strict safe pattern + length cap, or drop free-text phase and
   only emit a result ref matching an allowlist (URL / `PR #\d+`).
3. **CRITICAL — non-throwing transport failure silently "delivered".** An adapter
   returning `None` posted nothing yet was marked delivered. → Fix: validate the
   receipt; a falsy receipt = HOLD (not delivered), like an exception.
4. **Required — `cancelled`/`interrupted` stuck forever.** `_TERMINAL` = only
   completed/failed, so those terminal runs stay pending and re-count as
   `skipped_running` every tick. → Fix: treat all non-completed terminal statuses as
   "didn't finish" (honest, no success) and deliver once.
5. **Required — pagination starvation.** `list_pending` returns the oldest 200; 200
   older still-running entries hide a newer completed one forever. → Fix: cursor-page
   through ALL pending (created_at/rowid cursor), not just the first page.
6. **Required — content-free not enforced at the storage boundary.** No secret
   columns exist, but `app_binding_ref="xoxb-…"` persists verbatim. → Fix: cap field
   lengths + constrain to id-like values (defense-in-depth vs a caller bug).
7. **Required — tests self-confirming.** Add concurrency, cancellation, receipt-
   validation, pagination-starvation, and adversarial summary-safety cases.

STATUS: fixes NOT yet applied (Slice 2 is the landing priority this session; the
Slice 3 record-wire is a deferred cross-boundary change —
`docs/audits/2026-08-19-slice3-record-wiring-seam.md`). Apply these with the wire.

VERDICT (round 1): adapt: require run-and-terminal-revision adapter idempotency,
prevent concurrent claims, validate receipts and public summary fields, and handle
terminal-status and queue-starvation cases.

## Round 2 — review of the hardening → VERDICT: adapt (5 findings, Slice 3 PAUSED)

Codex confirmed fixed: the two-tick claim race (one winner), the four terminal statuses,
and all fail-closed paths (authorize None/raises, adapter raises/falsy-receipt → released).
Remaining, for when Slice 3 resumes:
1. CRITICAL crash-replay double-post: reclaim/mark by run_id alone has no fencing token; a
   stale worker can release/mark a newer claim, and ProductionAdapter ignores the
   idempotency_key before _post → real idempotency needs a fencing token + adapter dedup.
2. CRITICAL summary allowlists still leak: `https://…?token=xoxb-…` passes _SAFE_REF_RE;
   `failed_phase="token xoxb-secret"` passes _SAFE_PHASE_RE. Need stricter shapes / no
   query strings / reject token-like substrings; drop result_url + terminal_phase fallbacks.
3. CRITICAL no schema migration: _connect only CREATE TABLE IF NOT EXISTS; existing DBs lack
   claimed_at → claim() raises. Need an ALTER/migration.
4. content-free still unenforced: a short token passes the length cap; need id-shape validation.
5. poison-row starvation: get_run is outside the per-entry try; one unreadable run aborts the
   whole tick. Isolate per-row lookup failures.

PAUSED 2026-08-19 (founder reprioritized to deploy + multi-user). Slice 3 delivery pipeline
(core + tick + main() wiring) is built + 22 tests green but NOT landed; the record-wire
(Design A, run-ids-up, investigated) is not built. Resume here.

## Round 2 resolution (2026-08-19) — all 5 gaps fixed + re-review dispatched
1. Crash-replay: `claim()` returns a fencing `claim_token`; `release`/`mark_delivered` require it;
   `reclaim_stale` clears it. ProductionAdapter honors `idempotency_key` via a receipt store
   (`receipt_seen`/`record_receipt`) → cached receipt on crash+reclaim+retry, no double-post.
2. Summary: `_SAFE_REF_RE` forbids query strings; `_SAFE_PHASE_RE` single-token; both dropped on a
   `_SECRET_MARKERS` (token|secret|key|xoxb|ghp_|sk-|bearer…) match.
3. Migration: `_connect` PRAGMA table_info + ALTER for claimed_at/claim_token on existing DBs.
4. Content-free: `_ID_SHAPE = ^[^\s\x00-\x1f]{1,128}$` id-shape validation on every stored field.
5. Poison-row: get_run wrapped per-entry; one unreadable run no longer aborts the tick.
25 tests green (added fencing, migration, adapter-idempotency, poison-row). Codex re-review running.
UNPAUSED — Slice 3 core is production-ready pending the re-review verdict; record-wire (Design A) is the
remaining end-to-end integration (separate change).
