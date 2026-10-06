## ADDED Requirements

### Requirement: Browser fallback uses the owner cell and visible control
Browser fallback SHALL be used only after directory, MCP and usable API routes
are exhausted. It SHALL consume D5's owner/center/activity-bound browser inside
the owner's per-owner cell, with Chromium's own sandbox enabled. It SHALL
identify itself to sites as TinyAssets agent automation, including an explicit
User-Agent product token, and SHALL NOT hide that identity to evade a refusal.
The agent SHALL observe only sanitized accessibility snapshots and SHALL NOT
receive raw DOM. The app SHALL provide a live view, Take control, Return control
and Stop without requiring an LLM response.

#### Scenario: Owner takes control or stops during a browser action
- **WHEN** the owner selects Take control
- **THEN** the broker pauses agent input and observation before owner interaction
- **AND** Return control resumes only after custody checks pass
- **WHEN** the owner selects Stop
- **THEN** the task ends and outstanding actions and login callbacks cannot resume it

#### Scenario: A site refuses an identified agent
- **WHEN** a site blocks the identified TinyAssets browser
- **THEN** the app reports the refusal or an owner-only handoff
- **AND** the broker does not remove its agent identity to retry

### Requirement: Browser login keeps reusable credentials out of agent context
Browser login SHALL reuse the D5 context and protected owner takeover with an owner/session/draft/origin/expiry binding. Passwords, MFA values, cookies and reusable session credentials SHALL remain in daemon custody; agent code SHALL receive only scoped surrogate handles. Agent observation/control and login artifact capture SHALL be suspended during credential entry, and credential-bearing data SHALL be excluded when returning control.

The credentialed context SHALL be isolated from the agent shell/filesystem and expose only structured actions and sanitized page observations. It SHALL NOT expose arbitrary evaluation, DevTools, cookie/storage exports, profile files, raw network bodies/headers or credential-bearing input/URL fields. A site whose safe observations cannot be established SHALL remain owner-only with an explicit limitation.

#### Scenario: Agent tries to read credentials after takeover ends
- **WHEN** an agent attempts script evaluation, document.cookie/localStorage access, profile-file reads or a network trace in the credentialed context
- **THEN** the broker refuses those operations and exposes only sanitized permitted page observations
- **AND** a surface it cannot safely expose remains in owner-only control

#### Scenario: Owner logs into a site without an API
- **WHEN** the owner completes the protected login view from the inline card
- **THEN** the broker stores the session in daemon custody, activates the owner-bound connection and returns ordinary authenticated page access under current permission
- **AND** chat, screenshots, DOM snapshots, logs, network traces and extension inputs contain no captured credentials

#### Scenario: Login redirects or requires a passkey
- **WHEN** credential entry targets a new origin or the site needs owner-only challenge completion
- **THEN** the broker requests the new origin binding or keeps owner takeover waiting with an honest status
- **AND** it never forwards existing credentials to an unbound origin or pretends login succeeded

### Requirement: Connection finalization and removal are recoverable and scoped
New connection shapes SHALL reuse the existing coordinator, request idempotency, incarnation fencing and durable continuation with processed-ack. Cancellation, Stop, expiry, logout/account switch or a changed request SHALL invalidate pending capture/finalization. Revocation SHALL prevent further calls before cleanup and SHALL preserve unrelated connections. Schema migration SHALL preserve existing HTTP records and fail visibly on unsupported versions without deleting new-shape custody records.

#### Scenario: Login completion races Stop or restart
- **WHEN** a late callback arrives after Stop, or a process restarts between credential deposit and activation
- **THEN** cancelled work cannot activate, and valid recovery reconciles the same draft with at most one active grant and one committed continuation result
- **AND** abandoned staged custody is cleaned without leaking secrets or waking another owner's task

#### Scenario: Disconnect and reconnect use the same endpoint
- **WHEN** an owner removes an attachment/browser connection and reconnects later
- **THEN** old approvals, surrogates and transport sessions cannot authorize the new incarnation
- **AND** removing an attachment preserves an independent backing HTTP connection while removing that backing connection fences its dependents
