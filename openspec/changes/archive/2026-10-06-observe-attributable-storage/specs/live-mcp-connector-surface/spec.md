## ADDED Requirements

### Requirement: Legacy storage telemetry labels freshness and accounting scope
The existing storage-utilization status SHALL preserve prior response keys while
reporting scan-start UTC observation time, elapsed observation age, cache reuse
TTL, filesystem denominator and partial enumerated daemon accounting scope.
It SHALL NOT represent the largest listed subsystem as the largest host consumer
or as complete owner-attributed or billable storage.

#### Scenario: Cached status follows a storage change
- **WHEN** storage status reuses a cached measurement
- **THEN** its original observation time remains unchanged and elapsed age is refreshed
- **AND** declared cache TTL means reuse after scan completion, not atomicity or a hard maximum observation age

#### Scenario: Subsystems do not cover the filesystem
- **WHEN** status returns filesystem pressure beside enumerated subsystem bytes
- **THEN** it declares the filesystem-containing-data-root scope and formula `1 - volume_bytes_free / volume_bytes_total`
- **AND** caveats identify partial coverage, excluded Docker images/unlisted paths, mixed universe/root scope and no ownership attribution without exposing additional paths or identities

#### Scenario: Filesystem pressure cannot be measured
- **WHEN** the disk probe fails or reports zero total capacity
- **THEN** additive availability is unavailable and prior numeric keys remain compatible rather than constituting healthy evidence

### Requirement: Storage observations distinguish measurable footprint from complete attribution
Existing admin-only resource status SHALL report bounded metadata-only logical
file-footprint observations separately from unavailable complete attributed
storage. It SHALL preserve existing authority, policy and accounting without
new databases, record mutations, quota changes or file-content reads.

#### Scenario: Authorized owner reads a footprint
- **WHEN** the current universe admin reads status and safe traversal succeeds
- **THEN** the response reports capture time, logical regular-file bytes and categories for permanent workspace, provider runtime, other universe files and lease-attributed scratch
- **AND** it discloses no paths, file names, lease identities or other owners' usage

#### Scenario: Attribution is incomplete
- **WHEN** local footprint is observed but shared-root records and unowned scratch cannot be attributed
- **THEN** those exclusions remain explicit and complete attributed storage remains unavailable rather than equaling the measured footprint

#### Scenario: Walk cannot cover its scope
- **WHEN** traversal meets a link, unsafe path, inaccessible or changing file, unsupported host or work bound
- **THEN** coverage is partial or unavailable with a sanitized reason, not fabricated complete zero

#### Scenario: Cached measurement does not cache authority
- **WHEN** a cached snapshot exists but the caller no longer has current admin authority
- **THEN** it is not returned
- **AND** authorized cache hits retain the original measurement time and declared staleness bound

#### Scenario: Measurements do not create a scan stampede
- **WHEN** simultaneous status requests arrive
- **THEN** same-scope work shares one bounded scan and distinct-scope concurrency is bounded without changing user-work quotas
