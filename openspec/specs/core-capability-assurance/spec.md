# Core capability assurance

### Requirement: Measured replacement rather than cumulative coverage
The change SHALL replay each listed cutover regression against existing tests before replacement, report the catch rate and zero-catch groups, and remove or fold redundant tests which replace execution seams. Pure logic assertions SHALL remain. Performance claims SHALL measure the complete restored PR proof workload; correctness proofs SHALL NOT be skipped to reduce time.

#### Scenario: Tier selection
- **WHEN** a pull request changes runtime or deploy inputs
- **THEN** affected-test selection requires affected tests inside the user-namespace/browser-capable jail container and one real production-image capability check, plus path-selected jail and browser proofs for ready PRs; live checks remain outside the PR path

#### Scenario: Replacement cannot detect a broken capability
- **WHEN** a capability step stays green after its real defect is reintroduced
- **THEN** it is not accepted as replacement evidence and its ineffective assertion is removed or corrected before admission

### Requirement: One executable capability catalogue
The system SHALL use one catalogue for CI acceptance, live probing and failure attribution covering owner sign-in, streamed chat, bash, read, write, edit, ta, connected HTTP service, image reading, answered approval, background execution, scheduled wake, patch request and public MCP.

#### Scenario: Incomplete probe
- **WHEN** any catalogue capability has no successful evidence
- **THEN** CI acceptance fails and names the missing capability; live missing evidence alerts without blocking or authorizing rollback; skipped work never passes

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
- **WHEN** a live capability fails
- **THEN** the probe opens or updates a labelled GitHub issue and sends an advisory Pushover notification containing capability, error code and revision; notification failures warn without failing deployment

#### Scenario: Model claims success without executing
- **WHEN** a model reports a successful operation
- **THEN** the live probe requires independent file, image, service HMAC, delivery and run observations where available; direct API checks precede optional model-dependent checks, whose variance, quota and provider outages are alert-only; model prose alone cannot pass

#### Scenario: Fresh deployment loses a capability
- **WHEN** the existing public MCP canary or app probe fails after a successful deploy
- **THEN** the deploy fails, opens the deploy-failed issue and rolls back to the captured previous image; an exposed CF Access origin fails the deploy without image rollback. The new core-capability canary remains advisory once provisioned, and never runs a previous-versus-candidate image comparison. Missing configuration warns and does not roll back a deploy that does not move data.

### Requirement: Systemic runtime failure alarms
The daemon SHALL record stable failure codes and success denominators for core capability operations, expose bounded aggregate rates only to the existing operational canary authority, and report spikes and systemic absolute failure rates independently of synthetic probe success.

#### Scenario: Every tool call fails
- **WHEN** a capability fails at least three times for each of two distinct owners and exceeds the absolute threshold or materially exceeds the preceding baseline
- **THEN** protected health output and the live canary report the code, sample counts and rate as an alert-only alarm, never a rollback signal

#### Scenario: Diagnostic privacy
- **WHEN** runtime diagnostics are returned
- **THEN** they contain no owner identity, prompt, credential or raw exception text and identify the observation window and running revision

#### Scenario: Expected client refusal
- **WHEN** invalid client input or an expected authority refusal is rejected
- **THEN** it does not count as a systemic outage; unexpected OS permission errors and trusted upstream authentication failures still count

#### Scenario: Canary not provisioned
- **WHEN** the dedicated canary configuration is absent or incomplete
- **THEN** emit a loud canary-not-provisioned warning and issue, without fabricated capability failures or rollback, and continue a deploy that does not move data. A deploy that would move data — an owner-box layout candidate, a pending migration, or any other data-moving step — refuses before that move. Once the canary is provisioned, a red public canary, app probe, or failed core-canary step still rolls back.

#### Scenario: Concurrent deploy or probe
- **WHEN** multiple runs use the canary owner or the daemon restarts during evidence collection
- **THEN** each run uses unique scratch paths and retries a changed boot identity up to three times; exhausted restart retries warn and never page P0

### Requirement: Shared disk refusal has a stable failure code
Tool jail refusals caused by the shared disk floor SHALL be logged and counted as shared_disk_full by capability rates.

#### Scenario: Disk prevents bash admission
- **WHEN** bash is refused because shared disk space is below the floor
- **THEN** the capability rate snapshot and failure log carry shared_disk_full
