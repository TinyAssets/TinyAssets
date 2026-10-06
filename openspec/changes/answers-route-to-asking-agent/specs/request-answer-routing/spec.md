## ADDED Requirements

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

#### Scenario: Co-admin universe
- **WHEN** a universe has more than one admin
- **THEN** the recorded owner still answers and replies, and another admin's answer or reply to that owner's asker is refused before any change

#### Scenario: Unrecorded asker
- **WHEN** a request has no recorded asker, from before provenance or created without an admin identity
- **THEN** only the sole admin or the creator of the asking binding may answer it and becomes its recorded owner; any other caller is refused and nothing is written

#### Scenario: Unrecorded asker with no sole owner
- **WHEN** a request has no recorded asker and neither a sole admin nor an admin who created the asking binding owns it
- **THEN** every answer, decline and reply is refused with `unrecorded_asker_ambiguous` and an explanation, while any admin may dismiss it; the dismissal delivers nothing to any agent

#### Scenario: Caller-supplied target
- **WHEN** an answer or reply carries its own `agent`, `agent_id`, owner or origin
- **THEN** those fields are ignored and delivery uses only the stored origin

### Requirement: Each answer is enqueued once and delivered at least once
A protected approval or bound connection SHALL wake its asker once through its own continuation and SHALL enqueue no generic answer delivery. Every other answer, and each explicit reply by reply id, SHALL be enqueued once. An unacknowledged delivery turn (error, interruption or failure) SHALL be retried with backoff, so the asker can see the same answer more than once.

#### Scenario: Protected approval reply
- **WHEN** a reply targets a protected approval without the protected owner session
- **THEN** it is refused, so words in the asker's thread never stand in for the card's decision

#### Scenario: Interrupted delivery
- **WHEN** a delivery turn ends unacknowledged
- **THEN** it is sent again later and never after it is acknowledged
