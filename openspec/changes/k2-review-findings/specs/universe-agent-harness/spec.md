## ADDED Requirements

### Requirement: Signed grants bound ta mutations
The ta dispatcher SHALL require a signed backend mutation grant before dispatching
a capability that is not explicitly read-only, in addition to its existing gates.

#### Scenario: Status-only agent attempts extension mutation
- **WHEN** an agent node holds only get_status and invokes extension install, activate or revoke
- **THEN** ta refuses before writing extension or projected UI state
- **AND** status and extension help/list/events remain readable

### Requirement: Provider-neutral served inventory
Served turns SHALL expose only the granted subset of read/write/edit/bash, with
backend capabilities reachable through ta, and SHALL expose no native web tool.

#### Scenario: Thin-loop owner turn
- **WHEN** a fully granted owner turn opens the thin loop
- **THEN** the model inventory is exactly read/write/edit/bash
- **AND** conversation and activity reads remain reachable through authorized ta read_graph

#### Scenario: Non-granted provider turn
- **WHEN** Claude or Codex launches a non-granted served conversation
- **THEN** both launch with an empty tool inventory and no native WebFetch
