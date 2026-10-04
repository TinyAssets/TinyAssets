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
### Requirement: Publication series require explicit publisher lineage consent
A release SHALL bind an immutable author, exact public definition and explicit parent in a stable publication series. Publisher consent SHALL show the linkage and summary. Component keys SHALL retain their source identities across release history. Matching names or old publication receipts without lineage consent SHALL NOT establish a series.

#### Scenario: Competing release completion
- **WHEN** two approved publications name the same series parent
- **THEN** only the first registered release advances the head and the other remains an ordinary publication until its publisher explicitly resolves the stale linkage

#### Scenario: Removed component key is reused
- **WHEN** a later publication assigns a previously used component key to a different source object
- **THEN** release registration refuses without rebinding recipient provenance

### Requirement: Presentation update opt-in is explicit and bounded
Recipients SHALL explicitly confirm per-adoption presentation-update policy bound to an exact verified series/release and current baseline. Eligibility SHALL allow only name/style changes with executable content, permission surface and retained components unchanged. Other updates SHALL require a decision. Eligibility checks SHALL NOT apply updates or execute effects.

#### Scenario: Opt-in has not been confirmed
- **WHEN** a recipient previews or declines an opt-in request
- **THEN** automatic eligibility remains disabled

#### Scenario: Code or dependencies change
- **WHEN** an opted-in recipient has a candidate with changed markup, script, resources, permissions, components or private dependency state
- **THEN** the candidate requires explicit decision and no content is applied

#### Scenario: Owner opts out after private edits
- **WHEN** a recipient disables the policy after modifying their screen
- **THEN** opt-out succeeds and replaying the old opt-in cannot re-enable the policy

### Requirement: Version and policy controls use trusted explicit consent
The owner SHALL see release histories only from exact registered immutable-definition membership, including change summaries and source availability. New series and exact parent linkage SHALL appear in platform-authored publication consent. Policy preview SHALL not enable updates; only the separate exact request and digest confirmation may change a recipient preference. These controls SHALL not be callable through the engine consent-answer surface or an untrusted screen bridge.

#### Scenario: Publication succeeds but history cannot be linked
- **WHEN** lineage registration fails after a completed publication
- **THEN** the publication remains public and its visible receipt explicitly says history was not linked
- **AND** no release or automatic eligibility is fabricated

#### Scenario: Delayed policy response belongs to another session
- **WHEN** account, home, selection or management session changes while a policy reply is pending
- **THEN** the old response cannot update the current controls or issue a follow-on write

#### Scenario: Owner deletes their account after a home change
- **WHEN** the account is deleted with a new home or no current home
- **THEN** its private adoption, policy, application receipt and status records are removed across former homes
- **AND** another owner's records remain, including records naming the deleted owner's former home

### Requirement: Stored presentation grants authorize only fenced data updates
An automatic executor SHALL load a real accepted owner/home/adoption policy, preserve its original consent evidence, and select only the next verified release. It SHALL recheck ACL, home, account deletion, source publication, immutable pin targets, dependencies, local baseline, policy revision and storage admission under actual store write reservations. Only eligible name/style changes SHALL update the existing UI. UI, adoption, policy progress and receipt SHALL commit together in the main database without actor impersonation, manual acceptance fabrication, model calls or automation state changes.

#### Scenario: Opt-out races application
- **WHEN** opt-out commits before the final policy revision check
- **THEN** no automatic UI update commits
- **AND** if application committed first its receipt remains truthful and opt-out stops subsequent updates

#### Scenario: Sequential release changes no screen bytes
- **WHEN** the next verified release retains the exact UI and component bytes
- **THEN** installed source progress and its receipt advance without rewriting the UI
- **AND** an intervening unsupported release is never silently skipped

#### Scenario: Accounting fails after UI commit
- **WHEN** main committed the UI and receipt but accounting settlement fails
- **THEN** the result remains applied with pending settlement debt
- **AND** retry settles or measures the storage without reapplying UI content

#### Scenario: Competing dependency or quota writer
- **WHEN** retirement, publication withdrawal, pin mutation or reservation expiry races the final application
- **THEN** the competing store writer is fenced until commit or rollback
- **AND** a change that committed before the final fence prevents the automatic write

### Requirement: Automatic maintenance joins serving admission and reset exclusion
Automatic checks SHALL run as bounded work within the existing admitted service maintenance loop, with process admission before storage access and the service writer barrier held through each sweep. No import-time timer or synthetic owner identity SHALL be created. Recipient controls SHALL distinguish saved opt-in from worker availability and expose sanitized application or pending-settlement status. Settlement recovery SHALL remain independent of enabled policy.

#### Scenario: Reset excludes automatic writes
- **WHEN** an exclusive maintenance barrier is held
- **THEN** the automatic sweep cannot mutate recipient data
- **AND** releasing the barrier permits a later admitted sweep to apply a real eligible stored grant once

#### Scenario: Owner reloads after automatic application
- **WHEN** an opted-in presentation update has been applied by service maintenance
- **THEN** the owner sees its exact installed release and saved preference after browser reload
- **AND** no message or model invocation is emitted by the controls or worker

#### Scenario: Maintenance cannot start or complete
- **WHEN** the service maintenance worker is not started or its sweep fails
- **THEN** the owner sees waiting or unavailable status without any claim that a check completed
- **AND** later sweeps can retry without losing saved screens or policies
