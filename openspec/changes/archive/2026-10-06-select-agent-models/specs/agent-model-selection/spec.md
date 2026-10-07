## ADDED Requirements

### Requirement: Unconnected sign-in continues through model authorization
After authenticating and resolving the owner's own universe, the app SHALL automatically continue genuinely empty LLM setup through the configured OpenRouter hosted signup/sign-in and authorization flow. Provider consent SHALL remain a user action. First free-only setup SHALL need neither a running agent nor manual API-key creation/paste, using the existing owner-confirmed authority boundary.

#### Scenario: New provider account
- **WHEN** a signed-in owner has no LLM connection or OpenRouter account
- **THEN** setup reaches provider signup/consent, returns to the same owner's universe and offers eligible free-model execution after the required consent
- **AND** no other user's credentials or paid fallback is used

#### Scenario: Failed or unreadable existing setup
- **WHEN** existing setup is expired, revoked, pending, failed or unreadable
- **THEN** it is recovery or unavailable rather than empty, and automatic setup cannot overwrite it

#### Scenario: Cancel or refresh
- **WHEN** the owner cancels, refreshes, returns twice or changes home
- **THEN** setup is resumable without redirect loops, duplicate grants or replacing newer state

#### Scenario: Initial connection is serving
- **WHEN** authorized setup powers the universe
- **THEN** a nonblocking generic Connect another LLM request remains available

### Requirement: Main and work-scoped choices are independent
The platform SHALL support owner-authorized main-agent provider/model changes
and independent provider/model choices for an agent, workflow or task. A work
choice SHALL NOT require changing the universe's main serving provider or
rewriting a private workflow. Preferences alone SHALL NOT grant execution access.

#### Scenario: Accepted model access followed by an ordinary workflow
- **WHEN** an owner successfully binds and enables accepted model access
- **THEN** a permitted foreground or background workflow can execute with no explicit pin
- **AND** the runtime validates the chosen member/model rather than treating the assignment as legacy authority

#### Scenario: A task chooses a different authorized source
- **WHEN** a task selects a supported provider/model within current accepted authority that differs from the main agent
- **THEN** its invocation uses that exact choice without changing the main agent or the task definition
- **AND** actual execution identity and settlement identify the source that ran

#### Scenario: Enabling model access preserves background polling
- **WHEN** a universe successfully enables accepted model access with an eligible source
- **THEN** it remains visible to the generic background coordinator for its authorized work
- **AND** inventory does not silently drop it through legacy single-provider validation
- **AND** actual launches still independently validate current authority and lifecycle

#### Scenario: Multiple sources share one work allowance
- **WHEN** different nodes or fallback attempts use different accepted sources
- **THEN** they remain constrained by the same work-level aggregate allowance and current per-source constraints
- **AND** concurrent attempts cannot multiply the budget or replay completed effects

#### Scenario: Aggregate authority is not an anchor credential
- **WHEN** a manifest-authorized workflow records its work budget
- **THEN** its version4 receipt binds the assignment manifest and immutable work subject without a provider binding or credential
- **AND** each reserved invocation binds its own selected member, model, custody and permitted cost
- **AND** unrelated anchor-custody revocation does not invalidate a still-authorized member

#### Scenario: Legacy authority records stay strict
- **WHEN** existing version1–3 receipts are read or written
- **THEN** their prior exact representation and provider-bound semantics are preserved
- **AND** new manifest receipts cannot disguise provider or credential fields as aggregate authority

#### Scenario: Main-provider change through ordinary controls
- **WHEN** the authorized user requests a main-provider change through the app agent
- **THEN** existing scoped operations and necessary owner confirmation perform and report the actual change
- **AND** no developer-console action or generic approval acknowledgement is presented as an executed repair

#### Scenario: A selected source cannot currently execute
- **WHEN** the choice is outside model/cost authority, revoked, stale or unsupported by its executor
- **THEN** execution refuses with the actual category before provider launch
- **AND** it neither silently changes the pin nor requests unrelated authentication as the repair

#### Scenario: Owner-declared native choice
- **WHEN** an owner selects a nonempty native model ID already accepted in explicit model scope
- **THEN** chat and workflow invocations pass that exact request-local ID to the native executor
- **AND** process-global model overrides cannot replace the selection
- **AND** the catalogue labels it owner-declared with availability unverified, not executor-enumerated
- **AND** unknown answering-model telemetry remains unknown

#### Scenario: Native default and injected preference
- **WHEN** native authority selects its provider default or no native selection is authorized
- **THEN** a caller-supplied native model field cannot change that authority
- **AND** an authorized default omits the model argument rather than activating a host preference

### Requirement: Connection-authored discovery contracts
The existing provider-capability action SHALL allow a versioned bounded data
contract for an unfamiliar connected model source, without provider-specific
platform callbacks or model-release lists. The exact contract, endpoint and
authority context SHALL be revalidated before model admission. Legacy descriptors
SHALL keep their prior interpretation and serialization.

#### Scenario: Owner configures a custom source contract
- **WHEN** an authorized owner configures a validated contract with optional preview
- **THEN** its successfully fetched catalogue may enter selection under existing accepted model, grant, executor and cost limits
- **AND** the UI distinguishes owner-configured source semantics from verified account availability
- **AND** configuration creates no inference or spending permission and cannot satisfy an independently required privacy restriction

#### Scenario: Catalogue invents trust or execution support
- **WHEN** remote data claims filtered availability, account identity, unmetered pricing or executor support
- **THEN** those claims cannot create trusted authority or an installed executor
- **AND** absent authenticated source configuration leaves the source unverified

#### Scenario: Unsupported charging or changed contract
- **WHEN** unknown charges, unenforceable ceilings, stale evidence or a changed contract invalidate a candidate
- **THEN** no inference uses the stale candidate and free-only routing cannot become paid

#### Scenario: Contract edits do not manufacture independent capacity
- **WHEN** separate custom-source connections lack independent account evidence
- **THEN** they share a conservative server-derived capacity scope
- **AND** changing a descriptor, host, key or model name cannot establish independent capacity

#### Scenario: Distinct unfamiliar catalogue shape
- **WHEN** a supported custom contract describes a new source schema and model ID
- **THEN** the existing picker and authorized agent route consume the normalized result without a platform edit
- **AND** unsupported wire protocols remain explicitly unsupported rather than fabricated success

### Requirement: Model access uses the existing owner binding action
The authenticated custom_agents bind_serving_provider action SHALL accept optional
model_access via strict ModelAccess validation and existing assignment publication.
Omission SHALL preserve the legacy provider-only payload. Saving preferences SHALL
NOT publish or widen assignments, grants or permitted spending.

#### Scenario: Owner opts into discovered models
- **WHEN** the owner binds authorized sources with discovered scope and accepted price limits
- **THEN** the current assignment records membership through existing authority
- **AND** broader discovery grants or paid allowances require explicit authorization

#### Scenario: Onboarding without opt-in
- **WHEN** the deposit flow has no explicit model-access declaration
- **THEN** it preserves legacy binding and working native serving behavior

#### Scenario: Mixed-source automatic mode
- **WHEN** a native default and HTTP models are eligible
- **AND** no unresolved scoped authentication-failure hint demotes the native source
- **THEN** automatic selection uses the native default through its real executor
- **AND** an HTTP-only catalog cannot silently remove that preference

#### Scenario: Saved automatic preference on an existing legacy binding
- **WHEN** an owner saves automatic mode and has a legacy provider-only assignment
- **THEN** a turn without a current override retains that provider's own default
- **AND** the saved policy and generation remain unchanged without granting model access
- **AND** current overrides or explicit saved choices still require accepted model authority

#### Scenario: Serving readiness reports its selected provider
- **WHEN** a manifest-backed agent is enabled successfully
- **THEN** the response provider identifies the plan's first eligible candidate
- **AND** the assignment anchor is not rewritten to impersonate the selected candidate

#### Scenario: Legacy readiness cannot execute the saved choice
- **WHEN** the current owned home has an explicit saved choice but no accepted model assignment
- **THEN** enabling serving refuses without modifying its binding or saved preference
- **AND** absent or saved automatic preferences preserve legacy provider-default readiness

#### Scenario: Many inactive agents do not hide serving authority
- **WHEN** more than100 newer inactive bindings exist in the universe
- **THEN** serving selection still considers every exact owner-serving match
- **AND** zero or multiple matches cannot be mistaken for one current binding

#### Scenario: Another owned universe retains legacy execution
- **WHEN** the owner converses with a non-home universe without an override
- **THEN** home-only preferences neither block nor alter its existing binding

### Requirement: Unresolved source authentication failures influence new Automatic plans

Automatic planning SHALL move a source with an unresolved scoped authentication-failure
hint after other already-eligible sources. The hint SHALL NOT grant access, change
spending permission, modify saved preferences, or authorize replay of a failed
request. Explicit current and saved choices SHALL retain their exact ordering.
The initial implementation SHALL use bounded process-local advisory memory,
scoped by resolved base, owner, universe, provider and credential-reference identity,
generation and digest. Elapsed time alone SHALL NOT clear a hint. The store SHALL
retain no more than 4096 entries and SHALL store no credentials or raw errors.
Success under the exact custody SHALL clear its hint. New credential custody SHALL
be eligible without inheriting the previous custody's hint. Recording success or
failure SHALL discard lower-generation hints only for the same resolved base,
owner, universe, provider and credential-reference identity.
Restart or eviction MAY lose advisory health; it SHALL NOT change authority.

#### Scenario: A new Automatic message follows an authentication failure
- **WHEN** a served source reports the typed authentication-failure signal
- **AND** another source is independently eligible for the next Automatic plan
- **THEN** the next plan prefers the other source without changing the owner's preferences
- **AND** the failed turn is not replayed and retains truthful uncertainty about effects

#### Scenario: Explicit choices remain exact
- **WHEN** the owner explicitly chooses a failed source or has an explicit saved order
- **THEN** advisory source health does not silently substitute or reorder that choice
- **AND** execution still validates current authority before launch

#### Scenario: Time alone does not establish recovery
- **WHEN** seven minutes or a day pass after an authentication failure without success or custody change
- **AND** the process-local hint has not been evicted
- **THEN** a new Automatic plan still prefers another independently eligible source
- **AND** the failed request is not replayed

#### Scenario: Success or new credential custody permits recovery
- **WHEN** a source succeeds under the same exact custody scope
- **THEN** its hint clears
- **AND** renewed credential custody is eligible before any successful model call
- **AND** one owner's success or failure cannot clear or create another owner's hint
- **AND** a late old-generation success cannot clear a newer-generation failure

#### Scenario: All eligible sources have unresolved failure hints
- **WHEN** every eligible source has an unresolved hint
- **THEN** Automatic retains candidates in their relative order rather than manufacturing an unavailable replacement
- **AND** ordinary execution and error reporting still apply

#### Scenario: The picker explains unresolved source trouble
- **WHEN** a scoped hint is current
- **THEN** the picker displays a non-blocking reconnect warning
- **AND** it does not disable manual selection, claim successful sign-in or expose raw provider errors

### Requirement: Connection-scoped model choices
The app SHALL expose model choices from the universe owner's authorized connections with freshness and capability information, without a compiled model-release list.

#### Scenario: A newly released model appears
- **WHEN** refreshed connection discovery returns a new model
- **THEN** the owner can see and select it without a platform release, subject to current authority and supported capabilities

#### Scenario: Native discovered choice is checked at execution
- **WHEN** an accepted native discovered scope selects a nonempty model ID
- **THEN** fresh owned metadata must contain that ID with the required capability
- **AND** chat, foreground and background admission recheck the exact current member and custody before launch
- **AND** workflow evidence distinguishes versioned enumeration facts from an owner-declared choice and actual answering-model telemetry

#### Scenario: Native enumeration is unavailable
- **WHEN** a native executor cannot enumerate its current model catalogue
- **THEN** the provider-default choice remains available if independently authorized
- **AND** the app labels the enumeration gap rather than claiming a complete model list
- **AND** a nonempty discovered choice is refused without silently substituting the default

#### Scenario: Native metadata uses a launcher with child processes
- **WHEN** a registered native metadata executor launches inherited-pipe children on POSIX
- **THEN** the transport isolates and terminates its invocation's process group on success, refusal, timeout or cancellation, including after launcher exit
- **AND** cleanup observes inherited-pipe closure within a bounded interval rather than discarding a complete catalogue at the discovery timeout
- **AND** cancellation propagates, process output is not relayed and existing custody checks remain unchanged

#### Scenario: Native discovery reaches the public picker
- **WHEN** native metadata discovery succeeds for an accepted source
- **THEN** the public model-options read lists its provider default and eligible discovered models without assuming HTTP-only metadata fields
- **AND** freshness and exact custody are rechecked before display without opening another credential-store connection inside the admission transaction
- **AND** native revocation removes that source without hiding independently authorized HTTP choices

#### Scenario: Discovery fails
- **WHEN** discovery cannot refresh
- **THEN** cached choices are labelled stale and the app does not claim current availability

### Requirement: Current choice and durable preference are distinct
The app SHALL allow switching the interactive agent, saving a default and ordering accepted fallbacks independently of connection identity and actual execution receipts.

#### Scenario: Concurrent universe choices
- **WHEN** two owners choose different models on the same provider family
- **THEN** each next authorized inference uses its own choice without process-global preference leakage

#### Scenario: No accepted fallback
- **WHEN** the owner saves a model with an empty fallback sequence
- **THEN** exhaustion leaves that selection unchanged and does not substitute another source

### Requirement: Actual execution is visible and actionable
The typed-chat interface SHALL show a clickable active provider/model control, distinguish preference from actual execution, and remain usable without a working LLM.

#### Scenario: Applying a model choice is an ordinary visible action
- **WHEN** the owner opens the prominent model control
- **THEN** the primary selector contains usable choices plus any unavailable currently selected choice
- **AND** every unavailable model and reason remains in the full inventory
- **AND** using a tab-local choice closes the dialog without changing the saved default
- **AND** confirmed default saving clears the tab override so the next message uses the saved choice
- **AND** failed or ambiguous saves do not clear that override

#### Scenario: Reopening model selection with fresh evidence
- **WHEN** the owner reopens the picker before its owner-scoped catalogue expires
- **THEN** the picker reuses that snapshot without an unnecessary discovery wait
- **AND** expired or failed refresh evidence still prevents application
- **AND** execution independently rechecks authority; UI evidence never grants access

#### Scenario: Claude browser code is not a subscription token
- **WHEN** the owner submits a browser authorization code or malformed token text to the dedicated Claude subscription deposit
- **THEN** the canonical handler rejects it before any credential or ownership mutation
- **AND** the error explains that the browser code goes back into the setup terminal and the terminal's final token goes into the app
- **AND** neither credential bytes nor digests appear in errors or logs
- **AND** a successful shape check and serving bind report credential saved, not verified provider authentication

#### Scenario: A router answers with another model
- **WHEN** a response reports a model different from the requested alias
- **THEN** the answering-model display uses the reported model without rewriting the saved default

#### Scenario: Model metadata is unavailable
- **WHEN** a response does not report a usable model identifier
- **THEN** the answer remains usable and the display marks the actual model unknown instead of presenting the requested alias as verified

#### Scenario: Native root-answer model observation
- **WHEN** a successful Claude stream has explicit root assistant frames reporting a usable model
- **AND** their final message text matches the returned answer, including split blocks with one message ID
- **THEN** the answer receipt records that reported model, independently of the requested alias
- **AND** child-agent frames, initial configuration and aggregate usage cannot replace that evidence
- **AND** absent, conflicting, malformed, synthetic or mismatched evidence remains unknown without discarding the answer
- **AND** metadata-only observation cannot extend the provider idle watchdog
- **AND** native protocols without answer-owned evidence continue to report unknown

#### Scenario: Answering receipt survives history reload
- **WHEN** a reply has a valid server-observed provider/model receipt and its history write succeeds
- **THEN** the receipt is stored atomically on that reply's row, not the founder row
- **AND** the own-principal history projection and reloaded app restore that exact receipt
- **AND** the latest restored answer controls the answering-model display without changing preferences

#### Scenario: Missing or corrupt historical receipt
- **WHEN** an old database lacks the receipt column or a reply's optional receipt is missing or invalid
- **THEN** read-only history preserves the transcript without migrations or invented telemetry
- **AND** the reply displays unknown rather than inheriting another answer's receipt
- **AND** receipt fields never enter prompt history, permissions or model routing

#### Scenario: Optional persistence fails
- **WHEN** storing conversation history fails after a successful model reply
- **THEN** the immediate answer and its observed receipt remain available without repeating inference
- **AND** an unavailable optional receipt migration permits otherwise-valid text-only storage
