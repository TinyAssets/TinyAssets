## ADDED Requirements

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
Once SHALL be a one-reservation decision, not a rule row. Task/always SHALL be visible revocable preapproval rows in existing rules, matching the displayed predicate and design.md policy basis. Valid once/task/always authority SHALL count for do_if_preapproved without widening consent or overriding later applicable policy changes. Missing preapproval SHALL NOT introduce mandatory ask_first.

#### Scenario: Same owner, different agent or task
- **WHEN** another agent acts, or another task uses a task-scoped grant
- **THEN** the grant gives no authority and the initiating agent's current rules apply

#### Scenario: Broader scope and denial
- **WHEN** the owner selects task or always and approves
- **THEN** the displayed predicate and its scope are persisted in existing Rules, while once decisions appear only in request history
- **AND** Deny writes no grant and executes nothing

#### Scenario: Approving one card preserves other previews
- **WHEN** card A receives a once, task or always approval while B is pending
- **THEN** B's policy digest remains unchanged because all preapproval evidence is excluded
- **AND** a later behavior-rule edit matching B requires a fresh preview, while an unrelated edit does not

#### Scenario: Valid preapproval under an owner rule
- **WHEN** do_if_preapproved applies and an unconsumed once decision or active matching task/always grant exists
- **THEN** it satisfies preapproval within its exact scope and issuing policy, with normal consent and grant enforcement
- **AND** revoked, expired or superseded evidence cannot satisfy it

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
The common answer path SHALL persist sanitized outcome/wake data in existing activity_events and resume the saved agent/task/conversation, including item answers and OAuth completion. Approval continuation SHALL receive the execution result. Admission SHALL durably deduplicate event IDs and serialize with live work without an open page. Stopped/expired tasks SHALL remain held for fresh owner resumption.

#### Scenario: Answer elsewhere with the original page closed
- **WHEN** an owner answers or finishes sign-in elsewhere for an active task
- **THEN** the server resumes that saved context once with the result; successful connection gets a one-line confirmation and original-task continuation without another prompt

#### Scenario: Deploy between persistence and admission
- **WHEN** the daemon boots with an undelivered wake, partial grant finalization or a wake already admitted but not marked
- **THEN** the boot sweep reconciles these before new dispatch and admits eligible wakes using the durable event dedupe key
- **AND** no duplicate turn or effect results; event trimming and presentation delivery cannot erase pending wakes

#### Scenario: Failure or unavailable agent
- **WHEN** execution, connection or continuation fails, or context/power is missing
- **THEN** work stays visibly unresolved with retry, available alternative and skip, never invented success
- **AND** defer preserves it; denial/skip records the outcome and resumes only a still-active task without the denied effect

### Requirement: Protected storage has one authority for each fact
The system SHALL reuse pending_requests, rules, effect_intents and activity_events per design.md, with the pending tables migrated to protected activity storage. Request status SHALL be the sole request lifecycle authority; effect_intents SHALL own execution/uncertainty and API phase SHALL be derived. Task/always grant materialization across stores SHALL be idempotent and inert until finalized, never described as an atomic cross-database transaction.

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

#### Scenario: Upgrade or rollback
- **WHEN** existing stores migrate or execution is rolled back
- **THEN** verified copy/cutover preserves requests/items/answers/suppressions with one active protected writer and retained recovery data
- **AND** legacy approvals require new bound previews; rollback cannot restore a bypass or a second lifecycle authority
