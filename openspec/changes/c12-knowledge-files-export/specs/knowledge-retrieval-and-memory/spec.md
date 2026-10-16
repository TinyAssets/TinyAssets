## MODIFIED Requirements

### Requirement: OKF v0.1 export reads only the curated wiki source set

The one OKF wiki writer SHALL consume only owner-selected, preview-bound and
scrubbed bytes through the protected command-center transfer door. It SHALL
convert sorted Markdown beneath `wiki/pages/`, excluding every `soul.md`, source
`index.md` and `log.md`, and material under `drafts/`, `raw/`, and `daemon-wiki`.
These privacy exclusions SHALL also apply to nested wiki trees. The writer
SHALL perform no host-path I/O, add no MCP action and never mutate source files.

#### Scenario: Curated pages export without private material
- **WHEN** selected wiki pages include promoted pages and private wiki paths
- **THEN** private paths cannot be selected through the owner door
- **AND** only the selected non-reserved promoted page bytes become concepts.

#### Scenario: Unselected material cannot enter generated metadata
- **WHEN** a selected page links to an unselected page
- **THEN** the unselected page's title and content do not enter the index or log
- **AND** the link is reported as unresolved.

#### Scenario: Export target cannot be nested in the source wiki
- **WHEN** export runs through the owner transfer door
- **THEN** the wiki writer returns in-memory bytes and accepts no filesystem target directory
- **AND** the source wiki cannot be overwritten by an export target.

#### Scenario: Generated content conflicts with selected content
- **WHEN** a selected path collides with a generated wiki path, or a selected curated page has a reserved name
- **THEN** export refuses instead of overwriting or silently discarding selected bytes.

### Requirement: OKF export normalizes concepts, links, reserved files, and evidence

Each exported concept SHALL have `type`, `title`, `timestamp`, and
`workflow_original_path` metadata, using the current frontmatter, heading,
slug, or explicit export-time fallbacks and preserving non-empty top-level source fields
recognized by the lightweight parser under `workflow_` keys; this is not
lossless YAML round-tripping. The exporter MUST convert resolvable wikilinks to
absolute bundle links, render unresolved wikilinks as plain labels while
reporting them, write root `index.md` with `okf_version: "0.1"`, write dated
root `log.md`, and return a conformance report with concepts, exclusions,
unresolved links, reserved-file validation, issues, and counts. `conformant`
SHALL be the exporter's local structural flag only: it validates generated
reserved-file shapes and parseable concept frontmatter with non-empty `type`,
not complete upstream OKF conformance or canonical-store authority. Unresolved
links, privacy exclusions, and source pages named `index.md` or `log.md` SHALL
be reported but SHALL NOT independently make `conformant` false.

#### Scenario: Metadata and links are normalized into the bundle

- **WHEN** a promoted page has partial frontmatter and links to another exported page
- **THEN** the concept receives required fallback metadata, recognized non-empty top-level source fields are retained under `workflow_` keys, and the link becomes an absolute bundle link

#### Scenario: Unresolved links remain readable and evidenced

- **WHEN** a promoted page links to a target absent from the export set
- **THEN** the rendered body contains the readable label without a link and the report identifies the source and unresolved target

#### Scenario: Bundle reserved files are regenerated

- **WHEN** export completes with zero or more concepts
- **THEN** root `index.md` and `log.md` are written in the current OKF v0.1 shapes and their validation results appear in the report

#### Scenario: Reserved source-name issue is non-fatal

- **WHEN** a promoted source page itself is named `index.md` or `log.md` and no other conformance issue exists
- **THEN** that writer reports the omitted page while the local report remains conformant
- **AND** the owner export door refuses that selection rather than silently dropping it

#### Scenario: Local conformance is deliberately narrow

- **WHEN** a bundle has valid generated reserved files and parseable concept frontmatter with non-empty types but also has unresolved links or privacy exclusions
- **THEN** the local `conformant` flag remains true and does not claim complete upstream OKF or canonical-store conformance

## ADDED Requirements

### Requirement: Disposable canonical consumer indexes
Daemon, episodic, graph, promise, draft-version and temporal consumers SHALL
accept an explicitly admitted canonical source and SHALL NOT open a legacy
database as fallback. SQL structures MAY exist only as disposable in-memory
indexes rebuilt from the current owner-box revision using trusted repository DDL.

#### Scenario: Owner edits or forgets a canonical record
- **WHEN** an owner changes or removes a canonical record between operations
- **THEN** the next consumer operation rebuilds its index from that revision
- **AND** a stale legacy file cannot restore the removed value.

#### Scenario: Ambiguous or unreviewed namespaces
- **WHEN** a consumer is given an unbound source, unreviewed NULL scope or host path
- **THEN** access refuses without inferring ownership or reading a legacy store.

### Requirement: Atomic canonical consumer operations
Each public operation SHALL use one coherent snapshot and commit related mutations
atomically through canonical CAS before returning success. Failed operations SHALL
discard staged index changes. Unknown columns and metadata SHALL survive edits;
rows SHALL retain reviewed namespaces. Closed sessions, mismatched current owner
contexts and stale placement handles SHALL refuse access.

#### Scenario: Conflicting writes or interrupted commit
- **WHEN** canonical data changes during a consumer mutation or a provider refuses it
- **THEN** the entire mutation refuses without publishing a partial related-table update
- **AND** a retained pending transaction may only retry its original snapshot and operation ID.

### Requirement: Reviewed namespace migration census
Migration SHALL enumerate every declared knowledge namespace under a verified
snapshot. Assignments SHALL name an owner and center with snapshot-bound review
provenance and current authority verification. Unassigned rows SHALL remain in
restricted operator custody outside owner retrieval and export. Any unresolved
row SHALL block full cutover and source deletion; an empty census MAY clear
that ownership blocker without inventing a binding.

#### Scenario: Writer identity is mistaken for ownership
- **WHEN** a project has rows but no reviewed namespace binding
- **THEN** neither updated_by nor billing attribution assigns an owner
- **AND** its rows remain in restricted custody and are absent from owner projections.

#### Scenario: A reviewed binding is stale or contradictory
- **WHEN** the backup hash differs, two bindings claim a namespace, or current center authority rejects its owner
- **THEN** migration refuses before writing owner knowledge.

#### Scenario: Restore follows a newer deletion
- **WHEN** an offline knowledge restore is prepared after an owner deletion fence advanced
- **THEN** it does not restore identity, admission or deletion authority
- **AND** production activation must retain current C6 deletion fences and receipt journals.

### Requirement: Canonical source record adapter
Source-record readers and writers SHALL bind the configured owner BoxProvider
and use only committed OKF revisions. They SHALL fail on an absent provider or
unmigrated source, without opening legacy SQLite. Updates SHALL preserve unknown
columns and metadata, validate declared scope and primary-key identities, and
commit related tables atomically with the original snapshot and operation ID.

#### Scenario: An owner edits canonical prose
- **WHEN** the record adapter reads after a Markdown body edit
- **THEN** the edited body is the row's only prose value and no legacy database is consulted.

#### Scenario: A write crosses scope or loses a race
- **WHEN** an update changes a scope column or uses a stale owner snapshot
- **THEN** the write refuses without replacing committed knowledge.

### Requirement: Canonical project memory consumer
Project memory requests SHALL use an authenticated owner-box binding and a
reviewed project namespace. Current values and append-only history SHALL commit
together in canonical files. Stable request IDs SHALL recover committed writes
without adding another version. Missing providers, migrations or bindings SHALL
refuse; no request SHALL open the legacy global project database.

#### Scenario: A project write loses its response
- **WHEN** the same request ID is retried after its canonical commit
- **THEN** the original version is returned without another history row
- **AND** reuse for different content refuses.

### Requirement: Offline verified restore preparation
Restore preparation SHALL use a verified backup and a committed owner snapshot,
write only a new destination, and retain every unselected backup row. It SHALL
verify restored scoped values before returning a receipt. It SHALL explicitly
identify derived stores needing rebuild and SHALL NOT activate restored state.

#### Scenario: Restore input is changed or a destination already exists
- **WHEN** backup checksums differ, scoped identities are invalid, or the target exists
- **THEN** restore refuses without overwriting the backup or existing destination.

#### Scenario: A canonical key collides with another owner
- **WHEN** a canonical record conflicts with a preserved row even under a schema REPLACE or IGNORE policy
- **THEN** restore aborts and rolls back the destination transaction
- **AND** no successful receipt is issued and both original owners' rows remain.

### Requirement: Durable forgotten identities
Canonical deletion SHALL retain a tombstone with typed identity and provenance.
Disposable indexes SHALL preserve verified backup integer counters and later
deleted identities. Normal writes SHALL NOT revive a forgotten identity.

#### Scenario: Forgotten identities survive disposable index rebuilds
- **WHEN** an owner forgets a canonical record and its index is rebuilt
- **THEN** the current manifest SHALL retain a deletion tombstone and its typed identity/provenance
- **AND** automatic integer identities SHALL advance beyond retained deletion identities
- **AND** a write attempting to revive the forgotten identity SHALL refuse before publication
