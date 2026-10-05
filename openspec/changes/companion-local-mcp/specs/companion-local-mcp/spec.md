## ADDED Requirements

### Requirement: Local MCP devices expose only permitted capabilities
The system SHALL pair desktop and phone companions as owner-bound local MCP connections over authenticated outbound tunnels. Advertised capabilities SHALL be the intersection of device support, OS permissions and Off/Read/Read+interact grants, enforced again before each operation.

#### Scenario: Read-only device capability
- **WHEN** an agent holding only Read permission requests a click, command or file write
- **THEN** the companion and broker reject interaction even if the OS granted the app broader access

#### Scenario: Unsupported phone ability
- **WHEN** the phone OS denies an advertised class of local ability or suspends the companion
- **THEN** the capability becomes unavailable with a truthful explanation, without pretending execution succeeded

### Requirement: Device lifecycle does not become platform infrastructure
The system SHALL revoke device authority on unpair and reject foreign owner or old-incarnation handles. Cloud runs SHALL remain independent of companion availability.

#### Scenario: Lost or sleeping device
- **WHEN** the owner revokes a device or it goes offline
- **THEN** local calls are fenced or held visibly; unrelated cloud work continues and reconnect requires valid current pairing
