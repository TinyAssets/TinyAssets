## ADDED Requirements

### Requirement: Daemon content writers preserve owner custody
Daemon requests and periodic loops SHALL publish visible owner-center entries through owner-content publication, including soul snapshots and their parent directories.

#### Scenario: Soul history directory absent
- **WHEN** a soul edit creates its first snapshot on an admitted center
- **THEN** the snapshot directory and files belong to its owner and subsequent owner measurement succeeds.

#### Scenario: Daemon lifecycle regression guard
- **WHEN** common requests and periodic loop iterations execute on an owner-split fixture
- **THEN** no daemon-owned visible content entry appears, every recorded center passes owner measurement, and subscription/bid/auto-ship outputs exist at their intended paths after creation and subsequent mutations.

### Requirement: Nonempty soul history recovery preserves content
The online content repair SHALL repair daemon-owned soul history using the recorded admission identity, preserving paths, inodes, open writers and bytes. It SHALL leave platform state and already owner-owned entries unchanged, refuse foreign custody and multiply linked repair candidates, and change zero entries on repeat.

#### Scenario: Production backup contains daemon-owned history
- **WHEN** the verified backup is restored and repaired while the real server remains online
- **THEN** all soul-history paths and hashes remain identical, their custody is the recorded owner, and a repeated online content repair changes nothing.

#### Scenario: Unsafe recovery input
- **WHEN** the online content repair encounters a multiply linked repair candidate, special file, or foreign custody
- **THEN** it refuses without deleting content.

#### Scenario: Automatic deployment recovery
- **WHEN** the new production container is healthy
- **THEN** deployment verifies the newest full backup digest, applies online repair, and checks convergence without a manual production step
- **AND** daemon-owned symlinks are skipped and reported without following them; refusals or skipped entries trigger a deploy summary warning and alert without rolling back the healthy deployment

#### Scenario: Unsafe subscribed goal identifier
- **WHEN** a subscription or pool post contains a goal ID that is not a single safe path segment
- **THEN** creation refuses before filesystem access, saved unsafe subscriptions are ignored with a warning, and pool production never joins that ID to the pool root
