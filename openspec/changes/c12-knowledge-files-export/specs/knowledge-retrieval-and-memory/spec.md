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
