# Agent Model Selection

## Purpose

Owner-scoped model discovery, advisory choices, saved defaults and actual execution identity, using existing connection authority. Native catalogue availability depends on the connected executor; unsupported enumeration remains explicitly unknown.

## Requirements

### Requirement: Main and work-scoped choices are independent
The platform SHALL support owner-authorized main-agent provider/model changes
and independent provider/model choices for an agent, workflow or task. A work
choice SHALL NOT require changing the universe's main serving provider or
rewriting a private workflow. Preferences alone SHALL NOT grant execution access.

#### Scenario: Accepted model access followed by an ordinary workflow
- **WHEN** an owner successfully binds and enables accepted model access
- **THEN** a permitted foreground or background workflow can execute with no explicit pin
- **AND** the runtime validates the chosen member/model rather than treating the assignment as legacy authority

#### Scenario: A direct workflow run reads the owner's saved preference
- **WHEN** an owner with a saved default and ordered fallbacks starts a run directly rather than through a conversation
- **THEN** the run session captures that owner/home-scoped preference document once and uses the same candidate order the conversation path builds
- **AND** a parallel sub-branch of that run inherits the captured policy version rather than re-reading a preference saved mid-run
- **AND** each attempt still admits on current authority, so revocation or an ineligible explicit choice refuses instead of substituting the legacy single serving binding
- **AND** an owner with no saved preference keeps the pre-existing single-serving-binding behaviour unchanged

#### Scenario: Exhausting a saved order is reported as exhaustion
- **WHEN** every model in a run's captured order is exhausted or ineligible
- **THEN** the failure is typed apart from having no bound provider and is actionable by the owner
- **AND** it names each exhausted model and its capacity scope, plus the classified failure class and retry-after for a capacity boundary the run validated itself, chaining that last capacity failure as its cause
- **AND** the provider's raw response is never copied into the run record, and an authentication, source, or revocation failure is never reported as exhaustion
- **AND** a run record that kept only the error string still classifies as exhaustion by its typed message prefix, ahead of the generic timeout, quota, and overload nets, so a model id or capacity class inside the evidence never changes the class
- **AND** the suggested action names the owner's own model-options and model-preference routes and the validated node pin key
- **AND** it never directs anyone to change the universe's main serving provider, which cannot rescue a pinned source

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

#### Scenario: A powered owner confirms another model connection
- **WHEN** the owner confirms an exact model use on another HTTP connection
- **THEN** that source joins the accepted manifest with those models and free-only limits
- **AND** the current serving root and all prior member limits are preserved
- **AND** registration without model-use confirmation grants no model access

#### Scenario: Additional source activation fails
- **WHEN** activation fails after publishing the additional membership
- **THEN** recovery restores prior membership and serving using revision and assignment fences
- **AND** an intervening owner edit is never overwritten
- **AND** the request stays pending with a structured error and actual serving state

#### Scenario: Existing model setup cannot accept another source
- **WHEN** the powered setup lacks an accepted manifest digest or exactly one owned serving agent
- **THEN** the request stays pending with `model_source_acceptance_failed` and actual serving state
- **AND** the owner is told to confirm explicit model access for the existing source and ensure exactly one owned agent is serving before retrying
- **AND** a deposited credential alone is never reported as accepted model access

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

### Requirement: A sign-in refused before launch falls back inside the same turn

When a served turn's chosen source is refused for AUTHENTICATION before anything is launched, the turn SHALL continue to the next model already in the owner's accepted order, in that same turn, and SHALL NOT stop. The refusal SHALL be reported as a sign-in failure rather than a provider outage, so the source is marked for reconnect instead of placed on a provider cooldown.

This is distinct from the Automatic-planning requirement above, which orders a LATER
turn's plan: this one is the turn that is already running. It is also narrower than a
general auth fallback. It applies where nothing was launched, because credential
authorization did not complete, so "no side effect" is a fact about the attempt rather
than an attestation about a run. A refusal reported by a source that DID start a run
keeps its existing held outcome, because an incomplete run is not evidence that nothing
happened.

Fallback SHALL reach only sources already in the owner's accepted order, SHALL exclude
the refused source for the remainder of the turn, and SHALL NOT retry it. Where the
refused source is the owner's only source, the turn SHALL fail honestly rather than
substitute a source the owner has not accepted; the reconnect card is the recovery.

#### Scenario: the founder's spent sign-in is answered by the next model
- **WHEN** the chosen source's stored sign-in is refused before launch
- **AND** the owner has another accepted model
- **THEN** that model answers in the same turn and the turn completes
- **AND** the turn is neither abandoned nor left held

#### Scenario: the refusal is not a provider outage
- **WHEN** the same refusal is classified
- **THEN** it carries the authentication failure class and no provider cooldown is
  applied, so the owner's next turn is not additionally blocked by a cooldown for a
  source whose credential is the problem

#### Scenario: fallback stays inside what the owner accepted
- **WHEN** the turn advances after such a refusal
- **THEN** the source it advances to is one already in the owner's accepted order, the
  refused source is excluded for the rest of that turn, and no saved preference changes

#### Scenario: the only source is refused
- **WHEN** the refused source is the owner's only accepted source
- **THEN** the turn fails with the authentication failure rather than using an
  unaccepted source

### Requirement: An unproven capacity refusal is narrowed to the model that failed, for every account

A source contract reports a capacity refusal's scope as `model`, `account` or `unknown`, and that reported scope SHALL remain the evidence unchanged. When the scope is `unknown` and the failure class is a transient window (`provider_rate_limited`, `provider_overloaded`), a served agent turn SHALL narrow the resulting exhaustion to the MODEL that failed rather than the whole account, and the router SHALL NOT apply a source-wide cooldown to that attempt.

This decision SHALL read only what the SOURCE reported. It SHALL NOT read the
owner's accepted cost ceilings, their plan or any other account attribute: the
same refusal means the same thing for every account (founder, 2026-09-25). A
prior version required all-confirmed-zero ceilings, which made a paid source's
identical refusal a cooled dead end while its free neighbour continued to a
sibling.

Spend stays bounded by the mechanisms that bound every attempt, not by this
policy: the owner's accepted ceilings constrain EVERY request body, so a narrowed
replacement costs what the first attempt was already authorized to cost, and a
retry policy SHALL NOT raise a ceiling, admit a model or widen a grant.

The narrowing SHALL be bounded at three per turn, SHALL take its replacement from
the SAME advisory order under the SAME ceilings, and SHALL refuse a replacement on
any other connection — a narrowed exhaustion is a guess about one source's window,
never evidence that a different connection sharing its scope is healthy. Exhausted
credit (`provider_credit_exhausted`) and an `account` scope the source actually
reported SHALL keep the conservative account exclusion and its cooldown, for every
account alike — both are the source's own report, one about money and one about
breadth. A round whose executor is a native subscription SHALL NOT narrow at all:
its account IS the source, which is a fact about the source rather than about the
owner's plan. The diagnostics of every attempt the turn replaced SHALL be carried
onto the failure that finally escapes.

#### Scenario: A free universe's first message meets a busy model
- **WHEN** a served turn's selected free model is refused with an unknown-scope rate limit and nothing ran
- **THEN** the turn tries the next eligible model of the SAME grant at the SAME zero ceilings and answers
- **AND** no source-wide cooldown is applied that would have skipped that sibling

#### Scenario: A source that can spend gets the same reading
- **WHEN** the same unknown-scope refusal arrives for a source with a nonzero accepted ceiling
- **THEN** the turn narrows and the source is not cooled, exactly as for a zero-ceiling source
- **AND** the replacement attempt is priced by that source's own accepted ceilings

#### Scenario: Exhausted credit is never narrowed
- **WHEN** a source reports exhausted credit
- **THEN** the account exclusion and cooldown stand regardless of the accepted ceilings

#### Scenario: A native subscription executor is never narrowed
- **WHEN** an unknown-scope transient refusal arrives on a native subscription round
- **THEN** the account exclusion stands, because that account is the source itself

#### Scenario: A narrowed guess cannot cross into another connection
- **WHEN** the only remaining candidate after a narrowed exhaustion belongs to a different connection
- **THEN** the turn stops rather than treating that connection as independently healthy

#### Scenario: Repeated refusals stop rather than sweep the catalogue
- **WHEN** every model tried is refused the same way
- **THEN** at most three narrowed retries occur, and the reported failure carries one attempt record per model tried

The withheld cooldown buys exactly one thing: another model on the same grant.
Whoever concludes that no sibling attempt will follow -- the narrowed budget is
spent, the order has no sibling left on that source, or the turn moves to a
different connection -- SHALL cool the source after the fact, honouring the
source's own `Retry-After` when it supplied one. A refusal whose stated window
is longer than a whole turn may live SHALL keep its cooldown immediately, since
waiting is then the answer and no sibling attempt can outlast it.

#### Scenario: A daily cap is paid for once, not every turn
- **WHEN** a source refuses every eligible model with an unknown-scope rate limit
- **THEN** the first turn spends its bounded budget discovering that and the source is cooled
- **AND** the next turn is answered off that cooldown with its attempt skipped, rather than sending the same requests again

#### Scenario: A source that answered is not cooled
- **WHEN** a narrowed sibling attempt succeeds
- **THEN** the source keeps no cooldown from the refusal that preceded it

#### Scenario: A stated window longer than the turn is waited out
- **WHEN** a refusal names a retry-after longer than the turn's absolute cap
- **THEN** the source is cooled for that window and no sibling attempt is made
- **AND** this is decided at both call sites of the rule — the router's per-attempt
  handler and the coordinator's after-the-fact cooling — not only inside the predicate

#### Scenario: A source cannot retire itself with a Retry-After
- **WHEN** a refusal names a window longer than a bounded ceiling (one day)
- **THEN** the applied cooldown is clamped to that ceiling, so no response header can
  make an owner's own source unusable indefinitely

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

#### Scenario: A native catalogue refresh is pending or fails
- **WHEN** an accepted source has a previous owned catalogue and its background refresh is pending or fails
- **THEN** its previously enumerated, non-hidden choices remain visible and selectable as advisory choices under current scope and custody
- **AND** the app reports the refresh diagnostic and preserves actual source timestamps without turning catalogue age into a new consent requirement
- **AND** a fresh picker read remains usable even when native catalogue timestamps have expired; HTTP discovery expiry still gates its choices
- **AND** a display-only plan cannot authorize activation, while actual native execution still requires fresh metadata and current authority
- **AND** history alone cannot restore a withdrawn model or transfer a choice between providers

#### Scenario: Native metadata uses a launcher with child processes
- **WHEN** a registered native metadata executor launches inherited-pipe children on POSIX
- **THEN** the transport isolates and terminates its invocation's process group on success, refusal, timeout or cancellation, including after launcher exit
- **AND** cleanup observes inherited-pipe closure within a bounded interval rather than discarding a complete catalogue at the discovery timeout
- **AND** cancellation propagates, process output is not relayed and existing custody checks remain unchanged

#### Scenario: Native discovery reaches the public picker
- **WHEN** native metadata discovery succeeds for an accepted source
- **THEN** the public model-options read lists its provider default and eligible discovered models without assuming HTTP-only metadata fields
- **AND** exact current custody is rechecked before display without opening another credential-store connection inside the admission transaction; source timestamps retain their actual age
- **AND** native revocation removes that source without hiding independently authorized HTTP choices

#### Scenario: Discovery fails
- **WHEN** discovery cannot refresh
- **THEN** cached choices are labelled stale and the app does not claim current availability

### Requirement: Current choice and durable preference are distinct
The app SHALL allow switching the interactive agent, saving a default and ordering accepted fallbacks independently of connection identity and actual execution receipts.

#### Scenario: Concurrent universe choices
- **WHEN** two owners choose different models on the same provider family
- **THEN** each next authorized inference uses its own choice without process-global preference leakage

#### Scenario: Conversation capacity recovery preserves the saved preference
- **WHEN** the chosen conversation source is cooling down, rate-limited or exhausted and another source is accepted for the same owner
- **THEN** a replay-safe capacity failure advances to an eligible accepted source, preferring subscriptions to HTTP models and retaining automatic quality ranking
- **AND** an empty saved fallback sequence is a preference, not an explicit only-model restriction, and the stored selection remains unchanged
- **AND** every attempt revalidates that owner's source, model and cost authority; a connected credential without accepted access is not a fallback grant
- **AND** the final reply contains one notice naming the answering source and the original source's retry time, or says that no reset time was reported
- **AND** unknown or committed native effects refuse replay, and workflow model pins retain their existing strict semantics

#### Scenario: A fallback edit is saved when it is made
- **WHEN** the owner adds, moves or removes a fallback in the model dialog
- **THEN** that edit is saved through the same preference write the quick-pick uses,
  with the expected generation, and the dialog stays open and editable on the
  generation and policy the write returned
- **AND** a conflicting or unconfirmed save clears the order on screen and says it is
  not saved, so an order the server does not hold is never left displayed

#### Scenario: A displayed fallback the server does not hold is a broken fallback
- **WHEN** the dialog shows a fallback model that was never written
- **THEN** a sign-in failure on the chosen model has nothing to fall back to, which is
  why the displayed order and the stored order must not diverge

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

### Requirement: Native metadata subprocesses are confined to their launch snapshot
Native metadata transport SHALL use the shared confined owned-process launcher with the explicit owning command center, only its exact ephemeral credential snapshot and private temporary runtime storage. It SHALL NOT inherit an ambient engine route, expose sibling command centers or snapshots, or fall back to an unconfined process. Existing custody revalidation, fixed registered protocol, output bounds and cancellation semantics SHALL remain enforced.

#### Scenario: A metadata executable attempts a foreign read
- **WHEN** an owned metadata launch attempts to read another command center, another snapshot, platform source or a host process environment
- **THEN** the OS jail denies those reads while allowing its own synthetic credential and bounded metadata response
- **AND** the result grants no inference or effect authority

#### Scenario: Confinement cannot be established
- **WHEN** the owning directory or snapshot is absent, foreign or redirected, or the existing OS jail is unavailable
- **THEN** discovery fails before the provider is started with sanitized unavailable prose
- **AND** no host launch, new permission or persistent grant is substituted

#### Scenario: A metadata launcher leaves inherited-pipe children
- **WHEN** discovery succeeds, refuses, times out or is cancelled after starting an owned process family
- **THEN** the existing owned-family teardown terminates descendants even after launcher exit
- **AND** pipe cleanup is bounded and cancellation propagates

### Requirement: Workflow pins and refreshed native choices remain exact

A workflow step's explicit model policy SHALL reach the owner-bound policy caller.
A missing policy-capable caller SHALL refuse, naming the model and source, before
any ordinary or mock provider response. An automatic workflow order SHALL NOT
append unrequested alternatives to an exact model pin without an explicit fallback chain.
A provider-only pin SHALL retain eligible same-source models in the captured order.

#### Scenario: Cross-family workflow review
- **WHEN** two steps select different accepted provider families and model IDs
- **THEN** each invocation carries its exact selected model through its owner's authority
- **AND** exhausting a pinned model without an explicit fallback fails rather than substitutes

#### Scenario: Provider-only workflow choice
- **WHEN** a step selects a provider without an exact model ID
- **THEN** all eligible same-source models in the captured order remain candidates within the existing allowance
- **AND** exhausting that source fails without substituting another source

#### Scenario: A completed native refresh outlives the engine process
- **WHEN** another engine process reads a completed native catalogue refresh
- **THEN** it can display the same selectable models under current owner custody
- **AND** persistent advisory rows carry only model metadata, timestamps and a custody digest
- **AND** changed owner, provider or revoked custody cannot reuse the old catalogue
- **AND** a late older refresh cannot overwrite newer observations
- **AND** actual execution still requires fresh model discovery and authority checks
