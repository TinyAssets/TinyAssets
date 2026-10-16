## ADDED Requirements

### Requirement: Each scoped file has one authoritative home
The platform SHALL keep `.pause`, `.worker_supervisor.json`, `.worker_supervisor.<id>.json`, `auto_ship_attempts.jsonl`, and `.auto_ship_attempts.jsonl.lock` on the host. Readers and writers SHALL use center_platform_files. Soul presence SHALL use the owner's box without consulting a stale host soul.

#### Scenario: Pause and heartbeat agree across surfaces
- **WHEN** control_daemon pauses a center or the scheduler publishes a heartbeat
- **THEN** scheduler dispatch and API status observe the same host state.

#### Scenario: A new center has its soul only in the box
- **WHEN** first contact checks completion
- **THEN** it reports complete from the box and does not repeat creation.

### Requirement: Layout defines file homes once
boxes/layout.py SHALL define disjoint HOST_CENTER_ENTRIES and HOST_CENTER_MOVED sets, name constants and predicates including worker instances and SQLite companions. Inventory SHALL derive its classification from these sets. dispatcher_config.yaml, activity.log and status.json SHALL be classified as moved; rerouting their readers remains outside this change. The scoped guard SHALL reject direct host IO of soul.md and box IO of host names, and require scoped host IO to use the host adapter. Existing wave-2 mixed names MAY remain explicitly allowlisted.

#### Scenario: A scoped file gains a second home
- **WHEN** a soul literal feeds a host path operation or a scoped host name bypasses its adapter
- **THEN** the guard fails.

### Requirement: Requested center and admitted owner remain authoritative
Host access SHALL validate a single safe center name under the caller's base and refuse linked stubs. Soul access SHALL refuse a missing or linked stub before any provider bind, use a matching admitted session or broker admitted_owner, and create nothing for an unadmitted center. Missing provider configuration SHALL fail loudly for admitted centers.

#### Scenario: A links to B
- **WHEN** a request names A whose host stub links to B
- **THEN** soul reads and pause, heartbeat and ledger operations refuse without substituting B's name.

#### Scenario: Missing or unadmitted center
- **WHEN** presence is checked without a proven host stub and owner
- **THEN** soul presence is false without binding or creating content; a missing host center reads as not paused, no heartbeats and an empty ledger.

### Requirement: Ledger growth does not block recording
Recording SHALL scan duplicate IDs across the entire ledger with bounded row memory independently of total size.

#### Scenario: Existing ledger exceeds sixteen MiB
- **WHEN** a new attempt is recorded
- **THEN** append succeeds and duplicate IDs at both the beginning and end remain rejected.
