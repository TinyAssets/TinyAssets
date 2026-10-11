## MODIFIED Requirements

### Requirement: Authorized terminal failures persist atomically
The platform SHALL commit an admitted owner's original message verbatim in the principal-scoped conversation store BEFORE dispatch. Terminal success or a typed safe failure notice SHALL refer to that existing message without duplicating it. Failure to commit SHALL prevent dispatch.

#### Scenario: Failed provider turn followed by another successful turn
- **WHEN** an admitted turn fails after dispatch, including process death
- **THEN** the original message survives refresh and is available to the next authorized model
- **AND** recovery adds a typed platform notice without attributing a successful answer

#### Scenario: Unowned or unauthenticated request
- **WHEN** authentication or owner/interlocutor admission fails
- **THEN** no conversation row is persisted by this path

#### Scenario: Identical requests are distinct
- **WHEN** the owner explicitly sends the same message twice
- **THEN** persistence does not collapse them by text equality

#### Scenario: Storage fails before dispatch
- **WHEN** the admitted message cannot be committed
- **THEN** no provider or tool dispatch occurs and the caller receives a failure

### Requirement: Persistence and retry state remain honest
The platform SHALL distinguish saved messages, saved terminal failures and unknown outcomes, and SHALL never automatically replay a failed turn.

#### Scenario: Terminal write cannot complete
- **WHEN** a terminal failure write fails
- **THEN** the already admitted owner message remains committed
- **AND** the caller receives an unsaved-terminal indication without losing the original failure

#### Scenario: Existing account deletion applies
- **WHEN** authorized universe/account deletion runs
- **THEN** conversation and tool evidence follow their existing owner data boundaries

## ADDED Requirements

### Requirement: Failed turns preserve effect continuity
The platform SHALL expose failed-turn effect evidence to the next authorized agent as untrusted history, preserving known completed work and uncertain outcomes without replay.

#### Scenario: Injected fault after an effect
- **WHEN** a tool result is durably recorded and the turn subsequently faults
- **THEN** the next turn can identify the original request and the recorded effect
- **AND** any started operation lacking a terminal result remains explicitly uncertain

#### Scenario: Tool evidence storage fails
- **WHEN** the tool start cannot be durably recorded
- **THEN** that tool is not dispatched
- **AND** failure to read later tool evidence does not hide the admitted owner message

#### Scenario: Large results and paged history
- **WHEN** a failed turn has large tool results
- **THEN** model history includes bounded excerpts without evicting the original request
- **AND** history page cursors and page sizes count only durable conversation rows

#### Scenario: Overlapping admitted turns
- **WHEN** another request is admitted between a turn's request and its terminal result
- **THEN** that turn's learning completion does not settle the intervening request
