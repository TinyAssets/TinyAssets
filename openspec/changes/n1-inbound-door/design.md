## Context

Binding: `owner-daemon-in-box/research.md` and design T1-T10; registry N1; [founder baseline #4624](https://github.com/TinyAssets/TinyAssets/issues/4624). This is a spec-only cutover proposal. C2's `automation-event-triggers` management contract stays; its delivery target becomes the owner's existing `session.start`.

## Decisions

### 1. One door; owner work stays in the box

Keep the sole route template `/hooks/inbound/{binding}` (one segment), preserving valid token URLs. POST delivers events; GET supports per-binding subscription challenges in the box. No channel aliases, shared event dispatcher, owner-content paths, payload store or retry queue.

| Location | N1 change |
|---|---|
| `tinyassets/owner_router.py` | Verify binding, resolve positive identity mapping, reuse existing admission/wake/forward |
| `tinyassets/router_credentials.py` | Binding/profile metadata and installation records in the installed routing snapshot; no agent, subscription or inbox state |
| New `tinyassets/broker/inbound_verification.py`; broker `_Connection._connection_op` | One pure bounded verifier; `INBOUND_VERIFY` accepts only the authenticated router, never a box. Broker resolves the profile/secret from the binding ID, never a caller-supplied profile |
| `tinyassets/auth/middleware.py` | Preserve exact single-segment ingress exemption; router owns authentication |
| Owner receiver/packages | Durable inbox, parsing, grants, agent/thread selection, session start, subscriptions and replies |

The router uses the same pure verifier for URL-capability digests and pinned public keys from its snapshot. Secret-dependent verification runs only in broker custody. Its broker dependency is an explicit T1 exception: unavailable verification returns retryable 503 even for an awake box; other awake forwarding still needs no control-plane RPC. No provider switches or executable verification callbacks. The existing T7 waker and T6 proxy remain unchanged.

### 2. Tap-to-install, with only safe N1 transports

**Founder decision (2026-10-23):** shared TinyAssets apps, installed through owner OAuth/embedded signup; owners never create developer apps. This decision survives narrower N1 transport support.

| Channel | N1 route and proof |
|---|---|
| Slack | One shared app endpoint; raw-body HMAC, then fixed installation-identity extraction below |
| WhatsApp | Shared app with embedded signup; a distinct per-binding callback for each owner's business account, installed through the provider's per-account callback override; raw-body HMAC, **no body-field routing** |
| Generic webhook | Existing per-binding URL capability, header token or HMAC |
| Connected email | Owner-account per-binding subscription callback; supported token/HMAC/pinned-key proof; mailbox work stays in the box |
| Teams | **Follow-up, out of N1** until a fixture proves authenticated per-installation identity and installation-scoped outbound credentials |

WhatsApp eligibility requires a provider fixture proving the override is available for the selected signup flow and deliveries to that URL contain only that account's events, including batching. If unavailable or unproved, WhatsApp is out of N1: no shared-account selector, array indexing or payload splitting fallback. The [provider override documentation](https://developers.facebook.com/docs/whatsapp/embedded-signup/webhooks/override) is the implementation reference, not a claim of tested availability.

Verification profiles are schema-checked data: methods, token location, URL-capability digest, raw HMAC-SHA256 recipe (optional literal version and timestamp header), signature header, hex/base64 encoding and literal prefix; per-binding email may use pinned RS256 keys and exact issuer/audience/subject constraints. Reject other algorithms, expressions, request-supplied key URLs and executable hooks. Per-binding references resolve only in that owner's broker custody. Event freshness and clock-skew bounds are finite; the box must extract an immutable event timestamp.

Shared-app profiles have **one writer: platform installation authority**. That includes extraction/encoding data, any issuer/audience/keys, revisions and platform signing-secret reference; boxes cannot publish or refresh them. N1 shared apps use HMAC, so the shared JWT/key-refresh machinery is deleted. Only owner-specific profiles use the bound owner relay; an email connector refreshes its own pinned keys through egress on a pre-scheduled C2 wake, at a declared interval within provider rotation overlap. Unknown/stale keys return retryable 503 `verification_keys_unavailable`; shared verification never fetches keys.

### 3. Slack: fixed authenticated identity, no selector language

Verify `X-Slack-Signature` over `v0:<X-Slack-Request-Timestamp>:<raw body>` with the platform signing secret; hex and `v0=` are data, timestamp tolerance is five minutes. [Slack signs the raw bytes](https://docs.slack.dev/authentication/verifying-requests-from-slack/). Apply a 1 MiB byte cap before HMAC; parse only after successful verification. Accept uncompressed UTF-8 JSON objects, depth at most 32, rejecting duplicate keys throughout. No coercion, case folding or Unicode normalization: nonempty identity strings compare byte-for-byte as decoded UTF-8.

The fixed envelope schema declares `authorizations` as exactly one object in a singleton array. Identity extraction within that object uses only literal object keys: `is_enterprise_install=true` selects `enterprise_id`; `false` selects `team_id`. These keys, types and cardinality are platform-owned data, not provider-specific code. Require the boolean discriminator and corresponding string; never infer org-wide installation from the mere presence of an enterprise ID. No JSONPath, configurable array-index steps, wildcard, fallback paths or general selector machinery. [Slack documents this envelope and its installation fields](https://docs.slack.dev/enterprise/developing-for-enterprise-orgs/).

Only fixture-proven single-installation envelope families are eligible. Require `is_ext_shared_channel=false`; multiple authorizations, Slack Connect, missing/ambiguous scope and unsupported encodings (including form-encoded interactions/commands) are authenticated 200 no-ops. A singleton list is **not proof of uniqueness**: [Slack truncates authorizations](https://docs.slack.dev/changelog/2020-09-15-events-api-truncate-authed-users/), including shared-channel deliveries. If the envelope cannot establish one installation, do not route or query/fan out authorizations in shared code. Envelope shape/identity checks are the entire shared body interpretation; message content and event handling stay in the box.

### 4. Installation authority and lifecycle

The platform installation authority derives identity from the provider's server-side token exchange/inspection, never callback parameters or client embedded-signup IDs. OAuth state is single-use, bound to the starting owner and browser session, and checked in that same authenticated session before exchange/publication. For Slack, exchange `is_enterprise_install` chooses `enterprise.id` or `team.id` ([OAuth contract](https://docs.slack.dev/authentication/installing-with-oauth/)). Store `(app, installation kind, provider installation ID) -> owner, binding, credential reference, revision`, with only installation-scoped outbound credentials.

Publish atomically. On a competing install, validate the **existing** credential with the provider: a live credential keeps its owner; an indeterminate check defers the new install; a confirmed revoked credential retires the old record/revision before replacement. A refused second install discards its local result and **never revokes provider tokens**, which may be the existing installation's token.

Disconnect withdraws mapping/revision and scoped credentials, and uninstalls that installation at the provider. Provider-side uninstall/revocation is reconciled by the box when it wakes, against its routing snapshot view and provider credential status through the broker; it requests retirement through installation authority. No shared notification retry queue. Conflict checks also retire stale records while an old box is suspended; erasure removes that owner's installation records. Reconnect creates a new installation revision; other installations and the platform signing secret remain intact.

Slack `url_verification` and Meta verification GET are one-time platform setup outside the steady-state door, handled by installation authority with broker-held setup proof. They neither select a box nor start a session; the setup responder is not a permanent event handler or alternate ingress. Owner-specific subscription challenges remain in their mapped box.

### 5. Routing, acknowledgements and replay

Forward raw bytes verbatim with trusted binding, body digest and **installation record revision** for shared-app installs (binding revision for generic/owner-account callbacks), never the shared profile revision. Strip caller trust headers and proof/credential headers case-insensitively, including `X-Slack-Signature`, `X-Hub-Signature-256`, `Authorization`, `Proxy-Authorization`, `Cookie` and every configured token/signature header; remove capability/query tokens from forwarded routing metadata. The box authenticates its per-box router credential. Caller owner/box fields never choose a destination; T5 withdrawal invalidates proofs and prevents concurrent wake/forward under old admission.

| Result | Response and effect |
|---|---|
| Invalid/stale proof | Refuse; no parse, wake or forward |
| Authenticated unsupported Slack shape, or unmapped/disconnected/unadmitted installation on a shared endpoint | 200 no-op; no wake, persistence or accepted-event receipt |
| Unknown/revoked/unadmitted per-binding callback | 404 `not_found`; no wake/forward |
| Admitted delivery during maintenance, verification outage or cold-start timeout | Retryable 503; no shared payload spool |
| Accepted event | 2xx only after raw envelope and pending dispatch commit in the box |

No-op responses take precedence over maintenance for already-authenticated unroutable shared deliveries. This keeps dead installations from spending the whole app's failure budget. [Slack disables subscriptions after more than 95% failed attempts over 60 minutes](https://docs.slack.dev/apis/events-api/); bound maintenance by its retry/failure budget and verify recovery, without falsely acknowledging admitted events. Overlong outages can still disable delivery; N1 makes no lossless-outage promise.

The box parses, enforces current grants/pause and chooses `session.start(agent_ref, input, parent)` with its receipt as parent. Missing packages, malformed content or revoked grants get explicit refusal/defer receipts, never a fallback agent. Protocol/no-op acknowledgements are not accepted agent events.

Reuse `storage/webhook_hooks.py` as a box-local inbox/dispatch outbox keyed by `(installation/binding revision, stable event ID)`. Generic senders, including existing hooks, require nonempty `Idempotency-Key` and `X-Event-Timestamp` (Unix seconds, immutable across retries); missing/malformed values return 400 `event_identity_required`. Generic freshness is 24 hours plus at most five minutes skew. Check event freshness before dedupe, even under a fresh signature; expired events return 400 `stale_event`. Prune receipts/digests after the finite window, never extend it on retry; retain pending work/session-start keys until completion. Same ID/different digest is refused; equal payloads with different IDs remain distinct. Old revisions never resurrect.

Acceptance and pending dispatch commit atomically. C2's pump retries with the receipt as session-start idempotency key; C7/C8 must atomically bind it to durable session creation. Crashes before/after acceptance, start or acknowledgement cannot create a second session. Apply existing byte/concurrency and box capacity limits (413, 429 with `Retry-After`, or storage 503); accepted duplicates spend no new execution allowance.

All sends/fetches use T6's proxy with the installation/account-scoped credential injected only upstream; no app-wide outbound token is exposed through a box's connection. Reply outbox operations use provider idempotency where supported; ambiguous non-idempotent sends await reconciliation. Email uses the owner's connected mailbox/alias, local cursors and C2 renewal/polling; no platform mail domain, shared mail worker or new waker.

## Migration and verification

Build after C7/C8 on the isolated path. Convert branch/source bindings to explicit box-local connector/agent configuration; surface unmappable bindings. Preserve valid URLs/digests and unexpired receipts; require generic sender headers and drain legacy pending deliveries before cutover.

Delete receiver `_enqueue_branch_run`/`_emit_source_event`, `universe_server.py`'s inbound-only `_inbound_event_run_fn` bridge, payload-hash/time-window dedupe, duplicate receiver authentication and `router_credentials.hook_identity`'s receiver dependency. Replace the route in place; retain non-inbound automation and C2's pump. At implementation sync, replace C2's direct-enqueue requirement together with N1, leaving unrelated wake/timezone work alone. Rollback closes ingress with retryable failure, preserving inboxes; never restore the old dispatcher.

Create `tests/test_inbound_door.py` at implementation: real route -> box -> session start, byte preservation, no direct enqueue/shared owner reads/credentials, platform-only profile writer and router-only verify, same-session OAuth, live/revoked/indeterminate conflicts with no reject-time revocation, erasure/wake reconciliation, installation revision/header stripping, Slack workspace/org identities and ambiguous/truncated/encoded no-ops, WhatsApp account-isolated override batches and unavailable-override exclusion, Teams exclusion, setup outside delivery, HMAC representations, admission races, cold retry, duplicate/crash/freshness/capacity cases, proxy replies, and per-binding email key refresh while asleep. Keep the existing awake-forwarding outage test unchanged; add an inbound test proving snapshot-only verification survives and secret-dependent verification returns 503 on broker outage.

Run targeted files via `python scripts/linux_oracle.py -- <files>` plus ruff. Reuse `tests/test_owner_router.py`, `tests/test_automations.py`, `tests/test_owner_waker.py` and `tests/test_owner_next_wake.py`; port C2 management assertions into the new guard (its cited webhook/event test files are absent). Implementation needs deployed-SHA proof and a naive-user message/reply with devices off. All implementation tasks remain unchecked; no production access or live proof in this PR.
