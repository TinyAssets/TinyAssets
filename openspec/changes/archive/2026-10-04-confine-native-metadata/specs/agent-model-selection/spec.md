## ADDED Requirements

### Requirement: Native metadata subprocesses are confined to their launch snapshot
Native metadata transport SHALL use the shared confined owned-process launcher with the explicit owning command center, only its exact ephemeral credential snapshot and private temporary runtime storage. It SHALL NOT inherit an ambient engine route, expose sibling command centers or snapshots, or fall back to an unconfined process. Existing custody revalidation, fixed registered protocol, output bounds and cancellation semantics SHALL remain enforced.

#### Scenario: A metadata executable attempts a foreign read
- **WHEN** an owned metadata launch attempts to read another command center, another snapshot, platform source or a host process environment
- **THEN** the OS jail denies those reads while allowing its own synthetic credential and bounded metadata response
- **AND** the result grants no inference or effect authority

#### Scenario: Confinement cannot be established
- **WHEN** the owning directory or snapshot is absent, foreign or redirected, or the existing OS jail is unavailable
- **THEN** discovery fails before the provider is started with sanitized unavailable prose
- **AND** no host launch, new permission or persistent grant is substituted

#### Scenario: A metadata launcher leaves inherited-pipe children
- **WHEN** discovery succeeds, refuses, times out or is cancelled after starting an owned process family
- **THEN** the existing owned-family teardown terminates descendants even after launcher exit
- **AND** pipe cleanup is bounded and cancellation propagates
