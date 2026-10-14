## ADDED Requirements
### Requirement: Activated packages are the only extension executable authority
The ta client SHALL discover executable contributions only from the current owner-bound activated revision catalog and SHALL dispatch through its current generation check.

#### Scenario: Workspace manifest claims a tool
- **WHEN** any v1 or v2 extension.json exists in a shared or agent workspace
- **THEN** the file alone grants no discovered or callable tool
- **AND** an activated immutable v2 revision remains callable through the ordinary jailed dispatcher

### Requirement: Activities are admitted agent sessions
An activity SHALL bind its durable run and generation before execution and enter the sole session.start under its owner box. It SHALL use the existing provider budget and invocation authority with an explicit activity subject, without creating an Activities branch.

#### Scenario: A worker loses its generation
- **WHEN** the activity is stopped, superseded, yields or loses current owner authority
- **THEN** further invocation/effect dispatch is refused and terminal cancellation is explicit
- **AND** replacement waits until the former execution is proven terminal

### Requirement: One handle registration preserves profile confinement
Public and pinned profiles SHALL use the same registration boundary while retaining independent owner globals, middleware and authenticated capabilities.

#### Scenario: Two owners use pinned tools
- **WHEN** simultaneous pinned sessions register their handlers
- **THEN** one session cannot change the other session's owner or capability grants

### Requirement: Retired market execution preserves history
Removing bid, treasury and paid escrow/bonus execution SHALL NOT delete or rewrite stored ledger, settlement or attribution history.

#### Scenario: A retired route is requested
- **WHEN** a caller requests a removed paid-market action
- **THEN** the platform refuses it without changing ledger state
