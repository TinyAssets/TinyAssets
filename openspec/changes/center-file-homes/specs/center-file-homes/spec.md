## ADDED Requirements

### Requirement: Each center file has one authoritative home
The platform SHALL keep `.pause`, `.worker_supervisor.json`, `.worker_supervisor.<id>.json`, `auto_ship_attempts.jsonl`, and `.auto_ship_attempts.jsonl.lock` at `<data_root>/<center>` on the host. Host access SHALL validate the center and refuse symlinks. The box SHALL own `soul.md`; presence checks SHALL use an admitted binding or the configured owner box provider and SHALL fail when neither can provide access.

#### Scenario: pause and cap survive box isolation
- **WHEN** platform scheduling or accounting reads its center state
- **THEN** it reads the same host state written by the platform, without a box fallback.

#### Scenario: a new center has a soul only in its box
- **WHEN** first contact checks completion
- **THEN** it reports complete from the box, regardless of a stale host copy.
