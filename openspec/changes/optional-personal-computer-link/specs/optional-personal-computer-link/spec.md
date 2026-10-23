## ADDED Requirements

### Requirement: A personal computer is an owner-added box connector
The owner SHALL be able to add and remove a personal computer connector in their own box. The box SHALL own its configuration, grants, dispatch and returned owner data. The platform SHALL NOT mount the computer as an owner-data host path or add computer-specific routing or waking logic. The companion SHALL expose local files, commands and screenshot/click only where supported, and enforce the intersection of OS permission and owner Off / Read / Read+interact grants at each operation.

#### Scenario: Owner adds a computer
- **WHEN** the owner installs the connector and pairs a user-installed companion
- **THEN** only that owner's box can dispatch the enabled local capabilities under the companion's OS permissions
- **AND** another owner's box cannot use the pairing or read its results

#### Scenario: Read-only or OS-denied capability
- **WHEN** the box asks for a write, command or click under Read grant, or the OS denies the capability
- **THEN** the companion refuses it and reports its unavailable or denied state truthfully

#### Scenario: Owner removes the connector
- **WHEN** the owner removes or revokes the computer connection
- **THEN** new local dispatch is refused and stale connection authority cannot reconnect
- **AND** re-pair requires a new credential and connection incarnation

### Requirement: Broker-custodied pairing uses the existing owner door
Protected owner confirmation SHALL bind pairing to existing node identity, owner, device and connection incarnation. The companion SHALL keep its credential in the OS keystore; the broker SHALL hold its verifier and revocation state, authenticate an outbound companion connection at `/api`, and bind an opaque relay to the admitted owner's box. Box-to-companion traffic SHALL use the same broker/router channel, never direct egress to a private computer address. The broker SHALL NOT inspect device payloads or decide local capability policy. Revocation SHALL fence active and future channels before cleanup.

#### Scenario: Foreign box or stale incarnation
- **WHEN** another box attempts dispatch, or a revoked credential or old incarnation reconnects
- **THEN** the broker refuses the channel without forwarding device data or actions

#### Scenario: Companion reaches the box
- **WHEN** a paired companion connects outbound to `/api`
- **THEN** broker custody verifies its current owner/device/incarnation and the router relays opaque frames only to that owner's admitted box
- **AND** box replies use the bound channel without direct box-to-computer egress

### Requirement: Desktop companion replaces the legacy tray host
`desktop-app/` SHALL remain the thin app shell and package the optional user-installed companion. The legacy tray host `tinyassets/desktop/tray.py` and its launch path SHALL be deleted during implementation; hosted service SHALL never require the desktop app.

#### Scenario: Desktop app absent
- **WHEN** the owner's computer is off or the desktop app is not installed
- **THEN** hosted service remains available and local capabilities report unavailable

### Requirement: Hosted surfaces work without a computer
Every hosted surface SHALL function without the owner's personal computer online. An action that specifically needs that computer SHALL report unavailable or an uncertain outcome truthfully; it SHALL NOT silently run on another host or block unrelated hosted work.

#### Scenario: No computer is linked
- **WHEN** the owner has no paired or online computer
- **THEN** hosted chat, MCP, API, inbound hooks, schedules and box lifecycle remain available
- **AND** only computer-specific actions report unavailable

#### Scenario: Connection drops during an action
- **WHEN** the computer connection drops after a local action may have started
- **THEN** the box reports an unknown outcome and does not automatically replay the action
