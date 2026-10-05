## ADDED Requirements

### Requirement: Private routing is explicitly owner and destination bound
The system SHALL attach an owner overlay through an outbound connection with broker-held credentials and enforce exact host/service grants before private calls. A network attachment SHALL NOT authorize arbitrary private addresses or bypass infrastructure and cross-user isolation.

#### Scenario: First contact to a private MCP server
- **WHEN** an owner connects an overlay and requests an unapproved private MCP host
- **THEN** the protected sheet requests the exact host/service scope before ta may use the existing MCP attachment path

#### Scenario: Rebinding or foreign network
- **WHEN** resolution leaves the approved route, targets platform metadata or another owner names the attachment
- **THEN** the broker refuses before connecting or exposing credentials

### Requirement: Network revocation fences dependent access
Disconnect SHALL invalidate network generations and dependent calls before cleanup and SHALL preserve unrelated connections.

#### Scenario: Reconnect after revocation
- **WHEN** the same overlay is disconnected and later reattached
- **THEN** old host grants and MCP sessions remain invalid; offline work reports held status without a host fallback
