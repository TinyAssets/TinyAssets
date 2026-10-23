## Context

Binding: `owner-daemon-in-box/research.md` and design T1-T10; registry N1; [founder baseline #4624](https://github.com/TinyAssets/TinyAssets/issues/4624). C2's wake-triggers work is in `automation-event-triggers`, including served webhook create/list/revoke. Preserve that management contract, replace its delivery target. This is a proposed cutover, not a claim that N1 is built.

## Goals / Non-Goals

One external-event ingress, one owner box, one session start path. No channel runtime, owner-content store, agent selection or retry queue in shared services; no new waker, platform mail domain or platform feature editor.

## Decisions

### 1. Placement and exact shared diff

`/hooks/inbound` names the door; its sole route template remains `/hooks/inbound/{binding}` (one segment), preserving existing token URLs. POST delivers events; GET on the same route supports package-handled subscription challenges. There are no `/slack`, `/teams`, `/mail` or `/mcp/hooks` aliases.

| Location | N1 addition/change | Forbidden here |
|---|---|---|
| `tinyassets/owner_router.py` | Replace token-only authentication with verified-binding authentication, then use existing `forward` and cold-start transport | Payload interpretation, session selection, owner files |
| `tinyassets/router_credentials.py` | Extend existing binding map/relay with opaque binding ID, owner, connection reference, verification profile digest and revision; publish/revoke only through the bound owner relay | Branches, agents, subscriptions, inbox or dedupe state |
| New `tinyassets/broker/inbound_verification.py`, wired in existing broker RPC dispatcher | Bounded generic verifier over raw bytes and declarative profile; broker-held secret lookup; return authenticated binding/revision and body digest | Provider switches, executable package callbacks, owner policy |
| Existing `tinyassets/auth/middleware.py` ingress predicate | Keep exact single-segment exemption; hand authentication to the router; never broaden to prefix authentication | A second verifier |
| Owner box: existing `webhook_inbound.py` receiver and connector packages | Durable acceptance, parsing, challenge response, agent/thread selection, `session.start`, reply outbox, subscriptions, pause and owner grants | Raw channel credentials |
| Existing waker and proxy | Reuse unchanged T7 wake and T6 outbound credential injection | Event payloads or jobs in wake index |

Those are the complete shared additions: one verifier module and wiring/metadata in the existing router, binding relay and broker dispatcher. No new shared event service or storage beyond identity-binding metadata. Body bytes pass transiently through verification/forwarding and are never logged or persisted shared-side. Broker verification failures fail closed; awake forwarding otherwise retains T1's installed-snapshot path.

### 2. Verification is package data, not provider code

Connector installation registers a versioned, schema-checked verification profile through the owner's bound relay. The profile declares accepted methods, credential reference, signature/token location, canonical signed-byte recipe, algorithm allowlist, optional timestamp/skew bounds, and JWT issuer/audience/subject constraints with a pinned key source. Generic primitives are constant-time token comparison, HMAC and asymmetric/JWT verification; no arbitrary expressions, imports, network URLs from requests, or executable hooks in shared services. Key refresh uses broker-controlled public destinations. Reject unsupported profiles at registration.

| Package | Verification data to supply | Box-only work |
|---|---|---|
| Slack | Signed-body HMAC profile, timestamp/header recipe and replay freshness bounds | Challenge, event envelope, thread mapping and replies |
| Teams | Profile for the selected connector transport: signed bearer/JWT claims and key source, or subscription token proof | Activity/notification parsing, subscription validation and replies |
| WhatsApp | Signed-body HMAC profile and separate subscription-challenge token profile | Challenge response, message extraction and replies |
| Owner email | Connected provider's notification signature/JWT or subscription-token profile | Subscription renewal, cursor fetch, mailbox/alias filtering and sending |
| Generic webhook | Existing URL capability digest or configured bearer/HMAC profile | User package's payload parsing and agent selection |

Profiles must ship with official-protocol test vectors for the selected transport; this table does not declare different Teams or mail transports interchangeable. Proof and bound subscription identity must select exactly one owner. Where a provider signature proves only an app, bind a per-owner subscription capability or authenticated recipient selector as identity data; reject ambiguous app-wide fan-out. An unsigned body `owner`, `box` or routing header never selects authority. Strip caller trust headers and credential material before forwarding; preserve payload bytes verbatim. The box verifies its existing per-box router credential.

### 3. Authority and delivery

The binding identifies the owner, connection and profile, never an agent. T5 admission selects the owner's one box and governs wake/forward. Disconnect revokes the binding and broker credentials before reporting completion; revision invalidation prevents stale proofs from opening a box. Concurrent withdrawal obeys the existing T5 admission barrier, including cold starts. No missing owner is provisioned by a webhook. Unknown, disconnected or unadmitted bindings return uniform 404 with no wake, store or start; transport/verification-service outages return retryable 503.

The verified request wakes a sleeping box through the existing router lifecycle call and forwards unchanged payload plus trusted binding/revision/digest metadata. Only the box parses it. Its connector selects an installed agent under current owner grants and supplies `session.start(agent_ref, input, parent)` with an inbound receipt as parent. External text never grants authority. Revoked local grants, pause, malformed content or missing packages produce explicit local refusal/defer receipts, not a fallback agent. Deferred work uses C2's existing box-local wake machinery. Handshakes and rejected/ignored protocol notifications are not accepted agent events.

### 4. At least once, durable before acknowledgement

The provider owns retries until the box commits acceptance; no early shared 2xx or shared payload spool. Cold-start timeout returns 503; next delivery reuses the ordinary wake path. A 2xx event acknowledgement means raw envelope and pending dispatch are durable in the box. A crash after commit/before acknowledgement returns that receipt on redelivery.

Reuse/reshape `storage/webhook_hooks.py` delivery state into a box-local inbox and dispatch outbox, keyed by `(binding revision, stable provider event ID)`. A generic sender must supply a stable idempotency key; do not dedupe by payload hash, which merges distinct equal messages. Store a payload digest to reject conflicting reuse of an ID. Retain accepted IDs for the binding revision's lifetime; if storage admission fails, return 429/503 without accepting. Old revisions never resurrect after reconnect. Provider freshness checks reject stale signed requests; authentic retries within the protocol window resolve to the same receipt.

Commit acceptance and pending dispatch atomically. Retry dispatch through the existing wake/automation pump with the receipt ID as the session-start idempotency key; session creation must atomically bind that key to its durable start record. This is a required C7/C8 handoff, not a second start primitive. A crash on either side of start cannot create a second session. Preserve pause, resume and current-grant recheck in the box. Reply outbox entries use stable operation IDs and provider idempotency where supported; ambiguous non-idempotent send results are recorded for reconciliation, never blindly resent or advertised as exactly once.

### 5. Limits, email and replies

Use existing transport byte/concurrency limits before expensive verification; generic per-binding/owner ingress quotas are admission metadata. Return 413 for oversized requests and 429 with `Retry-After` for overload, with no acceptance. Box queue/storage/work limits apply after authentication; duplicate accepted deliveries return their receipt without spending another execution allowance. Shared transport overload can still throttle retries. Connector-specific backoff stays in the box.

Email arrives through the owner's connected account subscription at this same door. Mail content is fetched and cursor state kept in the box; send/reply uses that account's authorized sender or alias. Subscription renewal (and polling where the account lacks push) uses existing box-local scheduled sessions and T7's unchanged next-wake index, never a shared mail worker. No TinyAssets inbox, mail domain, SMTP ingress or credential in the box. All channel sends/fetches use T6's egress proxy, which injects only the bound service's broker-held credential; disconnect blocks later sends too.

## Migration Plan

Build after C7/C8 on the isolated path. Within the coordinated cutover, convert branch/source bindings to connector configuration and an explicit agent reference inside each box; refuse and surface bindings that cannot be mapped. Preserve existing token URLs/digests where valid. Retain accepted delivery receipts across conversion; drain legacy pending deliveries before switching dispatch, so they cannot run twice.

Delete `_enqueue_branch_run` and `_emit_source_event` from `tinyassets/webhook_inbound.py`, the inbound-only `_inbound_event_run_fn` bridge/wiring in `tinyassets/universe_server.py`, payload-hash/time-window dedupe and the receiver's duplicate token authentication. Remove `router_credentials.hook_identity`'s dependency on the owner receiver. Replace the existing receiver route in place, never mount another. Keep non-inbound automation events and the C2 pump. Replace tests asserting direct branch/source dispatch with session-start assertions. No shared owner-store checks are carried forward.

At implementation sync, replace C2's pending direct-enqueue requirement with this contract and sync both together; do not archive or complete C2's unrelated timezone/wake obligations here. Rollback disables ingress and returns retryable failure while preserving the inbox; it never restores the deleted dispatcher. Spec-only delivery leaves every implementation task unchecked.

## Risks / Trade-offs

- Provider acknowledgement deadlines may be shorter than cold start: retryable failures plus in-box catch-up from provider cursors where available. A non-retrying generic sender must retry using its event ID; no promise of accepting an event before durable box storage.
- Credential rotation and subscription renewal can overlap: explicit binding revisions and protocol fixtures prove the transition; no provider-specific patch in shared code.
- Before fixing any test/finding, ask whether the class exists under T1-T10. Move owner logic into the box and delete the old shared path if it does not.

## Verification

Implement `tests/test_inbound_door.py` as the `inbound-door` guard: enumerate the deployed route table, trace each channel fixture through the real router/box receiver to the real session entry, and fail on any direct branch/source enqueue. Test valid/forged/stale proof, recipient substitution, disconnect/admission races, sleep/timeout/retry, concurrent duplicates and crashes around each commit/start boundary, equal payloads with different IDs, conflicting IDs, limits, pause, handshake and grant refusal. Assert no shared owner-content reads and no credentials in box inputs/logs; replies must use proxy injection. Reuse owner-router, served-webhook and automation-event targeted regressions. Run through `python scripts/linux_oracle.py -- <targeted files>` plus ruff; implementation live proof is a channel message and reply with host devices off. No live proof or production access in this spec PR.
