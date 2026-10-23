## ADDED Requirements

### Requirement: One verified ingress maps positively to one box
The platform SHALL expose one external-event route template, `/hooks/inbound/{binding}`, and preserve tap-to-install shared TinyAssets apps without owner-created developer apps. Shared services SHALL authenticate, map to one admitted box, wake and forward; owner logic, data, agent selection and durable event state SHALL remain inside that box. No general selector language, provider-specific shared code, owner-content path or shared event/retry queue SHALL be introduced.

#### Scenario: Supported routes and deferred transports
- **WHEN** a supported Slack, WhatsApp, connected-account email or generic webhook event arrives
- **THEN** it uses the same route and verification primitives; only Slack uses fixed authenticated installation extraction, while the others use per-binding URLs with no body-field routing
- **AND** WhatsApp is eligible only with fixture-proven per-account callback override and account-isolated batches; unavailable/unproved override excludes it from N1
- **AND** Teams remains a follow-up until fixtures prove authenticated per-installation identity and installation-scoped outbound credentials

#### Scenario: Strict Slack extraction after HMAC
- **WHEN** Slack delivers within the 1 MiB byte cap
- **THEN** the broker verifies raw-body HMAC-SHA256 and the five-minute timestamp tolerance before any JSON parse; hex encoding, `v0=` and the `v0:<timestamp>:<body>` recipe are profile data
- **AND** only uncompressed UTF-8 JSON objects within depth 32 and without duplicate keys anywhere are eligible; the fixed singleton authorization envelope exposes object keys only, with no configurable index/path operations
- **AND** boolean `is_enterprise_install` selects nonempty string `enterprise_id` for org-wide installs or `team_id` otherwise; decoded UTF-8 identity bytes compare exactly without normalization/coercion

#### Scenario: Unsupported or ambiguous shared delivery
- **WHEN** an authenticated Slack delivery has multiple authorizations, Slack Connect scope, an unsupported encoding, malformed/bounded-out JSON or missing/ambiguous identity
- **THEN** it receives 200 no-op, with no wake, forward, receipt or shared persistence
- **AND** only fixture-proven single-installation shapes with `is_ext_shared_channel=false` may route; a truncated singleton authorization list alone is insufficient and no shared authorization lookup/fanout is added

#### Scenario: Shared mapping absent or withdrawn
- **WHEN** an authenticated shared delivery has no mapped admitted installation, including a disconnected, erased or suspended owner
- **THEN** it receives 200 no-op even during maintenance, so dead installations do not consume the shared app's failure budget
- **AND** no stale revision may complete wake/forward after T5 admission withdrawal, including concurrent cold starts

#### Scenario: Per-binding refusal, outage and maintenance
- **WHEN** a per-binding callback is unknown/revoked/unadmitted, or an admitted event cannot be delivered
- **THEN** the former returns stable 404 `not_found`; maintenance, verification outage or cold-start timeout returns retryable 503 without acceptance or shared payload storage
- **AND** maintenance must fit the provider retry/failure budget; a 2xx no-op SHALL NOT be used to hide loss of an admitted event

### Requirement: Installation authority owns shared profiles and mappings
Only platform installation authority SHALL write shared-app profiles and installation records, including extraction/encoding data, keys, issuer/audience, secret references and revisions. Identity SHALL come from server-side provider token exchange/inspection. OAuth state SHALL be single-use, bound to the starting owner and authenticated browser session, and checked in that same session before publication. Client signup IDs and callback query IDs SHALL NOT authorize a mapping.

#### Scenario: Conflicting installation
- **WHEN** another owner attempts to claim an existing installation identity
- **THEN** authority checks the existing credential with the provider and atomically preserves a live installation, defers on indeterminate status, or retires a confirmed revoked record/revision before replacement
- **AND** refusal never revokes provider tokens, including a token reissued for the existing installation

#### Scenario: Disconnect, erasure and wake reconciliation
- **WHEN** an owner disconnects, is erased, or the provider revokes an installation
- **THEN** disconnect withdraws its mapping/revision and scoped credentials and uninstalls at the provider; erasure removes its records; provider revocation is reconciled through installation authority when the box wakes or another install conflicts
- **AND** the box reconciles its snapshot view and credential status on wake; no notification retry queue exists, and neither another installation nor the shared signing secret is removed

#### Scenario: Setup has no owner destination
- **WHEN** Slack sends `url_verification` or Meta sends a verification GET during app/callback setup
- **THEN** one-time platform setup outside the steady-state door handles the challenge with broker-held proof; no box, agent event or permanent shared payload handler is created
- **AND** ordinary owner-specific subscription challenges remain box-local

### Requirement: Verification custody and forwarding have one authority
The pure verifier SHALL accept only schema-checked capability/token, HMAC-SHA256 or per-binding pinned RS256 profiles. Unsupported algorithms/expressions/hooks and request-supplied key URLs SHALL be rejected. Only the authenticated router SHALL call `INBOUND_VERIFY`; the broker SHALL resolve the profile and credentials from the binding and reject caller-supplied profiles. Boxes SHALL NOT modify shared profiles or use app-wide outbound credentials.

#### Scenario: Awake box during control-plane outage
- **WHEN** the installed snapshot and awake box are available but control-plane RPCs are not
- **THEN** URL-capability/pinned-key verification still forwards using the same pure module as the broker
- **AND** secret-dependent profiles return retryable 503 if broker verification is unavailable, the explicit T1 exception preserving broker-only custody

#### Scenario: Per-binding custody and email key rotation
- **WHEN** an owner-specific profile references a credential or pinned email keys
- **THEN** foreign credential references are refused; only that owner's relay publishes its keys, and its pre-scheduled C2 wake refreshes them through egress at a declared interval within provider rotation overlap
- **AND** unknown/stale keys return retryable 503 until republished, without inbound-triggered refresh or a shared fetcher; shared HMAC profiles have no box-owned key refresh

#### Scenario: Forwarded proof and revision
- **WHEN** a verified installation is forwarded to its admitted box
- **THEN** raw payload bytes remain verbatim; trusted binding/digest metadata carries the installation record revision, never the shared profile revision, and the box checks its per-box router credential
- **AND** case-insensitive stripping removes caller trust headers, `X-Slack-Signature`, `X-Hub-Signature-256`, `Authorization`, `Proxy-Authorization`, `Cookie` and all configured proof/token headers; routing metadata contains no capability/query token
- **AND** caller owner/box substitutions and invalid/stale proofs authorize no destination

### Requirement: Durable accepted events enter session start inside the box
The box SHALL enforce current grants/pause, select the agent and dispatch through `session.start(agent_ref, input, parent)` with its receipt as parent. Raw envelope and pending dispatch SHALL commit before event acknowledgement. C2's existing pump and T7 wake index SHALL be reused; direct webhook branch/source dispatch SHALL be deleted.

#### Scenario: Exactly one route and session entry
- **WHEN** implementation tests enumerate routes and deliver each supported fixture through the real router and receiver
- **THEN** exactly one distinct GET/POST template exists, and each accepted event reaches the real session entry once with expected owner, agent, preserved bytes and receipt
- **AND** there are no direct branch/source enqueue calls, shared owner-content reads or credentials in box inputs/logs

#### Scenario: Handshake or local refusal
- **WHEN** the box sees an owner subscription handshake, malformed event, missing package, revoked grant or paused workflow
- **THEN** it responds or records explicit refusal/defer, with no fallback agent; deferred accepted work resumes through the same pump/session entry
- **AND** handshakes and authenticated shared no-ops are not accepted agent events

#### Scenario: Cold start, concurrent retries and crashes
- **WHEN** delivery times out before durable acceptance, or the same binding/installation revision and event ID recur across acceptance/start/ack crashes
- **THEN** timeout returns 503 and retry uses ordinary wake; atomic inbox/outbox and receipt-keyed durable session creation yield the same receipt and at most one session
- **AND** reused IDs with different digests are refused, equal payloads with different IDs stay distinct, and reconnect never resurrects old revisions

#### Scenario: Generic identity and finite replay retention
- **WHEN** a generic sender omits/malforms nonempty `Idempotency-Key` or immutable Unix-seconds `X-Event-Timestamp`
- **THEN** it receives 400 `event_identity_required`, including legacy token-URL senders at cutover; no digest fallback exists
- **AND** event freshness is checked before dedupe even under fresh signatures; stale events receive 400 `stale_event`, receipts/digests expire after the profile window plus skew, and retries never extend retention
- **AND** generic freshness is 24 hours plus at most five minutes skew; pending work/session-start keys remain until completion

#### Scenario: Capacity and outbound work
- **WHEN** transport/storage capacity is exhausted or a reply is ready
- **THEN** excessive size gets 413, rate/capacity limits get 429 with retry guidance or storage 503, and accepted duplicates spend no new execution allowance
- **AND** sends/fetches use only installation/account-scoped proxy-injected credentials; disconnect blocks later sends, and ambiguous non-idempotent results await reconciliation
- **AND** email stays in the connected mailbox with local cursors and C2 renewal/polling, never a platform mail domain, shared mail worker or second scheduler
