## ADDED Requirements

### Requirement: Owner knowledge files are authoritative

The system SHALL keep each owner's durable knowledge in editable files inside that owner's box. New centers SHALL receive an OKF default bundle with `okf_version`, Markdown frontmatter and links; a custom organization SHALL retain file authority. Knowledge writes SHALL update files first, and no database or index SHALL overrule a conflicting file.

#### Scenario: File edit survives restart

- **WHEN** an owner edits a knowledge file and the box restarts
- **THEN** knowledge reads reflect that file without restoring an older database value

#### Scenario: Existing owner converts

- **WHEN** an existing owner is moved to file authority
- **THEN** conversion and verification run inside that owner's box, preserve existing knowledge, and either commit for that owner or leave its prior serving version intact

### Requirement: Knowledge indexes are disposable inside the box

The system SHALL derive graph, text and vector retrieval indexes from the owner's knowledge files inside the box. It SHALL rebuild missing, corrupt or stale indexes from those files and SHALL reuse the LanceDB connection for the same path. Shared components SHALL hold no owner-content path or knowledge business logic.

#### Scenario: Rebuild after index loss

- **WHEN** the owner's derived indexes are removed
- **THEN** rebuilding them from files restores equivalent knowledge results without consulting a second canonical store

#### Scenario: Shared layer has no knowledge path

- **WHEN** routing, broker egress or waking serves an owner operation
- **THEN** those components use identity, bound box, credentials or wake time only and cannot traverse the owner's knowledge files

### Requirement: Selected memory uses the same file authority

The system SHALL store durable selected memory and mini-brain knowledge in the owner's file tree and SHALL retire second canonical writes from `memory/` and `daemon_brain.py`. Operational history and transactional records SHALL remain in their proper owner stores rather than being misclassified as knowledge.

#### Scenario: Memory survives a rebuild

- **WHEN** a box restarts with its memory indexes removed
- **THEN** selected memory is recovered from owner files and no old memory store must be read as authority

## REMOVED Requirements

### Requirement: Path-Explicit Knowledge Graph With Scope Columns

**Reason**: A graph database cannot remain canonical knowledge after files become authority.
**Migration**: Derive the graph in-box from owner files; retain only index schema needed by that derived reader.

### Requirement: Scope-Isolation Defense-In-Depth On Retrieval

**Reason**: Cross-owner row filtering and shared singleton bleed are not valid isolation mechanisms in the C6 target shape.
**Migration**: Run retrieval against one admitted owner's box and derive indexes only from its files; remove shared cross-owner read paths.

### Requirement: Host-Local Daemon Learning Wiki With Bounded Caps

**Reason**: Host-local owner knowledge conflicts with the owner-volume boundary and the canonical file tree.
**Migration**: Keep owner wiki files and any bounded compaction inside the box.

### Requirement: Soul-Scoped Host-Local Mini-Brain Store

**Reason**: The mini-brain's separate canonical store duplicates owner knowledge.
**Migration**: Convert durable mini-brain entries to owner files, then remove the old store and writer.

### Requirement: Explicit Mini-Brain Review And Wiki-Promotion Lifecycle

**Reason**: Its separate promotion path can create a second authority.
**Migration**: Keep owner review behavior while writing accepted knowledge directly to owner files.

### Requirement: Per-Daemon Mini-Brain Retrieval And Bounded Injection

**Reason**: Retrieval must draw from owner files and their derived indexes.
**Migration**: Preserve bounded injection using in-box file-derived retrieval.

### Requirement: Caller-Owned Mini-Brain Quality Replay

**Reason**: Replay cannot write an independent mini-brain corpus.
**Migration**: Keep quality evaluation where needed, with durable results in owner files or operational history as appropriate.

### Requirement: Mini-Brain Dispatch Hints And Status Surfaces

**Reason**: These surfaces currently assume the retired mini-brain store.
**Migration**: Derive status from owner files and their indexes.

### Requirement: OKF v0.1 export reads only the curated wiki source set

**Reason**: A wiki-only exporter does not fulfill the single complete command-center export.
**Migration**: Fold wiki conversion into the in-box bundle assembler.

### Requirement: OKF export normalizes concepts, links, reserved files, and evidence

**Reason**: The standalone wiki-only exporter is retired.
**Migration**: Apply relevant OKF normalization within the single bundle export action.
