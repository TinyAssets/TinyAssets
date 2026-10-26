## ADDED Requirements

### Requirement: Modern discovery conforms to the protocol envelope
The server SHALL return complete MCP 2026-07-28 discovery results with nonnegative ttlMs, private cacheScope, supportedVersions, capabilities and standard server identity metadata, without changing legacy initialize.

#### Scenario: Authenticated modern discovery
- **WHEN** an authenticated client sends a valid server/discover request
- **THEN** its result includes resultType complete, ttlMs zero, cacheScope private and _meta.io.modelcontextprotocol/serverInfo
- **AND** the existing event capability and supported versions remain available

#### Scenario: Legacy initialization
- **WHEN** a legacy client initializes using its supported protocol
- **THEN** the existing FastMCP session behavior remains unchanged
