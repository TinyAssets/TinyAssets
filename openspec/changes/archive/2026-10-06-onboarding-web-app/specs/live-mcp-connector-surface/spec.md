# Live MCP Connector Surface

## ADDED Requirements

### Requirement: Same-Origin Sibling Routes Excluded From The Handle Set

The canonical handle set served at `/mcp` SHALL remain exactly the seven canonical
handles regardless of any additional same-origin static routes the daemon serves
under `/mcp/` (for example the onboarding app at `/mcp/app`) on the same HTTP app
as the MCP transport. Such sibling routes SHALL NOT be advertised in `tools/list`,
SHALL NOT be counted among the canonical advertised handles, and SHALL NOT cause
the advertised-handle drift canary to report drift.

#### Scenario: A sibling route is not an advertised tool

- **WHEN** a chatbot completes the MCP handshake and lists tools
- **THEN** the advertised handle set is exactly the seven canonical handles
- **AND** the onboarding route `/mcp/app` does not appear in `tools/list`

#### Scenario: Sibling routes do not trip the handle canary

- **WHEN** the advertised-handle drift canary runs against the live surface while
  the onboarding route is served
- **THEN** the canary reports no drift, because the sibling route is not a handle
