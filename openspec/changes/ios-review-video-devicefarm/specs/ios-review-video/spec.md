## ADDED Requirements

### Requirement: Complete review journey
The UI test SHALL assert the six review shots against production, capture named screenshots, verify a new substantive reply and persistence after termination, attach the verbatim sample through Files, verify its three facts in a new answer, and show the disabled deletion path without confirming it.

#### Scenario: A step cannot complete
- **WHEN** authentication, a reply, persistence, attachment or account navigation fails
- **THEN** the test and workflow fail instead of publishing a passing result.

### Requirement: Credential-safe bounded recording
The dispatch workflow SHALL inject reviewer credentials at runtime, type the password only in a secure field, exclude private test configuration and raw logs from artifacts, and record one iPhone 14+ on the newest available iOS with a 30-minute device timeout.

#### Scenario: Dry run
- **WHEN** dry_run is true
- **THEN** signed app and runner build and the production Simulator journey execute with no Device Farm calls.

#### Scenario: Physical completion
- **WHEN** Device Farm completes
- **THEN** non-passing results or missing video/step screenshots fail the workflow and available recording evidence is retained.
