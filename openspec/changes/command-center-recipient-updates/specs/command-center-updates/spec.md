# Recipient updates

## Purpose
Preserve recipient ownership and truthful source provenance when planning updates to copied command centers.

## ADDED Requirements

### Requirement: Updates retain immutable source and component provenance
Update plans SHALL bind an author-bound publication and exact immutable release ancestry. Each installed component SHALL retain its own source release and transformed recipient baseline. Unknown legacy provenance SHALL remain unlinked without an explicit recipient decision.

#### Scenario: Selective update
- **WHEN** the recipient selects one compatible component from a newer release
- **THEN** the plan identifies that component's new release and retains other installed component versions

#### Scenario: Legacy copy
- **WHEN** an activated installation pin validates against its exact immutable public definition, plan and complete target mapping
- **THEN** exact prior source provenance is recoverable but automatic update enrollment is not inferred

### Requirement: Update plans preserve recipient changes and authority
Plans SHALL detect divergent edits, deletions, destination collisions and dependency mismatches before writes. A plan SHALL carry exact current-content preconditions for later application. New permissions or privileged effects SHALL require recipient decision.

#### Scenario: Recipient edited a changed component
- **WHEN** both recipient and publisher have changed the same installed component
- **THEN** the plan reports a conflict and proposes no overwrite for it

#### Scenario: Unselected dependent
- **WHEN** a selected dependency update would invalidate a retained component's required source version
- **THEN** the plan reports the dependency mismatch without silently expanding selection

### Requirement: Automatic update eligibility is recipient scoped
Automatic plans SHALL require per-adoption opt-in, proven lineage, a public source, no conflicts and an unchanged accepted capability envelope. Plans SHALL preserve automation runtime state and SHALL NOT authorize execution, credential import or new privileged effects.

#### Scenario: New permission under opt-in
- **WHEN** the recipient opted in but a release requests a new capability
- **THEN** automatic eligibility is false and explicit recipient decision is required

### Requirement: Manual screen replacement commits with its receipt
The owner SHALL be able to explicitly select an exact same-author public definition to replace the UI of a verified component copy. The screen, installed baseline and replay receipt SHALL commit atomically. Dependencies, files, agents, automation runtime state, unrelated screens and selection SHALL remain unchanged. User-selected replacements SHALL NOT imply publisher release lineage or automatic update eligibility.

#### Scenario: Unpowered owner accepts a screen update
- **WHEN** a recipient without serving provider bindings previews and accepts an unchanged-dependency screen replacement
- **THEN** the screen updates without inference and records its selected definition separately from retained component source definitions

#### Scenario: Update fails during mutation
- **WHEN** an error occurs after the screen SQL write but before transaction commit
- **THEN** the screen, adoption baseline and pending consent roll back together and a retry may complete once

#### Scenario: Concurrent owner change
- **WHEN** owner authority, private content, UI revision or dependency configuration changes after preview or storage reservation
- **THEN** application refuses without overwriting the changed state

#### Scenario: Unsupported component update
- **WHEN** a proposed replacement changes workflow or agent versions, automation specifications, UI dependency bindings, or selects components other than the existing UI
- **THEN** the adapter refuses before component effects and does not report a partial success

#### Scenario: Retained automation changes after preview
- **WHEN** a retained recipient automation is retired, removed, reassigned or reconfigured after preview or storage reservation
- **THEN** the UI update refuses without advancing its installed baseline
- **AND** an automation writer racing the final dependency check cannot commit until the UI transaction commits or rolls back

### Requirement: Trusted update controls separate saving from displaying new code
The owner's Switch dialog SHALL expose verified source IDs and explicit same-author replacement choices. Preview SHALL not apply a replacement. Applying SHALL preserve the mounted screen and selected screen until the owner explicitly opens the updated screen; no message or model call SHALL be relayed by preview, apply, decline or provenance registration. These controls SHALL NOT be exposed to an untrusted screen bridge or the agent's consent-answer surface.

#### Scenario: Current screen is updated
- **WHEN** the recipient accepts replacement of the currently displayed screen
- **THEN** new screen code is saved without automatically remounting or executing it
- **AND** an explicit Open updated screen action displays it

#### Scenario: Account or selection changes while an update reply is delayed
- **WHEN** a reply belongs to an earlier account, home, selection or management session
- **THEN** it cannot populate or act on the current management session

#### Scenario: Earlier copy has an exact receipt
- **WHEN** the owner selects Verify earlier copy for an actual activated install pin
- **THEN** registry creation requires the exact immutable source, target mapping and unchanged installed baseline
- **AND** reading the earlier-copy list does not create a baseline or overwrite private edits

#### Scenario: Registration fails after installation
- **WHEN** provenance registration fails after a copy has completed
- **THEN** the copy remains installed and its authoritative success receipt includes separate registration-unavailable status
