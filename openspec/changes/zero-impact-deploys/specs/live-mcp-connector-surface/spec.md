## ADDED Requirements

### Requirement: Replaceable frontends preserve connector continuity
Frontends SHALL own no execution authority; durable session/principal bindings, JSON-RPC mappings and reply cursors SHALL live in a shared journal. Old frontends SHALL retain streams through completion, while replacements attach by persisted identity. A generic 202 SHALL NOT replace a tool result and uncertain mutations SHALL NOT blindly replay.

#### Scenario: Frontend generation changes during a tool call
- **WHEN** a legacy MCP call remains active across a frontend deploy
- **THEN** its old frontend retains the stream until delivery without blocking unrelated centers or duplicating execution

### Requirement: Connector timeout evidence bounds handover
The harness SHALL measure supported Claude.ai and ChatGPT connector call and idle timeouts and prove handover plus reply latency fits the shortest demonstrated supported limit. Heartbeats alone SHALL NOT count as evidence that an absolute timeout extends.

#### Scenario: Timeout contract is unproven
- **WHEN** candidate continuity lacks measured timeout evidence for a supported connector
- **THEN** that transport's zero-impact claim is blocked
