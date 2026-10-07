# Floor 1 (inbound webhook) — cross-family review log (Codex)

## Round 1 → VERDICT: adapt (before flipping TINYASSETS_INBOUND_ENABLED)

Confirmed sound: a request cannot redirect a stored binding (actor/provider derive from the
stored `universe_id`); unknown vs revoked tokens are indistinct with no timing oracle on a
256-bit token; the store persists no bodies/headers. Findings to fix before public exposure:

1. **Cross-universe ownership + author gate (CRITICAL).** mint/revoke/list must enforce
   `universe_access_allows(uid, write=True)`; mint must require the authenticated subject to be
   the branch AUTHOR (the claimed author gate does NOT exist on the direct enqueue — direct
   `execute_branch_async` bypasses universe-scope dispatch + ledgering that `_action_run_branch`
   does). Revalidate the binding before every enqueue.
2. **Body cap after unbounded buffering (CRITICAL).** The route `await request.body()` buffers
   the whole body before the 256 KB check → an anon attacker forces full buffering with junk
   tokens. Reject oversized `Content-Length` + stream/count chunks, aborting at 256 KB.
3. **Admission is not durable/aggregate (HIGH).** Invalid-token requests are unlimited (each
   opens SQLite + reruns schema DDL before lookup); valid-token limit is process-local, resets
   on restart, multiplies per worker, keyed only by token; minting has no quota (many tokens
   bypass). Each admitted request writes a durable run row + submits to an unbounded pool. Need
   pre-lookup origin/IP limiting + durable per-universe/token admission + bounded queue depth +
   replay/idempotency.
4. **Channel-signature verification impossible (HIGH).** `_decode_body` parses/UTF-8-replaces, so
   the branch never gets the exact signed bytes → forwarding `X-Hub-Signature` is useless; the
   URL becomes the sole auth. Preserve bounded RAW bytes (or exact base64) alongside the parsed
   convenience value.
5. **Revocation unreachable (HIGH).** The callable `extensions` schema has no `token` param and
   the dispatcher only routes `run_branch` (run_graph → run_branch), so mint/revoke/list — though
   registered in `_RUN_ACTIONS` — are NOT reachable through the MCP/chatbot surface. Wire them
   into the actual dispatch + schema so an owner can mint/revoke after a leak.

Also: tokens are stored plaintext (a read-only DB disclosure grants invocation authority —
consider hashing at rest); all non-Authorization/Cookie headers persist into tenant run input
(a proxy-injected Access/OIDC assertion would too — tighten the forward set).

Floor 1 stays DARK until `TINYASSETS_INBOUND_ENABLED` is flipped; the receiver mounts at
`/mcp/hooks/<token>` (under `/mcp`, reached by the EXISTING `/mcp/*` tunnel — no tunnel/infra
change), so these gate the flag-flip step, not the code landing. Status: store + receiver + route + ops built, 20 tests (mock ownership/enqueue
+ streaming — Codex notes those boundaries are untested). Fixes owed before go-live.

## Round 2 → VERDICT: adapt (6 findings + tests were self-confirming). NOW RESOLVED.

Codex round-2 confirmed the actor derivation, the owner-scoping of the six run_graph ops, and
the streaming body cap, but flagged the tests as self-confirming (branch ownership mocked as mere
existence; enqueue/run replaced with spies) and raised 6 findings. All fixed, each re-tested
against the REAL surfaces (real authored branches, real identities, real run_graph dispatch, real
enqueue → runs DB) in `tests/test_webhook_inbound_hardened.py`:

1. **Author-gate bypass (CRITICAL).** mint/create only checked branch EXISTENCE. Fixed:
   `_resolve_owned_branch` (webhook_ops.py) requires the branch's `author` to be the caller
   (`current_request_actor_id()`) or the caller's own universe actor. Real repro test: an attacker
   with write to their own universe cannot mint/create a Source for a victim's authored branch.
2. **DARK flag was not a boundary (CRITICAL).** The flag only gated scheduler startup; the route
   was always mounted and a plain hook enqueued regardless. Fixed: ONE master flag
   `TINYASSETS_INBOUND_ENABLED` gates the route MOUNT (universe_server) AND `handle_hook` re-checks
   it. Real test: flag off ⇒ route absent AND no run row created.
3. **Revocation race (HIGH).** Token resolved once then enqueued with no re-check. Fixed: a fresh
   active re-resolve immediately before enqueue/emit (webhook_inbound), and a pre-fire
   `_subscription_active` re-check in the bus (scheduler). Tests cover both.
4. **Replay bypassable (HIGH).** No dedupe on plain hooks; Source dedupe trusted a caller header.
   Fixed: server-side idempotency key = `sha256(token + exact body)` (never a header), durable
   `webhook_deliveries` store, applied to BOTH paths; the bus `event_id` is that key. Real test:
   same body with an ATTACKER-ALTERED delivery header fires once; a genuinely new body fires again.
5. **Valid-token execution DoS (HIGH).** Admission capped rate but the executor queue was
   unbounded. Fixed: per-universe in-flight back-pressure (`count_active_universe_runs`) → 503 when
   saturated, and dedupe fast-path BEFORE admission so replays never consume budget. Real test:
   a universe at its in-flight cap is refused and enqueues nothing.
6. **Credential/header persistence (HIGH).** Denylist forwarded every other header; token stored
   plaintext. Fixed: header ALLOWLIST (webhook_inbound), and token HASH-at-rest (`token_hash` +
   non-secret `token_prefix`; list never returns the raw token). Real test: CF-Access/Authorization/
   X-Api-Key never reach the durable run input; the raw token is nowhere on disk.
7. **404 not uniform (MEDIUM).** An active-but-unusable token returned 500. Fixed: a single
   `_NOT_DELIVERABLE` 404 for every non-deliverable state (unknown/revoked/disabled/bus-off/
   vanished-branch/internal-error), logged loudly internally. Real test asserts 404 for each.

Tests: 165 in the webhook+scheduler suite (46 real webhook/source + the hardened integration),
+122 server-boot/dispatch regression; ruff clean; plugin mirror rebuilt. Still DARK behind the
flag. Awaiting the lead's authoritative re-review.

## Round 3 → VERDICT: reject (findings 1/2/4-identity closed; the rest were check-then-act, broke under CONCURRENCY). NOW ATOMIC.

Codex round-3 (concurrent probes) confirmed the gates were TOCTOU: sequential tests passed but N
concurrent requests overshot the cap, double-admitted duplicates, and slipped past a revoke.
Reworked into ORDERED ATOMIC gates, each serialized by a single `BEGIN IMMEDIATE` transaction, and
re-tested with REAL CONCURRENT requests (ThreadPoolExecutor):

1. **Dedupe FIRST, atomic (Codex #4/#5).** `claim_delivery` is now an INSERT under the
   `webhook_deliveries` PRIMARY KEY, run BEFORE admission. N concurrent identical deliveries →
   exactly ONE winner (the losers get IntegrityError → 202-replay, consuming no budget). A
   downstream reject calls `release_delivery` so a channel retry still runs. Concurrent proof:
   `test_claim_delivery_is_atomic_under_concurrency` (16 racers → 1) +
   `test_concurrent_identical_deliveries_enqueue_exactly_one` (8 racers → 1 real run).
2. **Concurrency reserve, atomic (Codex #5).** Replaced `count_active_universe_runs` + enqueue with
   `reserve_dispatch`: one `BEGIN IMMEDIATE` that reconciles finished/abandoned reservations, counts,
   and inserts a reservation IFF under cap. Released on run termination (reconcile vs the runs table),
   on failure (`release_dispatch`), or by TTL (abandoned). Both paths reserve here → one counter
   bounds both. Concurrent proof: `test_reserve_dispatch_is_atomic_under_concurrency` (20 racers,
   cap 5 → exactly 5) + `test_concurrent_requests_never_overshoot_the_inflight_cap` (10 racers,
   cap 4 → exactly 4, runs blocked so slots stay held).
3. **Active-token consumed atomically (Codex #3).** The token active-check is now IN the same
   `reserve_dispatch` transaction (same DB as `revoke`), so `BEGIN IMMEDIATE` serializes them: a
   revoke either lands before the reserve (no run) or after (the run was already authorized —
   correct). Proof: `test_a_concurrent_revoke_and_reserve_never_both_win` + the end-to-end
   `test_a_revoked_token_triggers_no_run`. Source path: `_inbound_event_run_fn` links/releases the
   reservation and the bus re-checks `_subscription_active` before firing.
4. **Token-hash migration (round-2 gap).** `_migrate_hooks_to_hashed` transactionally rebuilds a
   legacy plaintext-`token` table into the hashed schema (hash existing tokens, carry `source_id`,
   drop the plaintext column). Proof: `test_migrates_a_legacy_plaintext_token_table`.
5. **True uniform 404.** All non-deliverable states share ONE `_NOT_DELIVERABLE` (same
   status+body+content-type), including internal faults (an outer exception boundary normalizes a
   valid-token DB error to 404, not 500); every exit logs its real classification.

Reconcile source: reservations released on run termination via `terminal_run_ids_for_universe`.
Verification: 174 webhook+scheduler tests (incl. the concurrent + migration proofs) + 377
server-boot/dispatch regression; ruff clean on touched files; plugin mirror rebuilt.

**One honest residual (flagged for the lead):** the caller-facing *status/body/content-type* is
uniform, but the TIMING is only reduced, not fully normalized — an unknown token short-circuits at
resolve, whereas a valid token that is rate-limited/at-cap does more work (claim → admit → reserve).
Fully constant-time behavior would need a normalized minimum-work path or artificial delay; flagged
rather than papered. Still DARK behind `TINYASSETS_INBOUND_ENABLED`.

## Ship decision (founder, round-4): SHAPE-CORRECT MVP ships LIVE first; deep hardening is DEFERRED.

Founder course-correction: the correct SHAPE + basic safety is closed and confirmed — author-gate,
dark=boundary, replay IDENTITY (server-derived key), owner-scoped ops, event bus + Source nodes.
We ship that MVP live (dark-flagged, tested as a real user via Slack/app) and harden AFTER, because
on a **dark, single-founder, not-yet-publicly-routed** path the remaining edges do not bite anyone.

Atomic-gate improvements already built are KEPT (a cap/dedupe/reserve that holds is strictly better).
The following are explicitly DEFERRED to a post-live-MVP hardening pass — TRACKED here, not lost, and
NOT to be chased further before ship:

- **Atomic admission under concurrency** — the reserve/dedupe are already atomic; any further
  concurrency edges (e.g. cross-worker fairness, reservation-GC precision) are post-live.
- **Atomic revocation fence** — the sub-millisecond revoke-after-reserve window (a self-inflicted,
  single-founder, one-extra-run race) is post-live; the reserve↔revoke serialization already built stays.
- **Token-hash migration for a prior committed schema** — the transactional migration is built and
  tested; broadening it (or removing it once no legacy DB can exist) is post-live.
- **404 timing uniformity** — status/body/content-type are uniform today; constant-time normalization
  of the side-channel is post-live.

Gating to LIFT before PUBLIC (multi-tenant / publicly-routed) exposure: revisit each deferred item,
and only then flip `TINYASSETS_INBOUND_ENABLED` (the /mcp/hooks receiver rides the existing /mcp tunnel — no tunnel change).
