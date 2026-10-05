# Connect-anything starter skill

## Purpose

Teach the owner's agent to connect arbitrary services through existing generic
primitives, as editable user content rather than platform policy or provider code.

## Requirements

### Requirement: Editable delivery

New command-center creation SHALL seed `skills/connect/SKILL.md`. The ordinary
skill index SHALL expose it, and the ordinary agent file tools SHALL read and
edit it. Repeated provisioning SHALL preserve an existing skill. Existing
accounts SHALL be able to fetch the identical Markdown through
`read_graph target=handbook query=write_graph.connect` and save it through the
ordinary file-writing tool. Reading guidance SHALL NOT reinstall deleted skills
or overwrite customizations.

#### Scenario: Owner changes the recipe

- **WHEN** the owner edits the seeded skill description
- **THEN** the next skill index reflects the edit without a code change

### Requirement: Generic connection ladder

The skill SHALL teach reuse of existing connections, current API documentation,
registered OAuth resolution through a `connect` ask, secure inline API-key
entry when needed, a generic HTTP connection verified by a harmless read through
`ta`, and a saved service skill or extension. Secrets SHALL never be requested
in chat or copied into saved connector content. Unavailable MCP attachment or
browser login SHALL be described explicitly with the missing next capability.

#### Scenario: Unknown API-key service

- **WHEN** a scripted model requests a service absent from the provider directory
- **THEN** its tool path creates a `connect` pending request with a `secret` field
- **AND** the owner's separate secure form submission creates the HTTP connection
- **AND** model messages and pending-request records contain no submitted key
- **AND** the connection is discoverable and callable through `ta` for a read
