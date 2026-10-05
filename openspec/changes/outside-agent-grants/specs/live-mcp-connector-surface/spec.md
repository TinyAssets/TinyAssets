## MODIFIED Requirements

### Requirement: Every handle requires a named principal

`initialize`, `tools/list` and every `tools/call` on the live connector SHALL
require a valid bearer. A request without one SHALL answer HTTP 401 with a
`WWW-Authenticate: Bearer` challenge carrying the resource-metadata URL, so an
MCP client starts OAuth before it lists tools. The principal SHALL carry verified client classification. Configured first-party
app clients SHALL be exempt from outside grants and the outside kill switch,
while retaining owner/ACL checks and separate interactive approval proof.
An outside principal SHALL carry verified outside-client identity and an active authorization generation as specified by
`outside-agent-grants`. Every outside-client tool call SHALL additionally enforce its current
owner-editable client grant before returning data or causing an effect.
For outside clients, the existing first-converse private-home bootstrap SHALL establish its default
grant before message admission as specified by `outside-agent-grants`. An early
no-home status response from an outside client SHALL require verified active client identity and its
revocation fence but no universe grant, return only the caller's no-home state,
and never provision or expose universe content.
`converse` SHALL require an authenticated actor with write or admin on the target
universe and, for outside clients, `message` for the resolved addressed agent. `get_status` and
`read_graph target=status` SHALL remain pure reads, requiring an outside client's `read`
grant for the returned scope, and never provision.

#### Scenario: unauthenticated initialize
- **WHEN** a client POSTs `initialize` with no bearer
- **THEN** the response is HTTP 401 with the `WWW-Authenticate` challenge and no session is created

#### Scenario: authenticated read
- **WHEN** an authenticated caller with an active client read grant calls `read_graph target=status`
- **THEN** the read succeeds within its granted scope and no universe is created

#### Scenario: newly connected user has no home
- **WHEN** a verified active client calls status for its authorizing user before any home exists
- **THEN** status returns the caller's no-home state without provisioning or exposing universe data
- **AND** a subsequent first `converse` creates the private home and default grant before admitting the message

#### Scenario: hosted connector has a cached tool catalog but no bearer
- **WHEN** it calls any canonical tool without a valid bearer
- **THEN** no tool handler runs
- **AND** the MCP error result carries `_meta["mcp/www_authenticate"]` with the routed protected-resource URL, an OAuth error code and an error description

#### Scenario: valid bearer lacks a client grant
- **WHEN** an authenticated owner client calls a handle without its required level or agent scope
- **THEN** it receives a clear MCP error naming the missing grant and directing the owner to Connected apps
- **AND** no ungranted data or effect is produced

## ADDED Requirements

### Requirement: Every existing handle enforces client scope daemon-side
For outside clients, the daemon SHALL apply the outside-agent grant to `converse(agent_id)`, `get_status`, `read_graph`, `write_graph`, `run_graph`, `read_page` and `write_page`, including their aliases and batch paths. Downstream actions SHALL follow the current owner-selected message-only/message-and-act policy and always retain revocable outside origin. For direct calls and message-only work, reads SHALL require `read`, messages `message`, and writes/runs `control`; spend, publish and external posts SHALL additionally require `costly` and the existing owner approval policy. Enforcement SHALL cover every affected agent/resource, filter mixed reads before serialization and refuse unresolved scope. No new top-level handle, public MCP endpoint or brand-specific implementation SHALL be introduced.

#### Scenario: Main-only grant encounters mixed content
- **WHEN** a main-only read client requests a mixed agent list, graph summary or page
- **THEN** collection results exclude out-of-grant content and a shared indivisible page requires scope covering every contributing agent
- **AND** explicit ungranted targets refuse without leaking their contents

#### Scenario: Page publication and graph run
- **WHEN** a control-only client attempts publication through `write_page` or a spending operation through `run_graph`
- **THEN** daemon-side admission names the missing costly grant before any effect
- **AND** the same check applies through write_graph aliases and message-only downstream execution

#### Scenario: Missing addressed agent is explicit main
- **WHEN** a client calls `converse` without `agent_id`
- **THEN** it is checked against main's message grant
- **AND** specifying another agent checks that resolved owned agent without fallback to main

### Requirement: Access and approval projections carry outside-client authority
The existing `read_graph target=access` readback SHALL add the caller's effective client grant without replacing the access sections owned by `agent-access-controls`; other sections SHALL be constrained to granted scope. Pending-action projections SHALL add verified originating client identity and grant revision/generation to the safe provenance owned by `inline-connect-and-approve`, without duplicating its approval protocol or granting bearer approval authority.

#### Scenario: Owner receives an outside app's sensitive request
- **WHEN** an admitted outside client raises a bound sensitive action
- **THEN** the existing pending-action read and protected owner sheet identify its client and addressed agent
- **AND** the client can read the safe request state but cannot approve it

#### Scenario: Grant refusal is actionable
- **WHEN** a client lacks control on an otherwise owner-authorized agent
- **THEN** the refusal identifies the missing control/agent grant and the owner editing path
- **AND** cross-user targets retain existing uniform not-found/auth refusals without identity disclosure

### Requirement: Bearer HTTP routes cannot bypass outside-client admission
All bearer-accepting routes SHALL follow the admission or outside-refusal disposition in outside-agent-grants, including every app HTTP route and private bearer surface listed in its `bearer-surfaces.md`. First-party identity SHALL be verified from platform configuration, never claimed by caller metadata. Owner controls SHALL be operational before grant enforcement activates.

#### Scenario: Outside client substitutes HTTP for MCP
- **WHEN** an outside bearer attempts `/app/api/read` or another app HTTP route to bypass its MCP grant
- **THEN** the HTTP route refuses before owner data or effects
- **AND** the first-party app remains available under its owner authority
