# Request answer routing

## Purpose

Return owner answers to the server-recorded asking agent across app and notification surfaces. Implemented by `storage/pending_requests.py`, `request_answers.py` and the existing protected continuation worker.

### Requirement: Answers return to the asking agent
Every request and notification SHALL retain its server-derived asking agent and available execution provenance, and owner replies SHALL reach that agent's conversation and next turn through the shared answer path.

#### Scenario: Sub-agent answer from any surface
- **WHEN** the owner answers a sub-agent request through a sheet, card, desktop or phone reply
- **THEN** delivery targets only that sub-agent, independent of the selected chat

#### Scenario: Main request
- **WHEN** main asks
- **THEN** its answer reaches main

### Requirement: Delivery is owner fenced
Delivery SHALL recheck the recorded owner and target binding; removed or retired asking agents SHALL fall back to the same owner's main conversation with a clear note.

#### Scenario: Foreign owner
- **WHEN** a different owner attempts an answer or the destination belongs to another owner
- **THEN** delivery is refused

#### Scenario: Retired asker
- **WHEN** the asking agent is retired or removed
- **THEN** main receives the answer with the original asker and fallback reason
