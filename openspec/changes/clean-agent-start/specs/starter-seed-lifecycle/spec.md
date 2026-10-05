## ADDED Requirements

### Requirement: Clean personal baseline contains three readable files
The clean profile SHALL provision exactly `soul.md`, `MEMORY.md` and `identity.md` as personal seed files through the existing D10 manifest and receipts. Soul SHALL be short plain persona/voice/boundary text, Identity SHALL have empty name/vibe fields unless supplied by the owner, and Memory SHALL contain no invented facts. It SHALL NOT seed jargon frontmatter, tracking links, companion documents or an empty AGENTS.md merely to complete the personal baseline. Separately installed editable starter harness content SHALL remain separately accounted for in the same seed lifecycle.

#### Scenario: A new account has taught the agent nothing
- **WHEN** a fresh center is provisioned
- **THEN** its personal baseline is Soul, Memory and Identity at the stable paths, with no seeded org chart, founder oath, project list or soul-version directory
- **AND** first contact recognizes the platform center record and no platform LLM call is needed

### Requirement: Clean-profile migration preserves all legacy user content
The clean-profile migration SHALL preserve every legacy companion path and all unreceipted, customized, empty, deleted, linked or unreadable core paths. Only a core file matching a D10 installed receipt with no overriding owner choice SHALL qualify for automatic template replacement. Its manifest SHALL publish no predecessor hashes for these three personal paths, so D10's predecessor-hash branch cannot upgrade unreceipted personal files. It SHALL reuse D10 recovery, notices, tombstones and conditional Undo, SHALL retain exact prior bytes, and SHALL NOT infer deletion authority from removal of a path from the new manifest.

#### Scenario: An old home mixes stock and handwritten content
- **WHEN** an upgrade sees an unreceipted stock-looking Soul, custom Identity, empty Memory, deleted instructions and a handwritten file under soul_versions
- **THEN** all existing bytes and deletions remain unchanged and the notice offers any new templates without blocking activation
- **AND** no companion path is removed even if another path can upgrade safely

#### Scenario: An edit races a receipted stock upgrade or Undo
- **WHEN** an owner edits a core file after its expected hash was recorded
- **THEN** conditional installation, recovery and Undo preserve the new bytes and report the skipped path
- **AND** another owner's receipt or prior-byte reference cannot read or modify this home

#### Scenario: Memory paths differ only in case
- **WHEN** a legacy home has an ambiguous case-colliding Memory path
- **THEN** migration preserves the paths and reports the collision instead of normalizing or replacing them

### Requirement: Sparse personal homes retain existing meaning
The readers SHALL accept the clean plain-markdown profile and absent optional companions while retaining existing purpose and Loop-branch associations. Center existence/first contact SHALL use the authenticated platform center/home record, with a D10 receipt where available, rather than owner-deletable Soul. Governed soul edits SHALL honor existing owner policy and create needed history only on actual edits. For a fresh never-configured home, a versioned D10 candidate template SHALL default to edits by the authenticated owner or agents within current delegated rules, without extra confirmation for already-permitted edits, and SHALL be materialized through D10 on first use. Owner deletion/custom policy SHALL override that default; unresolved policy SHALL be reported without guessing or restoring a deleted file. D7's governed replacement operation SHALL be used after handle retirement.

#### Scenario: Owner deletes Soul
- **WHEN** an owner removes soul.md from an existing registered home, with or without a legacy seed receipt
- **THEN** first contact, converse and work scheduling keep the same home and its Memory/Identity reachable, reading an empty soul
- **AND** no home rebind, replacement provisioning or Soul reseeding occurs

#### Scenario: First governed edit uses the editable starter default
- **WHEN** a fresh home has never selected or deleted a soul-edit policy and its owner-authorized agent makes a permitted edit
- **THEN** D10 materializes the versioned default policy and needed history, and the current governed operation performs the edit without inventing a platform approval gate

#### Scenario: First branch operation on a clean home
- **WHEN** first contact or branch status reads a blank clean Soul
- **THEN** it reads an empty soul without manufacturing a loop, founder identity or companion files

#### Scenario: An existing home carries a Loop branch and soul-edit policy
- **WHEN** the clean-profile readers load that home and the owner makes a governed edit
- **THEN** the Loop-branch association and selected policy remain effective and history is preserved
- **AND** the parser does not rewrite the old files as a side effect of reading
