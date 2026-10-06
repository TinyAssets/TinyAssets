# Request consent recovery

## Purpose
Clear and Deny close an ask without making it a permanent refusal. Preserve
owner consent when the need returns.

## Requirements

### Requirement: Every request kind has a way back
The shared request lifecycle SHALL allow a new ask when a real need recurs after
Clear or Deny. Pending duplicates SHALL collapse to one ask. An explicit
don't-ask-again decision SHALL continue to suppress that ask until lifted.
Reading the rail alone SHALL NOT re-raise a cleared ask. The agent SHALL receive
recent resolved states and recovery guidance in its on-demand request context.

#### Scenario: A connection fails after its reconnect ask was cleared
- **WHEN** the agent encounters the need again and raises the same ask
- **THEN** one new pending card appears and repeated raises return that card

#### Scenario: Owner revisits a cleared ask
- **WHEN** the owner opens Answered history in Needs you and chooses Ask again
- **THEN** the app sends a revisit request to the original asking agent, naming
  the original request, without executing an old action or lifting a mute
- **AND** any replacement ask uses the existing consent and validation gates

#### Scenario: The owner muted the ask
- **WHEN** the same need recurs
- **THEN** the standing decision is returned and no pending duplicate is created
