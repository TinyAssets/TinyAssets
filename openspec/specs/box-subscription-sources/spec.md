# Box subscription sources

## Purpose
Restore native subscription model access inside isolated owner boxes using editable source data.

## Requirements

### Requirement: Owner-box subscription runtime
The system SHALL load editable source definitions and build declared pinned CLI packages once into the shared read-only box image, with the same lock/integrity verification and exact registry egress as runtime installation. Only login/configuration state lives in the owner home. A record absent from the image MAY install into the admitted owner volume with a 1 GiB free-space check and failure/cache cleanup.

#### Scenario: First installation and reuse
- **WHEN** an owner connects a source twice
- **THEN** the matching verified image binary is reused and no owner install is performed

#### Scenario: Fallback installation
- **WHEN** a user-added source needs installation
- **THEN** connect returns immediately, the conversation shows it as installing, and offers sign-in when ready without a waiting tab

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
The page SHALL resolve declared address aliases to the same SourceRecord schema used by detected HTTP sources, then offer the native login handoff from the single address field. There SHALL be no separate subscription source list.

#### Scenario: Native code handoff
- **WHEN** the CLI requires a returned code because its callback cannot be reached
- **THEN** the page relays the code to the same owner-bound login process and does not report success before verification

### Requirement: Evidence-based default selection
The system SHALL include connected subscription sources in ordinary automatic model ordering, using only declared comparable evidence with a bounded freshness period and preserving explicit owner preferences.

#### Scenario: Stronger source connected
- **WHEN** a newly connected source has stronger fresh comparable evidence than the current automatic choice
- **THEN** automatic ordering selects it without replacing an explicit saved preference

#### Scenario: Unknown native default
- **WHEN** the native default alias has no comparable model evidence
- **THEN** it remains unranked rather than receiving an invented vendor quality score
