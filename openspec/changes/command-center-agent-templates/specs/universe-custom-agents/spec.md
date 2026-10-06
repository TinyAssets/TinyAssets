## ADDED Requirements

### Requirement: Installed agent directories derive from authenticated copy evidence
An addressed recipient agent SHALL expose its installed package `agent_slug`
only from a completed platform-owned installation for the same owner and command
center, whose template and progress identify that binding and public definition.
It SHALL NOT derive a directory from a binding ID, name, or editable configuration.

#### Scenario: Package placement is renamed to avoid a collision
- **WHEN** an owner installs a package beside an existing directory
- **THEN** resolving its copied agent returns the pinned collision-free placement
- **AND** another owner or command center cannot resolve that binding

#### Scenario: No completed file package supplies a directory
- **WHEN** the agent is uninstalled, copied from a screen-only publication, or its install is incomplete
- **THEN** no installed agent slug is returned and no directory is guessed

### Requirement: Shared instruction templates create independent recipient agents
Owner-confirmed command-center publication SHALL export only explicitly selected owner-owned conversable instruction templates as fingerprinted immutable public definition references, and owner-confirmed installation SHALL create fresh private configured bindings without publisher operational settings or authority.

#### Scenario: Two owners adopt the same public template
- **WHEN** each owner confirms installation of the same stably keyed public instruction template
- **THEN** each receives a distinct private binding addressable only in their own command center
- **AND** existing agents, provider assignments, private data and paused automations remain unchanged

#### Scenario: Installation resumes after a binding insert
- **WHEN** installation crashes after the binding insert but before its progress record is saved
- **THEN** replay accepts only the exact unchanged owner/source/configuration binding and does not duplicate it
- **AND** a recipient edit or identity collision refuses without overwriting

#### Scenario: A publisher selects an operational binding
- **WHEN** selection identifies another owner's binding, a serving binding, a conversation consumer, or a definition with unsupported runtime configuration
- **THEN** publication refuses before exposing the binding or its configuration
