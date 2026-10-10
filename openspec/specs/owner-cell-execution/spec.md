# Owner cell execution

## Purpose

Keep background and interactive execution bound to the same admitted owner and filtered network.

## Requirements

### Requirement: Cell execution resolves the admitted owner
Execution cells SHALL resolve the exact command center's owner from the broker admission log and admit only that owner or that center's internally bound run actor.

#### Scenario: Background and delegated execution
- **WHEN** a background graph, automation or sub-agent executes as its center actor
- **THEN** its code, HTTP and tool cells execute with the same admitted owner identity as interactive execution.

#### Scenario: Cross-owner refusal
- **WHEN** a foreign owner, foreign center actor, unbound actor or retired center requests a cell
- **THEN** admission fails before execution, without allocating an identity or changing ownership.

#### Scenario: HTTP effect under a center actor
- **WHEN** a center actor emits an authenticated HTTP effect
- **THEN** the same resolver supplies its admitted owner to the existing broker grant check, and foreign grants remain refused.

### Requirement: Tool networking retains filtered egress
Tool bash SHALL reach permitted HTTP and HTTPS endpoints through the pinned checking proxy while retaining network isolation.

#### Scenario: Production-copy acceptance
- **WHEN** acceptance restores and migrates the cutover snapshot and starts the real server
- **THEN** background and automation code/HTTP graphs, a sub-agent, Patch Request Intake through file_issue with stub GitHub, and tool bash curl to a local fixture succeed; cross-owner and private-destination requests remain refused.

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
