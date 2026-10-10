# Agent wake when

## Purpose

Let an owner agent resume authorized work automatically after an external blocker clears, using durable, bounded follow-ups.

## Requirements

### Requirement: Durable bounded follow-up
The platform SHALL expose registration, listing and cancellation through ta for the bound agent, with a saved note, expiry, finite repetitions, and time and/or condition. Conditions SHALL support the owner's run finishing/failing, the agent's request being answered, an available connection, release containment of a commit/PR, and a successful bounded probe.

#### Scenario: Resume without another user message
- **WHEN** a blocked agent registers a wake and the condition becomes true
- **THEN** the continuation worker starts that agent with its saved note and normal owner budget, and acknowledges the wake only after a successful turn.

#### Scenario: Restart and bounded retry
- **WHEN** the daemon restarts before a due time or condition match
- **THEN** persisted state remains owed and is checked again; retries stop at expiry or their declared finite bounds.

### Requirement: Owner authority and visibility
The platform SHALL derive authority from the launch or signed-in owner, recheck it before dispatch, expose pending wakes and cancellation in the app, and refuse foreign owner resources. No new model tool or provider-specific implementation SHALL be added.

#### Scenario: Foreign resource or target
- **WHEN** an agent names another owner's run, request, wake or agent
- **THEN** access is refused and no foreign turn starts.

#### Scenario: Narrowed launch
- **WHEN** an outside-client or narrowed workflow launch tries to register a wake
- **THEN** registration is refused; it cannot gain the full serving-owner authority of a later chat turn.

#### Scenario: Cancel pending work
- **WHEN** the owner cancels a pending wake
- **THEN** subsequent sweeps cannot dispatch it.

### Requirement: Starter blocker behavior
The editable starter skill SHALL tell an externally blocked agent to register and verify a wake before telling the user it will resume automatically.

#### Scenario: External blocker
- **WHEN** work cannot proceed until an external condition changes
- **THEN** the agent saves what it was doing and what to try, registers a bounded wake and tells the user it will resume on its own.
