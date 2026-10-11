## ADDED Requirements

### Requirement: Owner provider homes are outside temporary paths
Provider cells SHALL use a private owner-owned home outside `/tmp` for CLI configuration and credential copies, with temporary files separate and other owners inaccessible.

#### Scenario: Shipped Codex discovery and execution
- **WHEN** an owner uses Codex in the production image
- **THEN** the real CLI lists models and completes a real protocol turn through the confined provider path
- **AND** startup does not depend on creating helpers under a temporary home or escaping the OS cell

### Requirement: Connected workflow inference receives normal accounting
Owner workflow inference SHALL reserve and settle usage using the existing owner-bound accounting path for any connected model source, without requiring a special budget the owner never configured.

#### Scenario: Connected HTTP model used by a review workflow
- **WHEN** an authorized owner workflow calls a connected model source
- **THEN** the broker receives a matching reservation and the response is usable by the workflow
- **AND** normal owner limits, grants and cancellation still apply

#### Scenario: Another owner's source or an exhausted allocation
- **WHEN** a workflow lacks source authority or its applicable allocation is exhausted
- **THEN** the request is refused before network dispatch
