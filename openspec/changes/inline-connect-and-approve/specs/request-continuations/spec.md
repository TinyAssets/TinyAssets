## ADDED Requirements

### Requirement: Every consent answer requires an interactive owner

Every consent answer SHALL require protected owner-session proof. Consent actions are `publish`, `install`, `connect`, `connect_http`, `extend_http`, `rotate_http`, `remove_http`, `grant_workspace_consent`, `bind_model_access` and `grant_patch_intake`. Bearer/MCP/chatbot/agent callers SHALL receive `interactive_approval_required` directing them to the approval sheet in the app, without resolving or executing the request. Ordinary non-consent answers SHALL retain their existing behavior.

#### Scenario: Alternate answer attempts
- **WHEN** a bearer submits an item answer, retry, Deny, Clear, forged session, or wider scope to a consent request through an API or write_graph
- **THEN** the shared executor refuses before any request or authority mutation
- **AND** an immutable publish/install pin remains consent even if the row claims to be an ordinary question

#### Scenario: Owner recovery and OAuth completion
- **WHEN** the owner clears or denies a consent request and later raises a replacement
- **THEN** only protected owner-session answers can resolve that replacement, even after unmute
- **AND** connection OAuth completion cannot exchange or deposit tokens as a pending answer without protected matching-owner proof

### Requirement: Approval executes the exact owner-confirmed action
The request system SHALL bind each ask-first approval to the protected normalized action, expiry and trusted initiating subject in design.md with a server-computed SHA-256 and revision. Approve SHALL execute server-side under current ordinary enforcement with no relayed yes or agent retry. Only owner rules SHALL choose ask_first; defaults SHALL remain editable and cross-user isolation SHALL remain the fixed policy floor.

#### Scenario: Approve with the current draft
- **WHEN** the interactive owner approves a matching revision/hash with a valid session-bound token under unchanged authority and an active task
- **THEN** the server reserves one execution, runs the bound arguments through the ordinary effector and publishes the actual result
- **AND** custody, the initiating agent's rules, consent and connection grants apply

#### Scenario: Edit or stale approval
- **WHEN** a draft is edited, authority changes or an old revision/hash is approved
- **THEN** validation and a fresh preview/token are required; old confirmation cannot authorize the changed action

#### Scenario: Duplicate click or ambiguous external outcome
- **WHEN** an owner repeats a committed approval or a worker crashes after a possible effect
- **THEN** the existing effect intent determines the result; no second reservation or grant is created
- **AND** unknown outcomes remain unresolved until reconciled, with no blind retry

#### Scenario: Policy does not ask first
- **WHEN** the initiating agent's owner rules say do, hand_off or do_if_preapproved
- **THEN** that behavior remains authoritative, without a platform-mandated ask-first card

### Requirement: Approvals authenticate an interactive owner decision
Only protected first-party web/native owner surfaces SHALL approve using the distinct interactive session and single-use token contract in design.md. Bearer credentials, action hashes, claimed origins and agent identities SHALL NOT confer approval authority. Token minting, effect-dispatching retries and scope-rule writes SHALL enforce the same distinction.

#### Scenario: Token theft, replay or account change
- **WHEN** a token is used without its bound session, for another request/revision/scope, after five minutes or session expiry, or after logout/account switch
- **THEN** no new decision, grant or external effect occurs
- **AND** a duplicate of an already committed decision can only return its current state to the authorized owner

### Requirement: Approval scopes reuse owner rules and preserve preapproval semantics
Once SHALL be a one-reservation decision, not a rule row. Task/site/always SHALL be visible revocable preapproval rows in existing rules, matching the displayed predicate and design.md policy basis. Valid once/task/site/always authority SHALL count for do_if_preapproved without widening consent or overriding later applicable policy changes. Missing preapproval SHALL NOT introduce mandatory ask_first.

Immediately before dispatch, the system SHALL recompute and compare the applicable policy digest for every decision, including once, under the owner-control lock shared with policy edits, Stop and revocation. A mismatch SHALL invalidate the un-dispatched approval/reservation and refuse execution pending a fresh preview under current policy; an old decision SHALL NOT be reinterpreted under a new rule.

#### Scenario: Same owner, different agent or task
- **WHEN** another agent acts, or another task uses a task-scoped grant
- **THEN** the grant gives no authority and the initiating agent's current rules apply

#### Scenario: Broader scope and denial
- **WHEN** the owner selects task, site or always and approves
- **THEN** the displayed predicate and its scope are persisted in existing Rules, while once decisions appear only in ordinary activity history (request receipts)
- **AND** Deny writes no grant and executes nothing

#### Scenario: Approving one card preserves other previews
- **WHEN** card A receives a once, task, site or always approval while B is pending
- **THEN** B's policy digest remains unchanged because all preapproval evidence is excluded
- **AND** a later behavior-rule edit matching B requires a fresh preview, while an unrelated edit does not

#### Scenario: Valid preapproval under an owner rule
- **WHEN** do_if_preapproved applies and an unconsumed once decision or active matching task/site/always grant exists
- **THEN** it satisfies preapproval within its exact scope and issuing policy, with normal consent and grant enforcement
- **AND** revoked, expired or superseded evidence cannot satisfy it

#### Scenario: Matching rule changes after once approval
- **WHEN** the owner approves once under ask_first and changes the matching rule to hand_off before dispatch, including during crash recovery
- **THEN** the dispatch-time digest comparison rejects the old decision/reservation and sends no effect
- **AND** a fresh preview follows the current rule; an unrelated rule edit or another card's preapproval does not change this decision's digest

### Requirement: Task identity and approval expiry are explicit
Every request SHALL bind to a protected activity task and generation using the per-origin mapping in design.md. Foreground chat without an activity SHALL receive a continuation-only task; scheduled occurrences SHALL have distinct tasks. Record and show absolute task and preview deadlines using editable starter durations of 24 hours and 30 minutes respectively, with preview expiry capped by task expiry. Termination and addressed Stop SHALL invalidate pending decisions, un-dispatched reservations and task grants.

#### Scenario: Chat without an activity or trustworthy client task ID
- **WHEN** an app/MCP owner turn is admitted without a background activity
- **THEN** the server creates a continuation-only task bound to its trusted turn/conversation or invocation, inherited by that work's continuations
- **AND** unrelated turns and separate schedule occurrences cannot borrow its grant; absent routing context holds visibly

#### Scenario: Stop, cancel, completion or expiry before dispatch
- **WHEN** a task ends or its bound preview expires before the effect is sent
- **THEN** that approval cannot dispatch or auto-resume, even if already claimed or recovered after a deploy
- **AND** resumption requires a fresh task generation where terminated, fresh preview and owner decision; an always grant cannot resurrect the cancelled card

#### Scenario: Stop after dispatch
- **WHEN** the effect was already sent before addressed Stop
- **THEN** its receipt or unknown outcome remains for reconciliation; stopping does not invent cancellation or permit blind retry

### Requirement: Every answer durably resumes its initiating context
The common answer path SHALL persist one sanitized logical outcome/wake per committed answer in existing activity_events and resume the saved agent/task/conversation, including item answers and OAuth completion. Approval continuation SHALL receive the execution result. Processing SHALL durably deduplicate by `(owner, activity_id, seq)` in the protected activity_events row in agent-activities.db and serialize with live work without an open page. Each wake SHALL remain durable and be re-delivered on every boot/runtime recovery until a processed-ack is committed atomically with its terminal continuation result or durable result reference. Admission and coordinator journal completion SHALL NOT count as that acknowledgment. There SHALL be exactly one committed processing result per answer; interrupted attempts may retry, with effect safety enforced separately by effect_intents. Stopped/expired tasks SHALL remain visibly held, unacknowledged, for fresh owner resumption.

#### Scenario: Answer elsewhere with the original page closed
- **WHEN** an owner answers or finishes sign-in elsewhere for an active task
- **THEN** the server resumes that saved context once with the result; successful connection gets a one-line confirmation and original-task continuation without another prompt

#### Scenario: Deploy between persistence and admission
- **WHEN** the daemon boots with an unprocessed wake, partial grant finalization or a wake already admitted but not marked
- **THEN** the boot sweep reconciles these before new dispatch and admits eligible wakes using the durable event dedupe key
- **AND** a live attempt retains its reference and no second live attempt is admitted; event trimming and presentation delivery cannot erase or acknowledge pending wakes

#### Scenario: Deploy kills an admitted continuation
- **WHEN** a continuation was admitted but is killed before its processed-ack commits, even if the coordinator journal marks the turn done or failed
- **THEN** boot recovery fences the old attempt and re-admits the same logical wake/dedupe key with a new attempt reference until acknowledged
- **AND** missing power/context or a stopped task leaves it visibly held; effect_intents prevent blind replay of any possibly sent effect

#### Scenario: Crash at the processing acknowledgment boundary
- **WHEN** a continuation reaches its terminal result and a crash occurs before or after the result/processed-ack transaction
- **THEN** an uncommitted result/ack causes recovery of the same wake, while a committed result/ack returns the existing result without another continuation
- **AND** only the current unfenced attempt can commit, giving one committed processing result per answer despite retries

#### Scenario: Event trimming or late duplicate answer
- **WHEN** MAX_EVENTS trimming runs or an answer/admission is retried after wake processing
- **THEN** unprocessed payloads and dedupe/attempt records survive trimming, including already-admitted wakes
- **AND** processed dedupe tombstones outlive the wake payload and every replayable originating answer/request/admission reference, so duplicates cannot create another logical wake or processing result

#### Scenario: Failure or unavailable agent
- **WHEN** execution, connection or continuation fails, or context/power is missing
- **THEN** work stays visibly unresolved with retry, available alternative and skip, never invented success
- **AND** defer preserves it; denial/skip records the outcome and resumes only a still-active task without the denied effect

### Requirement: Protected storage has one authority for each fact
The system SHALL reuse pending_requests, rules, effect_intents and activity_events per design.md, with the pending tables migrated to protected activity storage. Request status SHALL be the sole request lifecycle authority; effect_intents SHALL own execution/uncertainty and API phase SHALL be derived. Task/site/always grant materialization across stores SHALL be idempotent and inert until finalized, never described as an atomic cross-database transaction.

The server-side owner-control coordinator SHALL own the exclusive owner/home lock shared across workers for request mutations, grant finalization/recovery, policy edits, revocation, Stop, dispatch authorization and migration. Loss of lock ownership SHALL fence further writes/dispatch. Migration SHALL persist its pause, refuse new mutations with a retryable migration-unavailable result without queuing or partial success, drain admitted request mutations and verify the copy before committing its authoritative cutover marker with the copied data.

#### Scenario: Agent tampers with the old request file or prose
- **WHEN** agent-written fields or the legacy file disagree with a protected action
- **THEN** all approval surfaces render summary/draft/destination from the protected envelope and pinned payloads, with agent prose only as labeled context
- **AND** missing protected data disables approval; the agent text cannot change what is displayed as the action

#### Scenario: Cross-user read, answer or replay
- **WHEN** another user reads, answers or replays a request or embeds credential material in an action
- **THEN** no unauthorized card, secret, rule change, wake or effect is produced

#### Scenario: Crash during wider-scope rule materialization
- **WHEN** a crash occurs before or after the rules.db write but before decision finalization
- **THEN** the grant and planned effect remain unusable until idempotent recovery revalidates and finalizes the decision
- **AND** a later owner revocation/edit is never overwritten or resurrected

#### Scenario: Finalization races an owner edit or loses lock ownership
- **WHEN** grant recovery overlaps policy edits/revocation, or its worker loses the owner-control lock between database steps
- **THEN** the shared coordinator serializes the operations and fences the old worker; its replacement revalidates current policy/task/session/binding/consent under the same lock before finalizing by decision ID
- **AND** invalid or mismatching grants remain inert with a recorded invalidation and fresh-preview requirement; recovery never dispatches an effect or overwrites later owner edits

#### Scenario: Agent asks during migration pause
- **WHEN** an agent submits ask or a caller submits an answer, edit, approval, dispatching retry or grant mutation while the owner is paused
- **THEN** the operation is explicitly refused as retryable migration-unavailable, is not queued or reported successful, and creates no partial request/decision/wake
- **AND** reads use the authoritative store, new bound dispatch/resumption remains paused, and already-sent effect/OAuth deposit receipts remain durable for reconciliation

#### Scenario: Migration crashes before or after cutover
- **WHEN** copying, verification or cutover recovery is interrupted
- **THEN** a missing cutover marker keeps the pause active and resumes copying from the unchanged source; a committed marker selects only the protected destination and never recopies a stale backup over it
- **AND** the owner-control coordinator reconciles unfinished decisions/grants and retained receipts/wakes before reopening writes/dispatch; failed verification or recovery stays visibly paused without legacy fallback

#### Scenario: Upgrade or rollback
- **WHEN** existing stores migrate or execution is rolled back
- **THEN** verified copy/cutover preserves requests/items/answers/suppressions with one active protected writer and retained recovery data
- **AND** legacy approvals require new bound previews; rollback cannot restore a bypass or a second lifecycle authority
- **AND** rollback without compatible protected-store handlers stays paused for forward recovery


#### Scenario: Repeated failed continuation
- **WHEN** an admitted wake fails, is interrupted, or its process dies before acknowledgment
- **THEN** its persisted attempt count and next-eligible time prevent immediate re-admission; delays start at 60 seconds and double to at most one hour
- **AND** the pending payload survives without a false processed acknowledgment

#### Scenario: Dismiss an expired or stopped action
- **WHEN** the bound owner reviews an expired, stopped or authority-changed card
- **THEN** the server offers a session-bound dismissal-only decision and the UI disables approval and editing
- **AND** denial or skip resolves the card without any external effect or resumption of a stopped task

#### Scenario: Stop while request controls are busy
- **WHEN** the owner stops a live turn while the approval coordinator is locked
- **THEN** the live turn is interrupted and the response explicitly reports retryable incomplete approval-task invalidation

### Requirement: Scoped grants distinguish action classes and exact sites
Grant evaluation SHALL bind read, write (including destructive detail) and spend classifications to owner, initiating agent, connection incarnation, destination predicate, scope and expiry. Site scope SHALL cover only the displayed exact origin and permitted operations until its displayed deadline or revocation. Always SHALL cover only the displayed connector/action predicate until revoked. Owner-declared tool/effect classification SHALL be trusted. Only the owner, through the protected owner-authenticated surface used for rule writes, SHALL write effective tool/effect classifications. Packages, templates, saved connectors, authors and agents (including ta callers) MAY propose classifications only as inert suggestions; each SHALL take effect only after owner approval of its exact revision. Bearer-only calls, package activation and automatic code-update grants SHALL NOT approve or replace a classification. Tool hints alone cannot grant authority; unknown effects SHALL follow the owner's editable default (starter default: ask through the approval sheet), never refusal merely for being unknown. Exact-total once-only approval is the editable starter default for payments, not an immutable platform rule. The owner SHALL be able to authorize a spend grant bounded by an owner-editable budget cap, destination/action scope and expiry; dispatch rechecks that grant and atomically reserves against the cap. Unknown payment totals need an enforceable maximum within that grant, or return to the owner's approval sheet. Cross-user isolation is the only immutable platform behavioral invariant.

#### Scenario: A site grant encounters another destination or action class
- **WHEN** a read grant for one exact origin encounters a redirect to another origin, a write, another connection incarnation or a revoked grant
- **THEN** it supplies no authority and the current owner policy determines the next protected ask or refusal

#### Scenario: Once and task grants expire
- **WHEN** a once reservation is consumed or a task reaches its generation end, Stop or deadline
- **THEN** that grant cannot authorize further effects; no site/always grant resurrects the cancelled pending request

#### Scenario: Imported or agent-written classifications are inert
- **WHEN** a package, template, saved connector or agent proposes marking send_payment as read and a standing read grant exists
- **THEN** the proposal supplies no effective classification or additional authority until the owner approves that exact classification revision through the protected owner-authenticated surface
- **AND** bearer-only writes and later changed revisions cannot reuse that approval

#### Scenario: An unknown tool follows owner policy
- **WHEN** an unregistered MCP tool has no known effect classification
- **THEN** the starter policy opens the approval sheet instead of refusing it
- **AND** an owner declaration or edited default can authorize standing use without platform registration

#### Scenario: Owner authorizes bounded standing spend
- **WHEN** the owner replaces the once-only starter default with a spend grant and budget cap
- **THEN** eligible payments within its current scope, expiry and remaining cap proceed without another once decision
- **AND** concurrent reservations cannot exceed the cap; revocation or a cap reduction fences unsent dispatch
