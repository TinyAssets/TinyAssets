## ADDED Requirements

### Requirement: Daemon writers never create daemon-owned visible names in an owner center

A daemon writer SHALL publish an owner-content top-level name (for example
`config.yaml`) only through the owner-content writer, and SHALL name its own
coordination files (locks) as dotted platform entries opened link-free.

#### Scenario: Config write after cutover keeps owner custody
- **WHEN** the daemon writes a provider assignment projection or engine fields into an owner center
- **THEN** `config.yaml` is published through `universe_files.write_universe_file` and the owner-storage scan still passes

#### Scenario: Status read on a new center creates no visible lock
- **WHEN** a branch-task, auto-ship ledger, subscription or bid execution-log lock is first taken in a center
- **THEN** the lock file is its dotted name (`.branch_tasks.json.lock`, `.auto_ship_attempts.jsonl.lock`, `.subscriptions.json.lock`, `.bid_execution_log.json.lock`), created without following a link, and classified as platform

#### Scenario: Pre-fix daemon-owned visible entries are repaired by the migration rerun
- **WHEN** a center holds a uid-1001 `config.yaml` or visible lock written after the split
- **THEN** rerunning the backed-up `deploy/role_migrate.py` relabels it to the owner, and the owner scan passes
