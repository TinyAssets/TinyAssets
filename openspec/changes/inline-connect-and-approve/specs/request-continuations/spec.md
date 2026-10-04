## ADDED Requirements

### Requirement: Approval executes the exact owner-confirmed action
The request system SHALL bind each ask-first approval to the normalized action and trusted initiating subject described in design.md, with a server-computed SHA-256 and revision. Approve SHALL execute that action server-side under current ordinary enforcement; no relayed yes or agent retry SHALL be necessary. Only owner rules SHALL decide ask-first policy; starter defaults SHALL remain editable.

#### Scenario: Approve with the current draft
- **WHEN** the owner approves a matching revision/hash under unchanged authority
- **THEN** the server reserves one execution, runs the bound arguments through the ordinary effector and publishes its actual result
- **AND** credentials stay in custody and the initiating agent's rules, consent and connection grants apply

#### Scenario: Edit or stale approval
- **WHEN** a draft is edited, authority changes, or a stale revision/hash is approved
- **THEN** validation and a fresh preview are required before execution; the previous confirmation cannot authorize the changed action

#### Scenario: Duplicate click or ambiguous external outcome
- **WHEN** another surface repeats an approval or a worker crashes after a possible effect
- **THEN** it returns the existing execution state; uncertainty remains pending until reconciled and retry cannot blindly repeat the effect

#### Scenario: Policy does not ask first
- **WHEN** the initiating agent's owner rules say do, hand_off or do_if_preapproved
- **THEN** that behavior remains authoritative, with no platform-mandated ask-first card substituted for it

### Requirement: Approval scope writes a bounded owner rule
The owner decision SHALL write a visible, revocable scoped rule with the design.md predicate: once binds one action digest, task binds the current task, and always persists the displayed predicate. These rules SHALL NOT widen connection consent, cross agent boundaries or override subsequent owner rule changes.

#### Scenario: Same owner, different agent or task
- **WHEN** an action comes from another agent, or another task uses a task-scoped approval
- **THEN** that approval grants no authority and the initiating agent's current rules apply

#### Scenario: Broader scope and denial
- **WHEN** the owner chooses always and approves
- **THEN** the displayed agent/connection/operation/destination predicate is persisted and visible in Rules
- **AND** choosing Deny instead writes no allow rule and executes nothing

### Requirement: Every answer durably resumes its initiating context
The common answer path SHALL persist sanitized outcome events and durably wake the originating agent/task/conversation from every surface, including item answers and OAuth completion. Approval continuation SHALL receive the execution result. Turn admission SHALL deduplicate wake events and serialize with live work without depending on an open page.

#### Scenario: Answer on another device with the original page closed
- **WHEN** an owner answers, or finishes sign-in, elsewhere
- **THEN** the server resumes the original context once with the result; successful connection gets a one-line confirmation and the original request continues without another prompt

#### Scenario: Failure or unavailable agent
- **WHEN** connection, execution or continuation fails, or required provenance/power is unavailable
- **THEN** the request remains visibly unresolved with Try again, available alternative, and skip; it is never silently dropped or called successful
- **AND** defer preserves it while explicit denial/skip resumes with that outcome and no forbidden effect

### Requirement: Protected lifecycle state cannot cross owner boundaries
The system SHALL implement the protected tables, reconciliation and migration contract in design.md, deriving authority from authenticated context and preserving existing credential and consent semantics. Request prose, a digest, or an agent-supplied identity SHALL NOT confer approval authority.

#### Scenario: Forged action, credential or owner
- **WHEN** another user reads/replays/answers a request, an agent tampers with its projection, or a payload embeds a credential
- **THEN** no unauthorized event, secret, rule change, wake or external effect is produced

#### Scenario: Upgrade with legacy requests
- **WHEN** an existing store is upgraded or execution is rolled back
- **THEN** requests and answers are preserved; unverifiable legacy approvals never become executable grants and pending work stays visible
