## ADDED Requirements

### Requirement: Cell execution resolves the admitted owner
Execution cells SHALL resolve the exact command center's owner from the broker admission log and admit only that owner or that center's internally bound run actor.

#### Scenario: Background and delegated execution
- **WHEN** a background graph, automation or sub-agent executes as its center actor
- **THEN** its code, HTTP and tool cells execute with the same admitted owner identity as interactive execution.

#### Scenario: Cross-owner refusal
- **WHEN** a foreign owner, foreign center actor, unbound actor or retired center requests a cell
- **THEN** admission fails before execution, without allocating an identity or changing ownership.

#### Scenario: HTTP effect under a center actor
- **WHEN** a center actor emits an authenticated HTTP effect
- **THEN** the same resolver supplies its admitted owner to the existing broker grant check, and foreign grants remain refused.

### Requirement: Tool networking retains filtered egress
Tool bash SHALL reach permitted HTTP and HTTPS endpoints through the pinned checking proxy while retaining network isolation.

#### Scenario: Production-copy acceptance
- **WHEN** acceptance restores and migrates the cutover snapshot and starts the real server
- **THEN** background and automation code/HTTP graphs, a sub-agent, Patch Request Intake through file_issue with stub GitHub, and tool bash curl to a local fixture succeed; cross-owner and private-destination requests remain refused.
