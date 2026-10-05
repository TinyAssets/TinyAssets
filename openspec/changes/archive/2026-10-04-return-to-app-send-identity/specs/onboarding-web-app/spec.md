## ADDED Requirements

### Requirement: causal return-to-app confirmation (PR 4458 round 2)
The app MUST mint a unique `client_send_id` for each send and retain it in its
inflight record and uncertainty notice. `converse` accepts an optional ASCII
identifier (1-128 letters, digits, underscores or hyphens; empty means legacy),
echoes it on the scoped active turn and persists it on the saved founder row.
The metadata is additive, grants no authority and is not an idempotency key.
Recovery MUST check the running turn first and confirm only on nonempty equal
IDs, never on text or timestamps.

#### Scenario: repeated short prompt
- **WHEN** an identical prompt finished ten seconds before a new interrupted send
- **THEN** neither the notice nor reload recovery confirms the new send from the old row
- **AND** an exact ID match confirms only its own exchange within the caller's thread

#### Scenario: a running turn without an ID finishes
- **WHEN** a watched running turn has no ID (sent by a connector, or running across a deploy)
- **THEN** the app draws only the replies after the newest matching founder row, for display
- **AND** it confirms no unconfirmed send and forgets no inflight record
