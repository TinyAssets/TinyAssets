## MODIFIED Requirements

### Requirement: Native metadata subprocesses are confined to their launch snapshot
Native model discovery SHALL be the external-agent package's `catalogue` operation, run by the generic runner in the owner's external-agent cell (later the owner's box) with the explicit owning command center, the owner-private agent home, and only the auth files materialized from the owner's revalidated custody snapshot for that job. It SHALL NOT inherit an ambient engine route, expose sibling command centers, other owners' homes or snapshots, place the CLI home under the temporary directory, or fall back to an unconfined process. Custody revalidation, the package's declared protocol, output bounds and cancellation semantics SHALL remain enforced, and every refresh SHALL leave a catalogue receipt.

#### Scenario: A metadata executable attempts a foreign read
- **WHEN** a catalogue launch attempts to read another command center, another owner's agent home, platform source or a host process environment
- **THEN** the cell denies those reads while allowing its own home and bounded metadata response
- **AND** the result grants no inference or effect authority

#### Scenario: Confinement cannot be established
- **WHEN** the owning directory, agent home or custody snapshot is absent, foreign or redirected, or the cell is unavailable
- **THEN** discovery fails before the CLI is started, and its receipt records `failed` with a sanitized reason
- **AND** no host launch, new permission or persistent grant is substituted

#### Scenario: A metadata launcher leaves inherited-pipe children
- **WHEN** discovery succeeds, refuses, times out or is cancelled after starting an owned process family
- **THEN** the owned-family teardown terminates descendants even after launcher exit
- **AND** pipe cleanup is bounded, cancellation propagates, and no auth file remains in the home
