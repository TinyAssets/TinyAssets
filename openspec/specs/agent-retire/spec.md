# Agent retirement

## Purpose

Allow owners to reversibly retire command-center agents without losing their files or history.

## Requirements

### Requirement: Retirement is a setting of the agent's files
An agent SHALL be retired by setting `agent.retired: true` in its `agents/<name>/settings.yaml` and restored by removing it; the directory, its files and its conversation history stay. The main agent cannot be retired.

#### Scenario: Retire and restore
- **WHEN** an owner's agent sets `retired: true` on one of the owner's agents with the edit tool
- **THEN** it disappears from the roster and cannot be addressed, and removing the key returns the same agent with the same id, files and history

### Requirement: Retirement stops execution
A retired agent's running turn SHALL stop at its next cancellation boundary with an agent-retired reason, and new work for it (a turn, an activity, a wake, a request answer) SHALL be refused while retired.

#### Scenario: Running agent
- **WHEN** an agent is retired during a turn
- **THEN** the turn's next check requests a stop with reason "agent retired", and a queued activity of that agent is refused at start

### Requirement: Existing switcher reflects retirement
The command-center switcher SHALL exclude retired agents using the shared roster.

#### Scenario: Browser roster
- **WHEN** the rendered command center loads its agent list after retirement
- **THEN** the retired agent is absent while main and other active agents remain
