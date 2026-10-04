## ADDED Requirements

### Requirement: Requests appear inline at the point of need
The bubble thread SHALL render one revision-aware card per request beside its originating turn, identifying the requesting agent. Connect SHALL show plain-word scope and Connect / Not now. Ask-first approval SHALL show the protected action summary, destination, editable draft, expiry, Approve / Edit / Deny and once / this task / always. Duplicate connect entry points SHALL focus this card; the rail SHALL be read-only history. Failure SHALL retain the card/draft with Try again / available alternative / skip. Existing owner-gated refresh transport SHALL remain in use.

#### Scenario: Request appears on a phone
- **WHEN** the app refreshes a connection or owner-rule approval request
- **THEN** its card appears in the originating thread and the collapsed bubble shows waiting-for-owner
- **AND** account switches clear previous-owner cards and all reads enforce current ownership

#### Scenario: Edit and select a scope
- **WHEN** the owner changes draft or scope in the protected first-party view
- **THEN** the card displays the validated protected preview, with once as default, exact wider predicate and deadlines visible
- **AND** a new session-bound approval token is required before Approve

#### Scenario: Agent prose conflicts with the action
- **WHEN** the agent's summary or legacy request fields describe a different destination or draft
- **THEN** the card renders approval data only from the server-protected envelope and pinned payloads, with agent prose separately labeled
- **AND** missing protected data disables approval instead of falling back to the agent text

### Requirement: Sign-in completes server-side without the parent app
Connect SHALL launch the bound OAuth flow in a popup or system in-app browser without replacing the thread. The server SHALL generate and retain the PKCE verifier in credential custody and the callback server SHALL exchange the code, deposit credentials and durably wake the saved active task as specified in design.md. Neither browser SHALL need an app bearer, verifier, parent relay or deep-link return. Tokens, secrets and codes SHALL NOT enter transcripts, agent context, event payloads or callback output/logs.

#### Scenario: Parent page closes before sign-in finishes
- **WHEN** the owner completes valid provider sign-in with the parent closed but the initiating session still valid
- **THEN** the callback server validates the bound flow and uses its server-held verifier to complete exchange/deposit and persist the wake
- **AND** the initiating agent continues the original active task without a page message or client completion call

#### Scenario: Native app is suspended
- **WHEN** the system browser returns from the provider while the native app is suspended
- **THEN** the same server callback completes sign-in and records the result/wake without native bearer access or app-link return
- **AND** the resumed app reads the safe result using its normal owner session

#### Scenario: Replay, revoked session or changed task
- **WHEN** callback state is replayed/expired, the owner logged out/switched accounts, or task/request/consent bindings changed
- **THEN** no unauthorized exchange/deposit/wake is admitted and no other owner/request can receive the connection
- **AND** stopped work cannot resume automatically

#### Scenario: Deploy after credential deposit
- **WHEN** credentials were deposited but the request/wake commit was interrupted
- **THEN** recovery reuses the flow receipt to commit the result/wake idempotently without repeating the code exchange
- **AND** uncertain redemption/deposit remains visibly unresolved rather than reported successful

#### Scenario: Provider failure or blocked popup
- **WHEN** sign-in fails, is cancelled or cannot open
- **THEN** the card remains pending with retry, available alternative and skip, without implying unsupported provider coverage
- **AND** available key deposit remains private to the card and vault

## MODIFIED Requirements

### Requirement: The chat with an agent floats over the command center
The app SHALL present thread/inline requests, request history, model bar, composer and status in a floating cloud above the stage, draggable/resizable/collapsible by pointer, touch or keyboard. Without saved placement it SHALL fill the stage for owners without a layout and start as a corner bubble with a layout. Placement SHALL persist per owner, agent (default main) and viewport (phone below 760px, otherwise wide) in owner-ui-preferences with device fallback. Cloud/bubble SHALL remain wholly visible after resizing; dragging SHALL NOT also open it.

The bubble SHALL remain reachable over broken/custom layouts as emergency control and conversation with every agent, retaining agent selection and addressed Stop. It SHALL show working/waiting-for-owner/failed/idle status from existing server reads even collapsed, and indicate replies received while collapsed. This change SHALL NOT migrate the working-state transport to a pushed stream.

#### Scenario: A new owner signs in
- **WHEN** no layout or placement exists
- **THEN** the cloud fills the stage

#### Scenario: A command-center layout is active
- **WHEN** a layout exists without saved placement
- **THEN** the bubble appears in the stage corner

#### Scenario: The owner placed it before
- **WHEN** the owner reloads
- **THEN** their owner/agent/viewport placement is restored regardless of layout

#### Scenario: The window shrinks
- **WHEN** the stage shrinks
- **THEN** cloud/bubble is moved and resized as needed to remain wholly visible

#### Scenario: A bubble is dragged
- **WHEN** the owner drags the bubble
- **THEN** it moves without opening

#### Scenario: A custom layout fails or the owner stops work
- **WHEN** the layout breaks or covers the stage
- **THEN** the protected bubble remains reachable for agent selection, conversation and addressed Stop
- **AND** Stop invalidates pending approvals and task grants for its affected work, retaining already-sent effects for reconciliation
