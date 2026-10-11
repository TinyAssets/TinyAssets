# Conversation failure history

## Purpose

Preserve admitted failed chat turns as safe, owner-scoped conversation evidence,
without inventing successful replies or automatically replaying work.

## Requirements

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

### Requirement: Failure history contains only bounded safe diagnostics
The platform SHALL use a versioned allowlisted failure code and platform-authored
notice rather than raw provider text, exception strings, attempts or credentials.

#### Scenario: Unknown failure or native sign-in clue
- **WHEN** evidence does not establish a specific cause or only suggests sign-in trouble
- **THEN** the retained notice expresses that uncertainty without inventing a diagnosis
- **AND** it does not claim that the turn performed no actions

### Requirement: A measured gate is a cause, and carries its wait
A failure record SHALL classify from MEASURED evidence, never from a guess. When
any provider on the chain was actually tried, that attempt's own cause decides the
class and a skipped provider beside it explains nothing. When NOTHING was tried,
a skip whose class was measured rather than guessed -- the router's own
quota/cooldown gate, or a fired timer -- IS the cause, so a turn refused by our
own cooldown window is never reported as an unidentifiable failure. A skip class
derived from a substring guess stays unknown.

A record MAY carry `retry_after_s`, a whole bounded positive second count, and
the composed notice SHALL name that wait. It comes only from measurement -- the
source's own retry-after, else the remaining window of our gate, taking the
soonest across several gated providers -- and only for a class whose remedy is
actually waiting. Absent stays absent; a legacy stored record without the field
stays readable.

#### Scenario: the chain was only ever gated
- **WHEN** a turn's single attempt is a skip from the router's own cooldown gate
- **THEN** the record's class is `quota_or_cooldown`, the notice says the source was in a usage-limit or cooldown window and how long remains, and it does not say the cause could not be identified
- **AND** the record still reports that nothing was sent and nothing ran

#### Scenario: a tried provider outranks a gate beside it
- **WHEN** one provider failed and another was skipped by the cooldown gate
- **THEN** the class comes from the attempt that was tried, even when that attempt's own class is unknown

#### Scenario: waiting is not the remedy
- **WHEN** the class is a sign-in problem, a setup gap or a platform fault
- **THEN** no wait is recorded or rendered, so the owner is not sent away to wait out something waiting cannot fix

#### Scenario: a malformed wait costs only itself
- **WHEN** a stored record carries a `retry_after_s` outside its bounded set
- **THEN** that field alone is dropped and the record still reads, so the owner keeps the class, stage, effects and ref that were valid
- **AND** an unrecognized key or an invalid REQUIRED field still rejects the record, because degrading a known optional field is not the same as accepting junk

#### Scenario: Exception contains a secret-like sentinel
- **WHEN** a failing provider includes private payload text in its exception or attempts
- **THEN** that text does not enter durable failure metadata or the platform notice

### Requirement: Readers preserve type and principal isolation
The platform SHALL expose retained failure notices through existing authorized
history, lossless retrieval and agent-memory readers without treating them as
owner instructions, consent or successful model replies.

#### Scenario: Shared and automation context readers
- **WHEN** an authorized shared-self or owner automation reads conversation context
- **THEN** platform notices retain their speaker label and untrusted status
- **AND** conversation rows are scoped to the persisted owner principal, not an arbitrary session

#### Scenario: Two principals access conversation history
- **WHEN** each owner reads conversation state
- **THEN** each sees only the conversation rows authorized for their own principal and universe

#### Scenario: Legacy or corrupt optional metadata
- **WHEN** a read-only caller opens a legacy store or invalid failure metadata
- **THEN** no schema mutation occurs and legacy text remains readable
- **AND** platform text is not labelled as owner speech or an answering-model receipt

### Requirement: Persistence and retry state remain honest
The platform SHALL distinguish saved messages, saved terminal failures and unknown outcomes, and SHALL never automatically replay a failed turn.

#### Scenario: Terminal write cannot complete
- **WHEN** a terminal failure write fails
- **THEN** the already admitted owner message remains committed
- **AND** the caller receives an unsaved-terminal indication without losing the original failure

#### Scenario: Existing account deletion applies
- **WHEN** authorized universe/account deletion runs
- **THEN** conversation and tool evidence follow their existing owner data boundaries

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
