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

#### Scenario: Online recovery preserves existing content
- **WHEN** a center holds daemon-owned visible content after the split
- **THEN** the backed-up online content repair relabels pinned inodes using recorded admission identities, preserves every path and byte, changes nothing on repeat, and leaves the daemon running with unchanged privileges

## MODIFIED Requirements

### Requirement: Wiki creation preserves owner custody
Wiki directories SHALL be created by the admitted owner's content cell. Existing empty and nonempty daemon-owned categories SHALL use the single online operator content repair.

#### Scenario: New wiki category followed by a tool
- **WHEN** a wiki write creates a category after migration
- **THEN** its directories and files belong to the owner and subsequent storage observation and tool execution succeed.

#### Scenario: Existing empty category debris
- **WHEN** online repair finds an empty daemon-owned category from the old writer
- **THEN** it relabels the existing inode without changing root labels or serving capabilities.

#### Scenario: Recovery races and user bytes
- **WHEN** a category is nonempty, a symlink, or concurrently replaced by its owner
- **THEN** recovery never follows the symlink, never removes nonempty content, and never overwrites the owner's replacement.
