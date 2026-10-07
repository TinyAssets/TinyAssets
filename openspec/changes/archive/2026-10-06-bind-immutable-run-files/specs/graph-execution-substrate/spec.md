## ADDED Requirements

### Requirement: Executable file declarations survive ordinary graph reuse
Executable branch serialization, immutable versions, authoring conversion, fork/remix and compiled snapshots SHALL preserve optional file/file-bundle `io_manifest` declarations. Runtime admission MUST validate those declarations against compatible state/input fields and the effective technical limits, rather than treating an unknown state type as file authority. Existing definitions without a manifest MUST preserve their scalar behavior.

#### Scenario: Published file workflow is remixed and executed
- **WHEN** an authorized user publishes, versions and remixes a graph with file and bundle inputs
- **THEN** its executable snapshot retains the same declared field/count/media constraints
- **AND** ordinary run admission validates and binds authorized immutable files for its entry and explicit downstream consumers

#### Scenario: Conflicting or excessive declaration
- **WHEN** a file declaration contradicts state shape or exceeds the runtime's disclosed supported limits
- **THEN** admission fails with an actionable error rather than silently clamping or falling back to untyped input

### Requirement: Every admitted origin uses the same file binding service
Direct, queued, triggered, nested, versioned, resumed and delivery-origin execution SHALL route file inputs through the generic immutable binding service after existing execution-authority checks. A legacy run lacking trusted owner evidence MUST refuse file admission without changing unrelated scalar execution. Post-exit workspace capture and sandbox read/materialization adapters MUST use trusted context and honor cancellation before publishing effects or returning chunks.

#### Scenario: Child explicitly receives a parent file
- **WHEN** an authorized nested invocation maps a parent's valid bound file into a child input
- **THEN** the child receives its own authorized binding to the immutable owned object
- **AND** guessing a parent's ID without that mapping fails

#### Scenario: Foreground and delivery executions consume equivalent input
- **WHEN** the same legitimate file-input definition is admitted directly and through an approved receiver
- **THEN** both entry and downstream consumers use the same file-read/materialization semantics
- **AND** delivery admission supplies independent receiver custody without changing the user's saved graph

### Requirement: Initial dispatch recovery uses explicit admitted origin
The existing run-owned admission envelope SHALL retain the trusted immutable
origin kind/version and strictly typed dispatch options needed to reconstruct
initial execution. Recovery MUST select only statically allowlisted adapters,
revalidate their current authority and original correlation, and use the same
run guard/start CAS. It MUST NOT infer origins from file bindings or serialize
callbacks, credentials or alternative input bodies. Legacy/unknown provenance
and started/ambiguous execution MUST remain held without automatic effect replay.

#### Scenario: Admitted run survives restart before dispatch
- **WHEN** a known-origin run was atomically admitted but never started
- **THEN** recovery can dispatch that same run with its original typed options
- **AND** current cancellation, owner/source/provider authority and start CAS still gate execution

#### Scenario: Origin is absent or execution may already have started
- **WHEN** provenance is unknown or the durable start marker is already present
- **THEN** recovery reports a held state and does not guess an adapter or replay work
