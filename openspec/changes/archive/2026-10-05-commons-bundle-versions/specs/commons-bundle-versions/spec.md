## ADDED Requirements

### Requirement: Author-bound bundle identity
The platform SHALL assign a stable bundle identity to repeated publication of the same author's command center and SHALL reject publication under another author's bundle. A remix SHALL receive a distinct bundle.

#### Scenario: Republishing
- **WHEN** an author republishes the same source command center, including after renaming it
- **THEN** a new immutable definition becomes the bundle's current version
- **AND** a different author's publication cannot supersede it

### Requirement: Current catalogue and immutable history
The commons SHALL list only the current version per bundle before applying filters and pagination, and SHALL expose traversable prior definition IDs without mutating old content or installations.

#### Scenario: Existing recipient
- **WHEN** a new version is published
- **THEN** exact old definition reads and installed copies retain their content
- **AND** recipient updates remain governed by existing explicit opt-in policies and release chains

#### Scenario: Install awaiting consent
- **WHEN** a recipient has previewed an exact old definition and the publisher republishes
- **THEN** the recipient can still confirm the pinned old version
- **AND** catalogue metadata does not alter the immutable consent digest

### Requirement: Public discovery
Published bundles SHALL be discoverable across accounts without an implicit current-author filter.

#### Scenario: Second account
- **WHEN** another account browses the commons without an author filter
- **THEN** it can discover the publisher's current bundle and read its history

### Requirement: Evidence-based legacy grouping
The platform SHALL backfill bundle membership only from authenticated platform publication evidence and SHALL leave unknown provenance ungrouped.

#### Scenario: Existing duplicate listings
- **WHEN** activated publication pins and exact definition receipts establish the same author and source screen
- **THEN** their unchanged immutable definitions become versions of one bundle
- **AND** a conflicting explicit pin owner refuses grouping, while pre-owner-column pins use their exact definition's authenticated author
- **AND** definition creation order determines the current version even when receipt finalization was delayed
