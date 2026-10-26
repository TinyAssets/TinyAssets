## ADDED Requirements

### Requirement: One owner export action produces a portable command center

The system SHALL expose one authenticated owner export action, usable from the UI and by the owner's agent, that creates a consistent downloadable folder or archive. Its versioned **export manifest** SHALL define the bundle paths and hashes and be consumed by `tinyassets/boxes/boxd.py::import_bundle` for sharing/import. The bundle SHALL contain harness and roster files, rules, workspace, wiki and brain files, workflows and schedules, selected memory and UI layouts. It SHALL include a README, license placeholder, secret-excluding `.gitignore`, `.env.example` and a small provider-agnostic local runner using the four tools plus `ta`. Credentials and broker custody SHALL NOT be exported.

#### Scenario: Export runs without the platform

- **WHEN** an owner invokes export and supplies a local model connection to the produced folder
- **THEN** the folder runs without a TinyAssets account and its export manifest/archive round-trips through `boxd.py::import_bundle`

#### Scenario: Export omits secrets

- **WHEN** an owner with connected model or service credentials exports
- **THEN** the archive contains configuration references and examples but no credential values

#### Scenario: The box assembles the bundle

- **WHEN** the owner invokes export through either surface
- **THEN** the router forwards one owner action to the admitted box, the box reads its own files and assembles the bundle, and shared components do not read owner contents
