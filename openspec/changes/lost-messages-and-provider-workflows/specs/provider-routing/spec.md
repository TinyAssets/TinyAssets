## ADDED Requirements

### Requirement: Owner provider homes are outside temporary paths
Provider cells SHALL use a private owner-owned home outside `/tmp` for CLI configuration and credential copies, with temporary files separate and other owners inaccessible.

#### Scenario: Shipped Codex discovery and execution
- **WHEN** an owner uses Codex in the production image
- **THEN** the real CLI lists models and completes a real protocol turn through the confined provider path
- **AND** startup does not depend on creating helpers under a temporary home or escaping the OS cell
