# Universe agent harness

## Purpose

Record the implemented additive D6a shell-capability slice. The broader harness
proposal remains in flight; resident-tool cutover and attached MCP extensions
are not claimed by this specification.

## Requirements

### Requirement: Shell capability discovery and calls
The jailed shell SHALL expose `ta search`, `ta describe` and capability calls
over the served platform catalogue, owner connections and local executable
extensions. Invalid extension definitions SHALL be reported without hiding
otherwise valid capabilities.

#### Scenario: Agent-authored extension
- **WHEN** an agent creates a valid shared or agent-scoped executable extension
- **THEN** it can discover, describe and call that extension without deployment.

### Requirement: Launch-bound authority
Platform and connection capabilities reachable through `ta` SHALL remain bounded
by the platform-signed launch tool grant and initiating agent. Caller-supplied
packet fields SHALL NOT replace that context or widen its authority.

#### Scenario: Narrowed launch
- **WHEN** a launch permits `read_graph` but not `write_graph`
- **THEN** discovery omits `write_graph` and calling it is refused.

### Requirement: Connection enforcement and credential custody
Connection calls SHALL apply the initiating agent's current owner rules and
ordinary broker, consent and scope checks. Vault credentials and response-header
values SHALL NOT be returned into the jail.

#### Scenario: Connection refusal
- **WHEN** the initiating agent's rule requires a hand-back or approval
- **THEN** the call refuses that effect instead of borrowing another agent's rule.

### Requirement: Bound approval composition
A bound once approval and an explicit execution context SHALL identify the same
agent before dispatch. Automatic approval capture SHALL require an ambient turn
whose agent matches the selected execution agent.

#### Scenario: Disagreeing agents
- **WHEN** an approval belongs to main but the explicit context belongs to worker
- **THEN** dispatch refuses before invoking the effect proxy.

#### Scenario: Different ambient turn
- **WHEN** a worker-context call needs approval during an ambient main turn
- **THEN** it remains refused without creating a card attributed to main.

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
