## ADDED Requirements

### Requirement: Measured replacement rather than cumulative coverage
The change SHALL replay each listed cutover regression against existing tests before replacement, report the catch rate and zero-catch groups, and remove or fold redundant tests which replace execution seams. Pure logic assertions SHALL remain. Net test count and measured per-PR CI time SHALL decrease.

#### Scenario: Tier selection
- **WHEN** a pull request changes runtime or deploy inputs
- **THEN** affected-test selection requires fast affected unit tests and one real production-image capability check; live and heavy suites remain outside the PR path

#### Scenario: Replacement cannot detect a broken capability
- **WHEN** a capability step stays green after its real defect is reintroduced
- **THEN** it is not accepted as replacement evidence and its ineffective assertion is removed or corrected before admission

### Requirement: One executable capability catalogue
The system SHALL use one catalogue for CI acceptance, live probing and failure attribution covering owner sign-in, streamed chat, bash, read, write, edit, ta, connected HTTP service, image reading, answered approval, background execution, scheduled wake, patch request and public MCP.

#### Scenario: Incomplete probe
- **WHEN** any catalogue capability has no successful evidence
- **THEN** the probe fails and names the missing capability; skipped work never passes

### Requirement: Production image acceptance before merge
Required CI SHALL build the PR production image and exercise every capability on a disposable synthetic owner through real cells, broker, launcher and relays, with deterministic simulation only at the model boundary and external service fixtures.

#### Scenario: Execution seam replaced
- **WHEN** acceptance code substitutes a cell, broker, launcher or relay
- **THEN** structural validation fails

#### Scenario: Cutover regression returns
- **WHEN** a documented cutover defect is reintroduced
- **THEN** the affected capability check fails with observable runtime evidence

### Requirement: Hosted live assurance and deployment rollback
The system SHALL run live checks after every deploy and every five minutes on hosted infrastructure, using only a dedicated canary owner and the public app and MCP surfaces.

#### Scenario: Live capability failure
- **WHEN** a capability fails or credentials required to probe it are absent
- **THEN** the workflow fails, opens or updates a labelled GitHub issue and sends the existing Pushover alert containing capability, error code and revision

#### Scenario: Fresh deployment loses a capability
- **WHEN** post-deploy capability acceptance fails after successful image convergence
- **THEN** the deploy fails and invokes its existing previous-image rollback decision

### Requirement: Systemic runtime failure alarms
The daemon SHALL record stable failure codes and success denominators for core capability operations, expose bounded aggregate rates only to the existing operational canary authority, and report spikes and systemic absolute failure rates independently of synthetic probe success.

#### Scenario: Every tool call fails
- **WHEN** recent tool failures exceed the minimum sample and absolute threshold or materially exceed the preceding baseline
- **THEN** protected health output and the live canary report the code, sample counts and rate as an alarm

#### Scenario: Diagnostic privacy
- **WHEN** runtime diagnostics are returned
- **THEN** they contain no owner identity, prompt, credential or raw exception text and identify the observation window and running revision
