## ADDED Requirements

### Requirement: Portable broker rule projection
The protected export door SHALL capture broker-owned rules for previewed agents,
scoped to the bound owner and center, and reject download if policy changes.
Selected agents SHALL carry their previewed behavior rules; source identifiers,
preapprovals and other centers' rules SHALL not travel. Local file tools SHALL
enforce workspace.files, and bash SHALL enforce workspace.shell plus local
consent. Missing broker enforcement SHALL never become a portable grant.

#### Scenario: Policy changes after content approval
- **WHEN** the broker policy generation or digest changes after preview
- **THEN** download requires a new preview even if the box files did not change.

#### Scenario: Exported agent has a restrictive workspace rule
- **WHEN** that agent requests a file or shell action locally
- **THEN** hand_off refuses, ask_first and do_if_preapproved require fresh local consent
- **AND** the agent cannot edit its policy using file tools.

#### Scenario: A source rule names a connection or operation
- **WHEN** its source authority cannot be bound locally
- **THEN** the projection conservatively applies its behavior to that action class
- **AND** no source grant or identifier appears in the portable policy.

### Requirement: Portable agent roster
Export SHALL include selected root harness files without collisions and derive
its roster from included instructions and settings. Settings SHALL use the
canonical bounded parser, replace source model/connection identifiers with local
references and omit source owner bindings. Other credentials SHALL stay blocked.
Import SHALL verify the roster against files; a per-agent endpoint override
SHALL never inherit the global endpoint's credential.

#### Scenario: Main agent exports from its actual root
- **WHEN** the owner selects root AGENTS.md, settings.yaml and a skill
- **THEN** the same preview bytes appear beneath agents/main in the export
- **AND** unrelated root state and unselected files remain absent.

#### Scenario: Source handles do not travel
- **WHEN** selected settings contain a model connection and source owner
- **THEN** the preview and archive contain local model references and no source handles
- **AND** unknown or credential-bearing behavior fields cannot bypass screening.

#### Scenario: Roster and files disagree
- **WHEN** an imported roster names missing settings or another agent's model group
- **THEN** import refuses the archive before custody or activation.

### Requirement: Local model environment file
The standalone runner SHALL read a bounded regular .env file as literal model
configuration, without sourcing shell code, following links or exposing that
file to model file tools. Process environment values SHALL override file values.

#### Scenario: Environment file contains shell-looking text
- **WHEN** a local configuration value contains a shell substitution
- **THEN** the value remains literal and no shell process executes.

#### Scenario: Environment file is malformed or linked
- **WHEN** .env has duplicate or unsupported keys, invalid quoted values, excessive size or a link
- **THEN** startup fails visibly without falling back to another model configuration.

### Requirement: Standalone chat runner
The export SHALL contain a Python-standard-library runner with no TinyAssets
account dependency. It SHALL read the selected agent's instructions and offer
only read, write, edit and bash to the owner's configured chat-completions
endpoint. A turn SHALL have an explicit model-call bound and fail visibly on
malformed responses or exhausted bounds.

#### Scenario: Export works without the platform
- **WHEN** the runner is copied into a folder with agent instructions
- **THEN** it can complete a tool-using turn against an owner-configured endpoint
- **AND** it does not import TinyAssets or contact its services.

### Requirement: Local owner consent
The runner SHALL refuse file paths outside the export root, symlink components,
credential files and policy/runtime writes. Bash SHALL require per-command
terminal consent unless the owner explicitly configures allow; deny and absent
terminal input SHALL not execute. The runner SHALL not start schedules implicitly.

#### Scenario: Unattended command lacks consent
- **WHEN** a model requests bash with the default policy and stdin cannot provide approval
- **THEN** no process executes and a refusal is returned to the model.

#### Scenario: Model tries to change its rules
- **WHEN** a file tool targets run/rules.json or runner code
- **THEN** the tool refuses the operation.

### Requirement: Atomic file authority
Knowledge SHALL be committed through the bound owner's BoxProvider with a
generation fence. A complete OKF revision SHALL precede its commit pointer;
only the pointed revision SHALL be indexed. Unknown entry metadata and exact
prose SHALL round-trip. A snapshot from another owner, center or epoch SHALL
never be accepted for a write.

#### Scenario: A write fails before its commit point
- **WHEN** staging is interrupted before the pointer write
- **THEN** readers continue to see the previous complete revision
- **AND** a retry with the original snapshot and operation id completes idempotently.

### Requirement: Private content preview
Export and import SHALL share a versioned file manifest with relative paths and
content hashes. Arbitrary content SHALL start excluded and selection SHALL bind
to the exact preview bytes and owner. Detected credentials SHALL remain excluded
regardless of approval; uninspectable binaries SHALL need named selection.

#### Scenario: Approval cannot override a detected credential
- **WHEN** the owner selects a file that contains a detected credential
- **THEN** export refuses that selection without returning an archive.

### Requirement: Bounded inert import
Import SHALL validate all bytes and path collisions before retaining inert
bytes in platform-owned package quarantine, SHALL enforce resource bounds, and SHALL never
execute imported files or create an activation record.

#### Scenario: Archive tries to escape or collide
- **WHEN** an archive contains traversal, links, special files, or case or Unicode collisions
- **THEN** import fails before creating quarantine files.

#### Scenario: Imported code remains inert
- **WHEN** a valid archive contains executable runner code or hooks
- **THEN** only validated regular file bytes are retained in package custody
- **AND** no runner, hook, filter, installer or schedule executes.

### Requirement: Protected owner transfer door
The app SHALL expose preview, private download and import through one owner
transfer route. Every operation SHALL require the current named identity and
its exact-origin protected owner session. A bearer token alone SHALL not expose
preview content or approve a transfer. The configured BoxProvider SHALL bind
ownership before any content operation; an absent provider SHALL fail explicitly.

#### Scenario: Source or owner changes after preview
- **WHEN** the source generation, placement epoch, authenticated owner or provider changes
- **THEN** the old preview cannot authorize a download.

#### Scenario: Private contacts are selected individually
- **WHEN** the owner selects one captured contact occurrence in the private export profile
- **THEN** only that occurrence remains and all unselected occurrences are removed
- **AND** the share profile rejects private contact selections.

#### Scenario: Import reaches existing package custody
- **WHEN** an authenticated owner imports a validated archive
- **THEN** the existing account package pool accounts for its inert canonical bytes
- **AND** an owner/center/epoch-bound quarantine record is retained outside every box
- **AND** neither an executable install action nor an activation receipt is created.
