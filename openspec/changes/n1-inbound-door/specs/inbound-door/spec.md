## ADDED Requirements

### Requirement: One verified ingress maps positively to one box
The platform SHALL expose one external-event route template, `/hooks/inbound/{binding}`, and SHALL map its opaque binding route ID to one owner, verify only the declared signature/token profile without parsing business content, then wake and forward the raw request to that admitted owner box. Each channel SHALL use an owner-controlled app/subscription with a distinct binding URL; shared app callbacks requiring body recipient selectors are unsupported. Shared code SHALL contain no provider-specific branches, owner-content access or agent routing.

#### Scenario: Each supported channel uses the same door
- **WHEN** Slack, Teams, WhatsApp, connected-account mail or generic webhook delivers a valid event
- **THEN** the same router verifies its registered profile, derives the owner from the binding and forwards to that owner's box
- **AND** no channel-specific public ingress or shared event dispatcher is used

#### Scenario: Invalid proof or substituted owner
- **WHEN** proof is forged, stale, ambiguous, or the caller substitutes another owner/box
- **THEN** no substituted destination, wake or session is authorized

#### Scenario: Exactly one route and one session entry
- **WHEN** `tests/test_inbound_door.py` enumerates the deployed route table and sends an accepted fixture for each supported channel through the real router and box receiver
- **THEN** there is exactly one distinct inbound template, `/hooks/inbound/{binding}`, serving GET/POST with no aliases
- **AND** each fixture preserves raw payload bytes and reaches the real `session.start(agent_ref, input, parent)` exactly once with the expected owner, agent and receipt, with zero direct branch/source enqueue calls

#### Scenario: Disconnection or admission withdrawal
- **WHEN** the binding is unknown, disconnected, revoked or unadmitted outside a maintenance/cutover window, including withdrawal concurrent with a cold start
- **THEN** delivery returns stable 404 `not_found` and cannot complete a wake or forward under withdrawn admission

#### Scenario: Maintenance or cutover closure
- **WHEN** ingress admission is temporarily closed for maintenance/cutover
- **THEN** the door returns retryable 503 `maintenance`, taking precedence over stable admission refusal, without accepting or starting work

#### Scenario: Awake box during control-plane outage
- **WHEN** the routing snapshot is installed and the box is awake but control-plane lookups and lifecycle RPCs are unavailable
- **THEN** URL-capability and pinned-public-key profiles still verify and forward
- **AND** secret-dependent HMAC/token profiles require broker `INBOUND_VERIFY` and return retryable 503 without forwarding if it is unavailable; this is the explicit T1 exception needed to retain broker-only secret custody

### Requirement: Accepted events enter session start inside the box
The box SHALL parse, select the agent, enforce current owner grants and dispatch each accepted event through `session.start(agent_ref, input, parent)` with the inbound receipt as parent. The platform SHALL remove direct webhook branch/source dispatch and reuse C2's existing wake machinery.

#### Scenario: Channel event starts the selected agent
- **WHEN** a valid event is accepted by an installed connector with current grants
- **THEN** its durable receipt reaches the existing session entry with preserved payload and owner authority
- **AND** shared services never select an agent or inspect owner files

#### Scenario: Protocol handshake or local refusal
- **WHEN** the box receives a subscription handshake, malformed event, revoked grant, missing package or paused owner workflow
- **THEN** the box returns the protocol response or records an explicit refusal/defer result
- **AND** no fallback agent starts; deferred accepted events resume through existing box-local wakes and the same session entry

### Requirement: Sleeping boxes and duplicate delivery preserve durable acceptance
The router SHALL reuse its existing wake/forward transport without changing the waker or its `(owner, next_wake_at)` index. Event acknowledgement SHALL follow durable in-box acceptance and pending dispatch. The inbox and session start SHALL share a stable receipt/idempotency contract so at-least-once delivery starts at most one session per event.

#### Scenario: Cold start exceeds the request deadline
- **WHEN** an admitted owner's sleeping box cannot durably accept before timeout
- **THEN** the door returns retryable 503 and stores no shared payload
- **AND** retrying the same event can wake, commit and acknowledge it normally

#### Scenario: Concurrent retries and crash recovery
- **WHEN** the same binding revision and event ID arrive concurrently or after a crash before/after acceptance, start or acknowledgement
- **THEN** the durable inbox resumes pending work and returns the same receipt with at most one session start
- **AND** a reused ID with different payload is rejected, while different IDs with equal payload remain distinct events

#### Scenario: Legacy generic sender without event identity
- **WHEN** any generic sender, including an existing token-URL hook, omits or malforms `Idempotency-Key` or `X-Event-Timestamp` (Unix seconds fixed across retries)
- **THEN** the box returns 400 `event_identity_required` without acceptance or dispatch; cutover preserves valid URLs but requires sender changes, with no payload-digest fallback

#### Scenario: Receipt expiry
- **WHEN** the channel's finite event freshness window plus clock skew expires
- **THEN** replay receipts and payload digests are pruned and that stale event is refused before dedupe or acceptance, even with a fresh request signature
- **AND** generic hooks use 24 hours plus at most 5 minutes skew; retries never extend retention, and pending work retains only its dispatch/session-start key until completion

#### Scenario: Rate or capacity limit
- **WHEN** transport or box admission is exhausted
- **THEN** a new event receives 413 for excessive size or 429 with retry guidance for rate/capacity limits, without acceptance
- **AND** an already accepted duplicate consumes no additional session allowance; transport throttling can still apply

### Requirement: Channels and connected email keep credentials in broker custody
Connector packages SHALL perform channel parsing, subscriptions, local routing and replies inside the box. Channel credentials SHALL remain broker-held, and all outbound calls SHALL pass through the egress proxy with scoped credential injection. Email SHALL use the owner's connected account, never a platform mail domain.

#### Scenario: Owner-scoped verification custody and pinned keys
- **WHEN** a binding profile references credentials or public verification keys
- **THEN** its credential reference resolves only in that binding owner's broker custody, rejecting foreign references at registration and verification
- **AND** public keys are pinned through the bound box relay; shared services perform no key fetch or refresh and reject unsupported profiles, expired keys and request-supplied key URLs

#### Scenario: Mail notification and reply
- **WHEN** the owner's connected mail subscription delivers a verified notification
- **THEN** the box fetches mail through the proxy, records its local cursor and starts the chosen session
- **AND** any reply uses that account's permitted sender/alias through the proxy without exposing OAuth credentials to the box

#### Scenario: Disconnect before outbound send
- **WHEN** a channel is disconnected after receipt but before a reply is sent
- **THEN** the proxy refuses the revoked credential and the box records the failed reply without bypassing egress

#### Scenario: Provider lacks push or send idempotency
- **WHEN** the connected account needs polling or a reply has an ambiguous non-idempotent send result
- **THEN** polling uses existing box-local scheduled sessions, and the reply is recorded for reconciliation instead of blindly resent
- **AND** no shared mail worker, event queue or second scheduler is introduced
