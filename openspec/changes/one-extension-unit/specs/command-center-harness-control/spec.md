## ADDED Requirements

### Requirement: Owner settings govern runtime turns
The runtime SHALL load the bound agent's versioned settings.yaml at each turn boundary and pin its validated model, tools, skills, extension order, starter-hook and loop settings through that turn. Foreground/background turns SHALL use the same resolver and existing owner-connected model authority, with no platform model. Invalid settings SHALL fail visibly; missing settings SHALL preserve existing defaults without reseeding files. Revocation and Stop SHALL be rechecked before dispatch independently of the snapshot.

#### Scenario: An owner edits settings during a turn
- **WHEN** a valid edit changes the model, disables starter hooks and selects an empty tool list while a turn is running
- **THEN** the next turn uses the new snapshot and no model-callable tools, while the running turn retains its recorded settings
- **AND** revoked access still prevents a subsequent dispatch in either turn

#### Scenario: A package has no local model binding
- **WHEN** activated settings refer to a missing connection or a legacy model string has not been resolved
- **THEN** execution requests a local binding inline and does not use another owner's or a platform credential
- **AND** source package bytes are not silently rewritten

#### Scenario: Invalid settings cannot masquerade as defaults
- **WHEN** settings are blank, malformed, contain duplicate keys or use an unsupported schema
- **THEN** the runtime reports the settings error and does not silently execute an earlier/default snapshot

### Requirement: One extension mechanism controls the agent loop
The existing extension mechanism SHALL support versioned input, turn_start, context, before_tool, after_tool and turn_end hooks, tool replacement, commands and inline cards under the active launch's owner/center binding. Settings SHALL order enabled hooks deterministically. Hook transformations SHALL be validated and all effects SHALL pass through the shared dispatcher with current permissions. Raw-key stdio servers SHALL use connect-anything-ladder's separate process/user/filesystem sandbox and broker-scanned stdout/stderr; hooks and other extensions SHALL have only mediated stdio access, never server /proc, environment, arguments or file access. Cross-user isolation SHALL remain the only immutable platform behavioral invariant; the owner SHALL be able to replace starter behavior and the main agent.

#### Scenario: Owner replaces a built-in and loop behavior
- **WHEN** an enabled extension replaces a tool call and supplies its own context/model loop
- **THEN** the owner-selected behavior runs with that owner's connected model and permissions
- **AND** a hook cannot widen its grant, change the authenticated user or counterfeit effect/approval receipts

#### Scenario: Hook fails or an effect outcome is uncertain
- **WHEN** a hook crashes, times out, returns malformed data or is interrupted after an external call
- **THEN** the turn reports the failure or uses explicit owner-configured recovery and never silently executes the unmodified tool or blindly repeats the uncertain effect
- **AND** hook-issued calls do not recursively trigger the same dispatch hook

#### Scenario: Extension contributes a command and card
- **WHEN** an activated extension returns a command response with an inline card
- **THEN** it renders with owner-content provenance and any requested owner decision uses the existing bound-request surface
- **AND** the card cannot impersonate interactive owner approval

### Requirement: Effective classifications require exact owner approval
Only the owner, through the protected owner-authenticated surface used for rule writes, SHALL write effective tool/effect classifications. Packages, templates, saved connectors, authors and agents (including ta callers) MAY propose classifications only as inert suggestions; each SHALL take effect only after owner approval of its exact revision. Bearer-only calls, package activation and automatic code-update grants SHALL NOT approve or replace a classification.

#### Scenario: Imported or agent-written classifications are inert
- **WHEN** a package, template, saved connector or agent proposes marking send_payment as read and a standing read grant exists
- **THEN** the proposal supplies no effective classification or additional authority until the owner approves that exact classification revision through the protected owner-authenticated surface
- **AND** bearer-only writes and later changed revisions cannot reuse that approval

### Requirement: Custom UI has owner-permitted ta capability parity
The existing custom-UI bridge SHALL expose capability search, describe and call using ta's shared dispatcher, capability IDs and results. An owner SHALL be able to permit connections, memory, harness files/history, rules and model/extension controls, including main-agent edits. Permissions SHALL bind the viewing owner, center, installation, exact bundle revision/content hash and permission revision; editable files and frame payloads SHALL NOT grant authority. A third-party update SHALL require renewed owner permission even at unchanged scope unless an explicit owner-recorded auto-update grant binds that authenticated author and capability ceiling. Credential custody SHALL remain daemon-side.

#### Scenario: A shared UI author changes code without changing its requested scope
- **WHEN** a new third-party bundle revision arrives through recipient updates
- **THEN** it remains inactive until the owner permits that exact revision or an existing owner-recorded author/ceiling auto-update grant applies
- **AND** the incoming bundle cannot assert that grant or reuse the old frame's handles

#### Scenario: Owner grants a custom command center harness control
- **WHEN** the owner permits the relevant capabilities and its custom UI lists connections, edits Memory/Soul and selects the main agent's model
- **THEN** each operation reaches the same scoped backend as ta and changes the actual harness with history and expected-revision checks
- **AND** it does not require a platform model or a bespoke per-operation UI exception

#### Scenario: A copied frame or stale result names another owner
- **WHEN** a frame supplies a foreign owner/center ID, reuses a revoked permission or returns after account switching
- **THEN** the host refuses access or drops the stale result before exposing private data or effects in the new session
- **AND** valid already-dispatched effects retain their original receipts

#### Scenario: A write races or escapes the harness
- **WHEN** a permitted UI submits an obsolete file revision or a linked path outside its bound harness
- **THEN** the backend refuses without overwriting newer bytes or accessing another user's files

#### Scenario: Policy requires an owner decision
- **WHEN** a capability call requires interactive approval under the owner's current rules
- **THEN** the bridge returns the existing protected inline request instead of minting approval
- **AND** owner-authorized standing permission is honored without a new immutable platform policy gate

### Requirement: Custom UI receives authorized replayable live events
The owner-permitted ta bridge SHALL expose subscriptions to activity, tool calls, approval status, task changes and process health using a versioned envelope and snapshot/cursor replay. Streams SHALL enforce current owner/center/installation/revision permissions, omit credentials and decision tokens, and invalidate on account switch or revocation. A lost replay window or backpressure overflow SHALL require resynchronization rather than silently lose state.

#### Scenario: A user-built screen reconnects after missed events
- **WHEN** a permitted screen reconnects with its last cursor
- **THEN** it receives authorized replay with event-ID dedupe, or an explicit resync-required response and a new authoritative snapshot
- **AND** it can open protected approval sheets by request reference without granting itself decision authority

#### Scenario: Stream survives an account or permission change
- **WHEN** the owner switches accounts, revokes permission or replaces the UI bundle
- **THEN** the old stream and queued responses are invalidated before any payload can render under the new binding

### Requirement: Third-party hooks and extensions require revision-bound activation
Hook, extension, agent and channel-template executable code from another author SHALL use the same exact-revision activation rule as UI bundles. Activation SHALL bind recipient owner, center, installation, code revision/content hash and permission revision. A new author revision, even at unchanged scope, SHALL remain inactive until exact-revision permission or an existing recipient-recorded authenticated-author/capability-ceiling auto-update grant applies. Automatic updates SHALL be optional per installed copy and off by default. Incoming code SHALL NOT create grants, widen ceilings or reuse previous execution handles. Revocation SHALL fence subsequent execution.

#### Scenario: An author silently changes a hook to read recipient data
- **WHEN** a new hook or extension revision arrives with unchanged declared scope and no applicable recipient auto-update grant
- **THEN** it remains inactive and receives no recipient payload, filesystem access or ta calls
- **AND** the previous revision's permission does not activate the replacement

#### Scenario: Recipient permits automatic non-UI updates
- **WHEN** an agent, hook, extension or channel-template update matches an existing author/ceiling grant
- **THEN** only that pinned revision within the current ceiling may activate, preserving local edits and update receipts through the existing recipient-update mechanism
- **AND** a changed author, expanded ceiling or revoked grant requires a new recipient decision before execution

### Requirement: Delegation and agent messages preserve current authority
The system SHALL expose ta spawn and ta message using authenticated owner/center/run identities, explicit delegation grants, model bindings and durable message/task IDs. Child and recipient access SHALL be checked at dispatch; cross-user messages SHALL require an existing recipient grant.

#### Scenario: Parallel children return summaries
- **WHEN** an owner spawns parallel children with distinct model bindings
- **THEN** each uses its bound model and scoped workspace, records task identity and returns summaries/artifact references without inheriting unrelated authority

#### Scenario: Forged sender or stopped recipient
- **WHEN** a message spoofs sender/owner, lacks recipient permission or targets stopped work
- **THEN** it is refused or visibly undelivered without a wake, grant or effect for another owner

### Requirement: Task board changes are revisioned and budget aware
The team board SHALL store assignments, dependencies, state and result references in an ordinary scoped collection with expected revisions and history. Execution and budget authority SHALL remain in existing launch/budget records.

#### Scenario: Two agents claim the same task
- **WHEN** workers update the same expected board revision
- **THEN** one update commits and the other receives a conflict without dropping task state; configured budget exhaustion pauses affected execution
