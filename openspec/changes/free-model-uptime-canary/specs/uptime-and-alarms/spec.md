## ADDED Requirements

### Requirement: Dedicated Free Model Canary Uses The Public Served Path
The system SHALL run one hourly UTC served canary turn, backing off to one every three hours after two consecutive failures until a pass, from a droplet systemd timer through exactly `https://tinyassets.io/mcp`, using a dedicated private account/home and its own free OpenRouter connection.

#### Scenario: Active hourly sample
- **WHEN** the connected canary's next eligible slot starts under its current cadence
- **THEN** the host submits one real `converse` request with `message="Reply with the single word: ok"` and `graph_id` equal to its reserved home
- **AND** the request traverses public TLS, edge authentication, ordinary serving admission and the connected provider
- **AND** pass requires terminal reply text `ok` after whitespace stripping, no terminal structured failure, one to three actual model requests within the shared free-tier recovery budget and an identified answering free model
- **AND** localhost, redirected endpoints, stub replies, paid models and other accounts' credentials cannot satisfy the sample

#### Scenario: Duplicate invocation or interrupted host
- **WHEN** another invocation claims an already admitted UTC hour, or the runner restarts after an uncertain send
- **THEN** durable host and server admission prevent a second converse turn for that hour
- **AND** only bounded own-turn status reconciliation is allowed
- **AND** persisted host and server `next_eligible_at` admission enforces three-hour failure backoff across restarts and manual acceptance
- **AND** missed hours are not replayed after downtime
- **AND** the existing external uptime workflow remains responsible for detecting a down droplet

### Requirement: Canary Bearer Is Generated And Confined On The Host
Deployment SHALL generate `TINYASSETS_FREE_MODEL_CANARY_TOKEN` automatically on the droplet when absent, persist it through the atomic installation pattern in a separate root-owned mode-0600 `/etc/tinyassets/free-model-canary.env` read only for the canary runner and daemon, and never require founder secret handling or export a stored copy off-host.

#### Scenario: First deployment and later convergence
- **WHEN** the dedicated env-file assignment is absent or EMPTY
- **THEN** deployment generates at least 32 random bytes under the host-mutation lock and installs them through protected stdin and the `set-once` atomic installation pattern adapted to the dedicated file
- **AND** subsequent deployments reuse the valid value without rotating it
- **AND** an empty assignment is replaced as allowed by `set-once`; duplicate, malformed nonempty or unreadable existing configuration fails closed
- **AND** the token is absent from canonical `/etc/tinyassets/env`, watchdog and backup service environments
- **AND** secret values do not enter command arguments, logs, docs, CI secrets, artifacts, backups or provider/tool subprocess environments
- **AND** the only off-host bearer transmission is the intended authenticated TLS request through the public edge


### Requirement: Canary Ownership And Enrollment Authority Are Distinct
The system SHALL resolve the human enrollment authorizer only through `universe_owner.owner_of` for the identified founder home; no env owner ID or parallel ownership table SHALL establish authority. The reserved account SHALL own the canary home and its connections.

#### Scenario: Resolve and recheck the human authorizer
- **WHEN** enrollment is minted, redeemed or bound
- **THEN** the signed-in human must equal the live owner resolved from the identified founder home
- **AND** missing ownership, deleted owner or deleted founder home fails closed without exchange, deposit or bind
- **AND** the human is recorded only as enrollment authorizer, never the canary home admin, founder-home principal or usage payer

#### Scenario: Reserved home invariants
- **WHEN** provisioning or admitting a canary turn
- **THEN** conversation admin authority, the founder-home binding and `owner_of(canary_home)` for seat/usage charges all identify the RESERVED account
- **AND** inconsistency fails closed without recreating or borrowing another home

### Requirement: Canary Principal Has An Exact Own Home Allowlist
The system SHALL compare the configured canary bearer in constant time and refuse every request outside the exact canary allowlist before dispatch, including indirect tool execution.

#### Scenario: Only allowed requests are dispatched
- **WHEN** the bearer presents a single JSON-RPC 2.0 `tools/call` POST to `/mcp` with exactly `jsonrpc`, bounded string `id`, `method`, and `params`, with params exactly `name` and `arguments`
- **THEN** the only accepted calls are `converse` with exactly the fixed message and reserved `graph_id`, or `get_status` with empty arguments
- **AND** status is server-scoped to that home and projects only readiness and its own synthetic turn receipt/failure, without keys or fleet/other-user data
- **AND** canary authority cannot create a home or bind a connection

#### Scenario: Disallowed actions and malformed requests
- **WHEN** the canary bearer requests any other tool or route, including connect/OAuth, key deposit, approval, writes, reads, runs, deletion, sharing or account administration
- **THEN** the request is refused before dispatch, mutation, data disclosure or provider work
- **AND** this refusal also covers `read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`, unknown/new tools, changed prompts, another home, model overrides, extra/duplicate keys, invalid types, batches, notifications, initialize, tools/list and non-POST requests
- **AND** model-generated or nested tool calls cannot bypass the restriction
- **AND** rejection never falls back to a broader user/wiki-canary identity

#### Scenario: Shared confinement precedes public access
- **WHEN** canary execution reaches the shared universe permission boundary, directly or through persisted, resumed or background execution started by its turn
- **THEN** the trusted canary principal/home/generation restriction is checked before any public-read shortcut
- **AND** another owned public universe remains inaccessible for both reads and effects
- **AND** missing or inconsistent persisted authority context fails closed; HTTP filtering or a request ContextVar alone is insufficient

### Requirement: Owner Click Enrollment Grants Only One Canary Bind
The system SHALL provide an owner-only one-time PKCE enrollment flow that connects the acquired OpenRouter key only to the reserved canary home without granting the human session or runtime bearer general authority over that home.

#### Scenario: Click-only enrollment
- **WHEN** the designated owner signs in and opens the nonsecret `/app/canary/connect` entry URL
- **THEN** GET renders an inert consent page; only a deliberate press of its Authorize enrollment button issues the same-origin authenticated POST that mints the flow for the fixed canary account/home and installed free-only preset
- **AND** page load, JavaScript hydration, prefetch and authenticated prerender neither mint nor replace a flow
- **AND** mint and bind require that the canary has no connection
- **AND** the browser uses the existing connect-screen S256 PKCE redirect/callback flow and the owner clicks Authorize at the provider
- **AND** the server deposits the resulting key privately and completes only the narrowly consented free-model bind through a purpose-specific internal enrollment grant
- **AND** vault credential, connection and outbound grants record the reserved account as owner, with the human audited separately as authorizer; the ordinary ambient-human `connect_http` owner path is not used
- **AND** successful binding produces a nonsecret confirmation in the authorizer's own request rail
- **AND** the founder neither pastes a key nor runs a shell/secret step
- **AND** the future `docs/host-actions.md` row says: 1. Sign up at OpenRouter. 2. Open this link while signed in to TinyAssets. 3. Click Authorize.

#### Scenario: Nonce lifecycle and foreign requests
- **WHEN** a nonce is minted
- **THEN** its digest is bound to the designated owner, canary target, connection generation, preset digest, PKCE challenge, callback origin and a 600-second expiry
- **AND** a valid redemption atomically consumes it before exchange, with at most one winner
- **WHEN** a caller is unauthenticated/non-owner, supplies a reused/expired nonce, switches session, forges origin, fails PKCE or substitutes target/preset/generation
- **THEN** no key exchange, deposit, bind or broader authority is allowed
- **AND** a foreign invalid attempt cannot consume the owner's still-valid nonce

#### Scenario: Concurrent lifecycle change or uncertain completion
- **WHEN** deletion, revocation or another connection changes the target during enrollment
- **THEN** a guarded lifecycle/generation recheck refuses stale credential/binding writes
- **AND** ordinary current-home/founder checks remain unchanged for non-canary flows
- **WHEN** a callback outcome is uncertain or a partial deposit exists
- **THEN** a durable operation receipt permits narrowly scoped server reconciliation without replaying the OAuth exchange or overwriting a newer connection
- **AND** an unrecoverable flow requires a fresh owner-only click ceremony, never pasted credentials

#### Scenario: Account deletion fences enrollment and cleans state
- **WHEN** the reserved account is deleted
- **THEN** its credentials, connections/grants, home bindings, target mapping, nonces and operation/turn receipts are removed
- **AND** stale callbacks, reconciliation and background execution cannot resurrect them
- **WHEN** the human authorizer is deleted before, during or after enrollment
- **THEN** their enrollment mapping references, nonces and authorizer-linked receipts are removed, personal audit identifiers follow account-deletion policy, and pending binds are cancelled with unbound partial deposits/grants cleaned up
- **AND** a completed reserved-owned connection and grants, reserved target mapping without human authority, and reserved-owned turn receipts survive human deletion
- **AND** deletion of either account during exchange/deposit is fenced before bind; no dangling ownership references or replayable receipt remains

#### Scenario: Stolen owner session during eligible enrollment
- **WHEN** an attacker controls the owner session while the canary has no connection
- **THEN** the documented threat model acknowledges that the attacker can initiate PKCE and bind their own OpenRouter account; PKCE does not defend against session compromise
- **AND** the blast radius is the canary home and results from an attacker-controlled free model, not other homes
- **AND** an existing connection prevents enrollment and successful binding is confirmed in the owner's own request rail

### Requirement: Canary Uses The Shared Free Tier Recovery Budget
The system SHALL exercise the SAME bounded recovery as real free-tier users, supplied by PR branch `fix/request-count-per-turn`: at most two rounds plus at most one alternative after failure, with at most three inference requests per message on a free/daily-capped source. Activation SHALL depend on that shared policy being available.

#### Scenario: Normal reply and learning policy
- **WHEN** the fixed prompt obtains a normal reply
- **THEN** it normally costs one inference request
- **AND** learning extraction and deferred learning for capped messages are skipped by the same platform source policy from `fix/request-count-per-turn`, without a canary-specific learning skip

#### Scenario: Recovery after failure
- **WHEN** a provider failure allows recovery under the shared free-tier policy
- **THEN** the same bounded recovery applies to the canary, with at most one eligible free alternative and three requests total across persisted/background continuation
- **AND** no paid/shared/borrowed credential, tool authority or separate canary no-retry policy is introduced
- **AND** a terminal `ok` with authoritative accounting inside that budget can pass after recovery

#### Scenario: Sustained failures reduce sampling frequency
- **WHEN** two consecutive samples fail
- **THEN** the runner alarms and persists a next eligible run three hours later, continuing at three-hour intervals until a pass
- **AND** intervening timer ticks skip without inference or resetting the failure streak
- **AND** the first pass restores hourly admission, without an immediate extra sample
- **AND** planning records that hourly worst case is 24 x 3 = 72 requests, exceeding 50, while sustained-failure backoff is about 30 requests/day (two initial failures plus up to eight three-hour probes, all at three requests)
- **AND** this failure-regime estimate is not claimed as a universal rolling-day bound for intermittent passes

### Requirement: Canary Evidence Is Durable And Truthful
The host runner SHALL atomically persist each sample in `/var/lib/tinyassets-free-model-canary/state.json` and emit its redacted structured record to the journal, following the watchdog state-file convention.

#### Scenario: Served success or failure
- **WHEN** a terminal receipt is available
- **THEN** the record includes UTC slot, correlated request/turn IDs, pass/fail, `turn_failure.code` as `failure_code` when present, failure-record `requests` on failure or actual provider-send count across rounds/alternatives on success, with recorded rounds for reconciliation, monotonic public-turn latency and the model that actually answered
- **AND** an attempted model is distinct from an answering model
- **AND** mismatched counts, wrong output or missing success evidence fail the sample

#### Scenario: No authoritative turn receipt
- **WHEN** transport, edge auth, timeout or receipt decoding prevents authoritative accounting
- **THEN** unknown request count and answering model are null, with a separate `probe_error`
- **AND** no structured failure code is invented from prose and zero requests is recorded only if proven
- **AND** an uncertain turn is never replayed to obtain evidence

### Requirement: Canary Activation Is Dark Until First Connection
The host unit SHALL make no model request and raise no canary outage alarm before first connection activation, while treating missing credentials/home after activation as failures.

#### Scenario: Installed before the owner connects
- **WHEN** enrollment has not completed its first bind
- **THEN** hourly ticks record `skipped/awaiting_connection`, with no converse call, inference request or failure streak
- **AND** the unit does not claim a passing free-model check

#### Scenario: Previously active setup is broken
- **WHEN** the key is revoked, daily quota is exhausted, or the canary home is missing after activation
- **THEN** the sample fails with the served structured code where available and otherwise a separate probe error
- **AND** it does not revert to initial dark behavior, recreate the home or borrow another credential
- **AND** corrupt/unwritable state is an operational alarm and cannot silently erase an active incident or permit duplicate dispatch

### Requirement: Two Consecutive Failures Use The Host Watchdog Alarm Channel
The runner SHALL alarm after two consecutive failed samples through the shared watchdog alarm log and journal, with durable incident deduplication and recovery state.

#### Scenario: Threshold and recovery
- **WHEN** a second consecutive active sample fails
- **THEN** the runner publishes a redacted `FREE_MODEL_CANARY_FAILED` event with incident ID to `/var/log/tinyassets/uptime_alarms.log`, honoring `TINYASSETS_WATCHDOG_ALARM_LOG`, and the journal
- **AND** the append/flush is checked; write failure retains pending delivery, surfaces a redacted stderr/journal error and nonzero service exit, and never marks delivery successful
- **AND** the watchdog helper's swallowed write errors are not accepted as proof of recording
- **AND** ongoing failures update the incident without duplicate opening notifications or daemon restart
- **AND** failed notification delivery is retried independently of model dispatch
- **WHEN** the first subsequent sample passes
- **THEN** the failure streak resets, hourly sampling resumes and one recovery event is emitted
- **AND** a single failure followed by success never opens an incident

#### Scenario: Optional GitHub incident mirror
- **WHEN** an already-installed host `GH_TOKEN` can write issues in the repository
- **THEN** the runner creates or updates one incident-marked issue and closes it on recovery, reconciling uncertain creation by marker
- **WHEN** no usable existing issue credential is available
- **THEN** the local host log/journal alone is the alarm, no paging or off-host delivery is claimed, and issue delivery is recorded unavailable
- **AND** no new GitHub credential, founder secret handling or Actions-token assumption is introduced
