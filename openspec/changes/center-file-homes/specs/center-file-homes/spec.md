## ADDED Requirements

### Requirement: Each center file has one authoritative home
The platform SHALL keep `.pause`, `.worker_supervisor.json`, `.worker_supervisor.<id>.json`, `auto_ship_attempts.jsonl`, and `.auto_ship_attempts.jsonl.lock` at `<data_root>/<center>` on the host. Host access SHALL validate the center and refuse symlinks. The box SHALL own `soul.md`; presence checks SHALL use an admitted binding or the configured owner box provider and SHALL fail when neither can provide access.

#### Scenario: pause and cap survive box isolation
- **WHEN** platform scheduling or accounting reads its center state
- **THEN** it reads the same host state written by the platform, without a box fallback.

#### Scenario: a new center has a soul only in its box
- **WHEN** first contact checks completion
- **THEN** it reports complete from the box, regardless of a stale host copy.

### Requirement: Layout defines file homes once
`tinyassets/boxes/layout.py` SHALL be the single definition of `HOST_CENTER_ENTRIES` and `HOST_CENTER_MOVED`, with disjoint sets and predicates for supervisor instances and SQLite companions. Host accessors and the AST guard SHALL consume that definition. The guard SHALL reject moved-name host operations and host-name box helper calls, while retaining the generic mixed-home check. Only box-layout migration, account deletion, `okf/`, and the box read adapter are exempt from moved-name host checks.

#### Scenario: A new mixed-home access is introduced
- **WHEN** a moved literal feeds a host path operation or a host entry feeds a box helper
- **THEN** the guard fails, without extending the known mixed-name allowlist.

### Requirement: Platform owner readers use the box
The platform SHALL list, stat, and read owner content through `owner_box_reads`, using a matching admitted binding validated against the owner or an explicit configured-provider bind. Missing provider configuration SHALL fail loudly. Paths SHALL be validated lexically and containment enforced by the provider, never by resolving stale host owner paths. Paged listings SHALL be consumed correctly.

#### Scenario: Owner content exists only in the box
- **WHEN** the owner file browser, automation brain reader, package plan or publish walk, soul-version allocator, config check, wiki destination check, canon listing, output metric, or prompt inventory reads content
- **THEN** it sees the box content with its existing bounds and error shapes, even when the host content is absent.

#### Scenario: Only stale host content exists
- **WHEN** any routed reader checks a path absent from the box
- **THEN** the host copy does not make that content present.
