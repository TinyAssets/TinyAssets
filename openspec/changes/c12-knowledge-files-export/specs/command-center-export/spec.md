## ADDED Requirements

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
Import SHALL validate all bytes and path collisions before creating fresh
platform-owned quarantine, SHALL enforce resource bounds, and SHALL never
execute imported files or create an activation record.

#### Scenario: Archive tries to escape or collide
- **WHEN** an archive contains traversal, links, special files, or case or Unicode collisions
- **THEN** import fails before creating quarantine files.

#### Scenario: Imported code remains inert
- **WHEN** a valid archive contains executable runner code or hooks
- **THEN** only regular file bytes are copied to quarantine
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
