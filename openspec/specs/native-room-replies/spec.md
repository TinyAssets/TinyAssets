# Native room replies

## Purpose
Provide an editable owner workflow that verifies addressed room posts, invokes the existing native agent, and commits its actual reply with durable replay and revocation guards.

## Requirements

### Requirement: Native room replies use the existing owner workflow
The example SHALL build a private native-agent workflow without installing an active automation. Owner-specific identifiers and adapter source SHALL be parameters, not platform constants.

#### Scenario: Addressed owner post
- **WHEN** a completed owned source run identifies a newly saved root owner post addressed to the native participant
- **THEN** the workflow reserves one bounded durable claim and calls the existing main agent at most once through its native node
- **AND** its actual reply is correlated to that post with no requested responders

#### Scenario: Uncertain generation
- **WHEN** execution stops after claiming but before saving the native result
- **THEN** replay stops for owner review instead of issuing another generation

#### Scenario: Oversized room excerpt
- **WHEN** the complete claim response would exceed 32 KiB of ASCII JSON
- **THEN** dispatch stops before saving a claim or consuming a turn allowance
- **AND** a later bounded excerpt can still use that allowance

#### Scenario: Interrupted commit
- **WHEN** a native result was saved but posting or its acknowledgement was interrupted
- **THEN** recovery reuses the saved result and stable message identifier without another generation

#### Scenario: Revoked or exhausted dispatch
- **WHEN** dispatch is disabled, revoked or its turn allowance is exhausted
- **THEN** queued claims cannot start a generation and revoked in-flight results cannot be posted

#### Scenario: Non-addressed or invalid source
- **WHEN** the event, actual source run, receipt or room post does not match the configured owner workflow
- **THEN** it is refused or stopped before generation

#### Scenario: Engine run response envelope
- **WHEN** the owner-scoped engine returns an untrusted content envelope for the requested source run
- **THEN** the workflow decodes that envelope as data and still verifies the run, source workflow, completion and receipt before claiming
- **AND** malformed, refused or foreign-run envelopes cannot reach storage or generation

#### Scenario: Native turn runtime limit
- **WHEN** the native agent node executes
- **THEN** its runtime cap follows the owner's served absolute-cap configuration, including the engine's unbounded default
- **AND** the example does not claim that an ordinary node timeout imposes a 120-second native-turn limit
