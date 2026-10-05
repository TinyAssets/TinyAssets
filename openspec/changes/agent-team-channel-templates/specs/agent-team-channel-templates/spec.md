## ADDED Requirements

### Requirement: Delegation and agent messages preserve current authority
The system SHALL expose ta spawn and ta message using authenticated owner/center/run identities, explicit delegation grants, model bindings and durable message/task IDs. Child and recipient access SHALL be checked at dispatch; cross-user messages SHALL require an existing recipient grant.

#### Scenario: Parallel team returns summaries
- **WHEN** an owner launches three differently configured workers from an orchestrator
- **THEN** each uses its bound model and scoped workspace, records task identity and returns summaries/artifact references without inheriting unrelated authority

#### Scenario: Forged sender or stopped recipient
- **WHEN** a message spoofs sender/owner, lacks recipient permission or targets stopped work
- **THEN** it is refused or visibly undelivered without a wake, grant or effect for another owner

### Requirement: Task board changes are revisioned and budget aware
The team board SHALL store assignments, dependencies, state and result references in an ordinary scoped collection with expected revisions and history. Execution and budget authority SHALL remain in existing launch/budget records.

#### Scenario: Two agents claim the same task
- **WHEN** workers update the same expected board revision
- **THEN** one update commits and the other receives a conflict without dropping task state; configured budget exhaustion pauses affected execution

### Requirement: Channel teams publish with local bindings and remix credit
Channel bundles SHALL package routing, receivers and outbound operations as templates using general primitives. Published copies SHALL contain no live author credentials, message history or grants. Versioned installed-copy updates and remix credit SHALL use the existing package/update/attribution owners.

#### Scenario: Second account installs a forum team
- **WHEN** another owner copies a four-agent topic-routing template
- **THEN** the copy requires its own forum/model/connection bindings and routes only its authenticated inbound events

#### Scenario: Creator updates a remixed team
- **WHEN** a new package version is offered to an edited installed copy
- **THEN** the existing updater preserves local changes or reports conflicts, honors bundle permissions and retains original/remix credit with Undo
