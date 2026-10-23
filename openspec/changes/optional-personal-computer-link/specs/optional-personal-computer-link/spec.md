## ADDED Requirements

### Requirement: A personal computer is an owner-added box connector
The owner SHALL be able to add and remove a personal computer connector in their own box. The box SHALL own its configuration, grants, dispatch and returned owner data. The platform SHALL NOT mount the computer as an owner-data host path or add computer-specific routing or waking logic.

#### Scenario: Owner adds a computer
- **WHEN** the owner installs the connector and pairs a user-installed companion
- **THEN** only that owner's box can dispatch the enabled local capabilities under the companion's OS permissions
- **AND** another owner's box cannot use the pairing or read its results

#### Scenario: Owner removes the connector
- **WHEN** the owner removes or revokes the computer connection
- **THEN** new local dispatch is refused and stale connection authority cannot reconnect

### Requirement: Hosted surfaces work without a computer
Every hosted surface SHALL function without the owner's personal computer online. An action that specifically needs that computer SHALL report unavailable or an uncertain outcome truthfully; it SHALL NOT silently run on another host or block unrelated hosted work.

#### Scenario: No computer is linked
- **WHEN** the owner has no paired or online computer
- **THEN** hosted chat, MCP, API, inbound hooks, schedules and box lifecycle remain available
- **AND** only computer-specific actions report unavailable

#### Scenario: Connection drops during an action
- **WHEN** the computer connection drops after a local action may have started
- **THEN** the box reports an unknown outcome and does not automatically replay the action
