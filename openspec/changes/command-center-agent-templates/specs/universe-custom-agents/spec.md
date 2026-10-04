## ADDED Requirements

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
