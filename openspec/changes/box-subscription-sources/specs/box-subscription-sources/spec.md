## ADDED Requirements

### Requirement: Owner-box subscription runtime
The system SHALL load editable source definitions and install pinned packages only in the admitted owner volume, with integrity verification and exact registry egress.

#### Scenario: First installation and reuse
- **WHEN** an owner connects a source twice
- **THEN** a verified pinned install is reused and no shared image is changed

### Requirement: Native login and durable isolation
The system SHALL run the native CLI login in the box and expose its URL or device code, verifying completion before publishing a source.

#### Scenario: Login across sleep and wake
- **WHEN** a successful login is followed by box shutdown and restart
- **THEN** that owner can invoke the source and another owner cannot see the login

### Requirement: Generic invocation and selection
The system SHALL invoke subscription_cli only through the owner box using definition argv/output data, and include connected sources in normal owner model selection.

#### Scenario: Non-interactive reply
- **WHEN** an authorized owner selects a connected subscription source
- **THEN** a model call returns the CLI response without a provider-specific Python adapter

### Requirement: Definition-generated connect controls
The page SHALL offer native subscription login controls from definitions alongside HTTP sources.

#### Scenario: Native code handoff
- **WHEN** the CLI requires a returned code because its callback cannot be reached
- **THEN** the page relays the code to the same owner-bound login process and does not report success before verification
