# Owner content writers

## Purpose

Preserve owner custody when the daemon publishes content, and prove lossless offline recovery.

## Requirements

### Requirement: Daemon content writers preserve owner custody
Daemon requests and periodic loops SHALL publish visible owner-center entries through owner-content publication, including soul snapshots and their parent directories.

#### Scenario: Soul history directory absent
- **WHEN** a soul edit creates its first snapshot on an admitted center
- **THEN** the snapshot directory and files belong to its owner and subsequent owner measurement succeeds.

#### Scenario: Daemon lifecycle regression guard
- **WHEN** common requests and periodic loop iterations execute on an owner-split fixture
- **THEN** no daemon-owned visible content entry appears.

### Requirement: Nonempty soul history recovery preserves content
Offline owner migration SHALL repair daemon-owned soul history using the recorded owner identity, without removing or changing history bytes.

#### Scenario: Production backup contains daemon-owned history
- **WHEN** the verified backup is restored and migrated under the exclusive layout lock
- **THEN** all soul-history paths and hashes remain identical, their custody is the recorded owner, and a repeated migration changes nothing.

#### Scenario: Unsafe recovery input
- **WHEN** the migration encounters a symlink, hardlink, special file, or active layout lock
- **THEN** it refuses without deleting content.
