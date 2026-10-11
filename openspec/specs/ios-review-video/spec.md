# iOS App Review video

## Purpose

Automate the six-step App Review packet with private evidence on Simulator and a physical iPhone.

## Requirements

### Requirement: Complete review journey
The UI test SHALL assert the six review shots against production, capture named screenshots, verify a new substantive reply and persistence after termination, attach the verbatim sample through Files, verify its three facts in a new answer, and show the disabled deletion path without confirming it.

#### Scenario: A step cannot complete
- **WHEN** authentication, a reply, persistence, attachment or account navigation fails
- **THEN** the test and workflow fail instead of publishing a passing result.

### Requirement: Credential-safe bounded recording
The dispatch workflow SHALL inject reviewer credentials at runtime, paste the password only in a secure field without per-key echo, exclude private test configuration and raw logs from artifacts, encrypt video/screenshot evidence before public artifact upload, and record one iPhone 14+ on the newest available iOS with a 30-minute device timeout.

#### Scenario: Dry run
- **WHEN** dry_run is true
- **THEN** signed app and runner build and the production Simulator journey execute with no Device Farm calls.

#### Scenario: Physical completion
- **WHEN** Device Farm completes
- **THEN** non-passing results or missing video/step screenshots fail the workflow and available recording evidence is retained.

### Requirement: Public artifacts require an independent recipient key
Evidence SHALL use ephemeral X25519, HKDF-SHA256 and AES-256-GCM to a committed public recipient key, never derive a key from reviewer credentials. Key generation SHALL create an owner-only local private key without overwriting or printing it. Decryption SHALL read that private key locally.

#### Scenario: Recipient is not configured
- **WHEN** the committed public key is absent, invalid or a placeholder
- **THEN** preflight fails before signing or credential-bearing jobs start.

#### Scenario: Wrong private key or tampered ciphertext
- **WHEN** evidence authentication fails
- **THEN** decryption fails before creating any output files.

### Requirement: Main-only dispatch and cancellation recovery
Secret-bearing review dispatches SHALL require main. Dependencies SHALL install with pinned versions in separate steps without secrets. IAM SHALL grant explicit project-scoped recording/cleanup operations plus ListRuns/ListUploads for recovery and global ListDevices for discovery.

#### Scenario: Runner was forcibly terminated
- **WHEN** the next physical run starts
- **THEN** it first sweeps runs and uploads older than two hours in the dedicated project, stops unfinished old runs before deletion, attempts every old resource, and refuses a new run if cleanup fails.
- **AND** dry runs continue to make no AWS calls; absent a next physical run, the lead must remove leftovers manually.
