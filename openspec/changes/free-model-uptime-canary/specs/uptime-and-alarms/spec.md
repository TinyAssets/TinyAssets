## ADDED Requirements

### Requirement: Dedicated Free Model Canary Uses The Public Served Path
The system SHALL run one hourly UTC served canary turn from a droplet systemd timer through exactly `https://tinyassets.io/mcp`, using a dedicated private account/home and its own free OpenRouter connection.

#### Scenario: Active hourly sample
- **WHEN** the connected canary's next hourly slot starts
- **THEN** the host submits one real `converse` request with `message="Reply with the single word: ok"` and `graph_id` equal to its reserved home
- **AND** the request traverses public TLS, edge authentication, ordinary serving admission and the connected provider
- **AND** pass requires terminal reply text `ok` after whitespace stripping, no structured failure, one actual model request and an identified answering free model
- **AND** localhost, redirected endpoints, stub replies, paid models and other accounts' credentials cannot satisfy the sample

#### Scenario: Duplicate invocation or interrupted host
- **WHEN** another invocation claims an already admitted UTC hour, or the runner restarts after an uncertain send
- **THEN** durable host and server admission prevent a second converse turn for that hour
- **AND** only bounded own-turn status reconciliation is allowed
- **AND** missed hours are not replayed after downtime
- **AND** the existing external uptime workflow remains responsible for detecting a down droplet

### Requirement: Canary Bearer Is Generated And Confined On The Host
Deployment SHALL generate `TINYASSETS_FREE_MODEL_CANARY_TOKEN` automatically on the droplet when absent, persist it through the atomic host-env installer, and never require founder secret handling or export a stored copy off-host.

#### Scenario: First deployment and later convergence
- **WHEN** the canonical host-env assignment is absent
- **THEN** deployment generates at least 32 random bytes under the host-mutation lock and installs them through protected stdin and `set-once`
- **AND** subsequent deployments reuse the valid value without rotating it
- **AND** empty, duplicate, malformed or unreadable existing configuration fails closed
- **AND** secret values do not enter command arguments, logs, docs, CI secrets, artifacts, backups or provider/tool subprocess environments
- **AND** the only off-host bearer transmission is the intended authenticated TLS request through the public edge

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

### Requirement: Owner Click Enrollment Grants Only One Canary Bind
The system SHALL provide an owner-only one-time PKCE enrollment flow that connects the acquired OpenRouter key only to the reserved canary home without granting the human session or runtime bearer general authority over that home.

#### Scenario: Click-only enrollment
- **WHEN** the designated owner signs in and opens the nonsecret `/app/canary/connect` entry URL
- **THEN** GET renders an inert consent page and a same-origin authenticated POST mints the one-time flow for the fixed canary account/home and installed free-only preset
- **AND** the browser uses the existing connect-screen S256 PKCE redirect/callback flow and the owner clicks Authorize at the provider
- **AND** the server deposits the resulting key privately and completes only the narrowly consented free-model bind through a purpose-specific internal enrollment grant
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

### Requirement: Canary Turn Consumes At Most One Inference Request
The system SHALL enforce a principal-scoped one-request inference budget at the provider-send boundary and exclude canary turns from learning extraction and deferred learning debt.

#### Scenario: Reply would otherwise trigger learning
- **WHEN** the canary obtains a reply without recording a brain lesson
- **THEN** `_LEARNING_SYSTEM` extraction and later debt repayment do not run for that turn
- **AND** no second request is made even though ordinary founder turns can invoke `extract_learning`
- **AND** ordinary users' learning behavior is unchanged

#### Scenario: Retry, fallback or tool continuation would dispatch
- **WHEN** the canary turn meets a provider failure, tool call, malformed reply, compaction need or recovery path
- **THEN** its trusted persisted policy prevents retries, fallback models, tool execution, repair inference and any second provider request
- **AND** the failure is reported honestly, without paid or shared-account fallback
- **AND** steady-state hourly operation consumes at most 24 inference requests per UTC day, including any manual acceptance occupying that hour's slot

### Requirement: Canary Evidence Is Durable And Truthful
The host runner SHALL atomically persist each sample in `/var/lib/tinyassets-free-model-canary/state.json` and emit its redacted structured record to the journal, following the watchdog state-file convention.

#### Scenario: Served success or failure
- **WHEN** a terminal receipt is available
- **THEN** the record includes UTC slot, correlated request/turn IDs, pass/fail, `turn_failure.code` as `failure_code` when present, failure-record `requests` on failure or round count on success, monotonic public-turn latency and the model that actually answered
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
The runner SHALL alarm after two consecutive failed hourly samples through the shared watchdog alarm log and journal, with durable incident deduplication and recovery state.

#### Scenario: Threshold and recovery
- **WHEN** a second consecutive active sample fails
- **THEN** the runner publishes a redacted `FREE_MODEL_CANARY_FAILED` event with incident ID to `/var/log/tinyassets/uptime_alarms.log`, honoring `TINYASSETS_WATCHDOG_ALARM_LOG`, and the journal
- **AND** ongoing failures update the incident without duplicate opening notifications or daemon restart
- **AND** failed notification delivery is retried independently of model dispatch
- **WHEN** the first subsequent sample passes
- **THEN** the failure streak resets and one recovery event is emitted
- **AND** a single failure followed by success never opens an incident

#### Scenario: Optional GitHub incident mirror
- **WHEN** an already-installed host `GH_TOKEN` can write issues in the repository
- **THEN** the runner creates or updates one incident-marked issue and closes it on recovery, reconciling uncertain creation by marker
- **WHEN** no usable existing issue credential is available
- **THEN** the host alarm channel alone is the alarm and issue delivery is recorded unavailable
- **AND** no new GitHub credential, founder secret handling or Actions-token assumption is introduced
