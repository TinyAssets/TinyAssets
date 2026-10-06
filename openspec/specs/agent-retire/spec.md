# Agent retirement

## Purpose

Allow owners to reversibly retire command-center agents without losing their data.

## Requirements

### Requirement: Reversible owner-scoped retirement
The platform SHALL expose revision-fenced retire and restore operations on agent bindings through write_graph and ta, preserving history and files and retaining account-deletion coverage.

#### Scenario: Retire and restore
- **WHEN** an owner retires an agent with its current revision
- **THEN** it disappears from lists and cannot be addressed, and restoring with the new revision returns the same agent and history

#### Scenario: Protected targets
- **WHEN** an actor attempts to retire main, another owner's binding, or uses a stale revision
- **THEN** the operation is refused without changing the binding

### Requirement: Retirement stops execution
The platform SHALL stop running turns and automation agent work cleanly with an explicit retirement reason and refuse new work while retired.

#### Scenario: Running agent
- **WHEN** an agent is retired during execution
- **THEN** execution stops at its safe cancellation boundary with an agent-retired reason

### Requirement: Existing switcher reflects retirement
The command-center switcher SHALL exclude retired agents using the shared roster.

#### Scenario: Browser roster
- **WHEN** the rendered command center loads its agent list after retirement
- **THEN** the retired agent is absent while main and other active agents remain

