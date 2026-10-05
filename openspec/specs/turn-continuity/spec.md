# Turn continuity

## Purpose

Keep retained work discoverable independently of prompt size and tool process lifetime.

## Requirements

### Requirement: Retained conversation remains discoverable
The system SHALL expose owner-bound search and lossless retrieval of retained messages regardless of the recent prompt window, and SHALL tell the served agent how to retrieve missing context.

#### Scenario: Earlier exchange falls outside the prompt window
- **WHEN** more than twenty newer messages or the prompt character budget excludes an exchange
- **THEN** a literal query finds the retained exchange, with paging and exact message chunks, without returning another session's messages

### Requirement: Persistent workspace remains discoverable
The system SHALL inventory agent-created workspace files using the same logical paths as its persistent served tools, without following links or exposing shadowed entries.

#### Scenario: New turn after a process restart
- **WHEN** an agent writes exports in `/u` and a fresh turn reads its inventory
- **THEN** the files remain readable and visible in the bounded inventory, with explicit instructions for discovering entries outside that preview
