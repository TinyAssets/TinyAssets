## ADDED Requirements

### Requirement: Each center file has one authoritative home
The platform SHALL keep `.pause`, `.worker_supervisor.json`, `.worker_supervisor.<id>.json`, `auto_ship_attempts.jsonl`, and `.auto_ship_attempts.jsonl.lock` at `<data_root>/<center>` on the host. Host access SHALL validate the center and refuse symlinks. The box SHALL own `soul.md`; presence checks SHALL return absent for an unadmitted center or missing host stub without binding or materializing content. Content reads SHALL refuse these centers.

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
The platform SHALL list, stat, and read owner content through `owner_box_reads`, using a matching admitted binding or the broker mapping `admitted_owner(data_root, center=...)` before a configured-provider bind. The requesting actor and `universe_owner` SHALL NOT select box identity; existing ACL/visibility gates SHALL retain authorization. Missing provider configuration SHALL fail loudly. Paths SHALL be validated lexically and containment enforced by the provider, never by resolving stale host owner paths. Paged listings SHALL be consumed correctly.

#### Scenario: Owner content exists only in the box
- **WHEN** the owner file browser, automation brain reader, package plan or publish walk, soul-version allocator, config check, wiki destination check, canon listing, output metric, or prompt inventory reads content
- **THEN** it sees the box content with its existing bounds and error shapes, even when the host content is absent.

#### Scenario: Only stale host content exists
- **WHEN** any routed reader checks a path absent from the box
- **THEN** the host copy does not make that content present.

### Requirement: Multi-center surfaces isolate box reads
Listings SHALL perform no per-center box reads. Box-derived fields SHALL be null with an explicit unavailable reason directing callers to single-center inspect/status. Other multi-center scans SHALL continue after a per-center box refusal and report the affected derived field as unavailable; single-center content reads SHALL raise on refusal.

#### Scenario: Listing includes an unadmitted center
- **WHEN** a discoverable center lacks broker admission or its provider refuses reads
- **THEN** the listing still returns every visible center without binding any box.

### Requirement: Writers determine the authoritative home
`dispatcher_config.yaml`, `activity.log`, and `status.json` SHALL belong to BOX, with platform readers using `owner_box_reads`. `.runtime_status.json` SHALL remain HOST. The branch deletion guard SHALL read soul-declared loop dependencies through the admitted box and expose unreadable dependency scans explicitly.

#### Scenario: Box-only operational state and dependency
- **WHEN** dispatcher policy, activity, status, or a declared loop exists only in the box
- **THEN** platform readers observe it and ignore stale host copies.

### Requirement: Host reads use the caller's base
Host helpers SHALL accept the caller's universe path/base, validate a single safe center component and its parent against that base, and refuse symlinks. Missing host centers SHALL read as not paused, no heartbeats, and an empty ledger.

#### Scenario: Worker uses an explicit base
- **WHEN** a worker uses a base other than global data_dir
- **THEN** pause and heartbeat operations use the worker's base; missing centers do not fail reads.

#### Scenario: A host center stub links to another center
- **WHEN** a request names A whose host stub links to B
- **THEN** box reads and host writes refuse before binding; the authorization key is never resolved into B.

### Requirement: Reads do not scaffold
Page reads SHALL use box content without a session binding and SHALL NOT scaffold or write. An absent page or wiki SHALL return a clean not-found response. Inspect SHALL route notes through the box and report a refused notes field as null with an unavailable reason while preserving the rest of the response.

### Requirement: Ledger growth does not block recording
Recording SHALL check duplicate IDs across the entire ledger with bounded memory, independent of total file size. IDs before any tail window SHALL remain protected.
