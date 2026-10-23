## ADDED Requirements

### Requirement: One owner export action produces a portable command center

The system SHALL expose one authenticated owner export action, usable from the UI and by the owner's agent, that creates a consistent downloadable folder or archive. It SHALL contain a versioned bundle manifest, harness and roster files, rules, workspace, wiki and brain files, workflows and schedules, selected memory and UI layouts. It SHALL include a README, license placeholder, secret-excluding `.gitignore`, `.env.example` and a small provider-agnostic local runner using the four tools plus `ta`. Credentials and broker custody SHALL NOT be exported.

#### Scenario: Export runs without the platform

- **WHEN** an owner invokes export and supplies a local model connection to the produced folder
- **THEN** the folder runs without a TinyAssets account and can be imported through the bundle format used for sharing

#### Scenario: Export omits secrets

- **WHEN** an owner with connected model or service credentials exports
- **THEN** the archive contains configuration references and examples but no credential values

#### Scenario: The box assembles the bundle

- **WHEN** the owner invokes export through either surface
- **THEN** the router forwards one owner action to the admitted box, the box reads its own files and assembles the bundle, and shared components do not read owner contents
