## ADDED Requirements

### Requirement: A verified checkpoint precedes agent execution
Every interactive or scheduled agent turn SHALL have a verified pre-turn checkpoint through the same opaque-volume lifecycle used by S27, with no separate per-tool snapshot path.

#### Scenario: Start a queued turn
- **WHEN** the in-box scheduler queues a turn
- **THEN** work drains, the box flushes/exits and fully detaches, and the existing lifecycle copies, uploads, verifies and publishes its snapshot before execution resumes
- **AND** the turn's tools share that checkpoint; reuse is allowed only for the unchanged detached volume's latest verified snapshot

#### Scenario: Checkpoint failure or concurrent wake
- **WHEN** snapshotting fails, storage is exhausted, the process restarts or another wake arrives during the handoff
- **THEN** no queued turn executes without its verified checkpoint; the same lifecycle intent resumes without losing or duplicating the turn or publishing after erasure

### Requirement: Undo shares backup retention and pins
Undo SHALL use the single C2 catalog and latest-seven retention rule, preserving supported recovery references and the latest pre-turn checkpoint until a replacement is verified.

#### Scenario: Sleep snapshots consume the retention window
- **WHEN** newer backup snapshots would evict the current pre-turn checkpoint or a supported recovery point
- **THEN** existing pin-aware retention preserves those references and deletes only unreferenced snapshots; the app lists retained points only

#### Scenario: Free-tier storage has insufficient headroom
- **WHEN** the 5 GiB live volume plus retained snapshots and temporary copies exceed available storage
- **THEN** checkpoint creation refuses visibly before agent execution, without silently shrinking the live allowance, deleting pinned backups or creating a second storage path

### Requirement: Undo requires owner confirmation and restores a coherent volume
Undo SHALL require protected owner confirmation bound to a retained snapshot and current volume generation, drain running work and restore the complete volume through the existing C2 lifecycle.

#### Scenario: Owner confirms while a turn runs
- **WHEN** the owner confirms Undo in the app after seeing that files, DB, history and later edits rewind but external effects do not
- **THEN** new work stops, the running turn drains or completes existing owner cancellation, and restore waits for complete detach
- **AND** the verified image and new generation commit before restart; platform admission, credentials, billing, spending and egress policy remain unchanged

#### Scenario: Agent asks or stale confirmation arrives
- **WHEN** an agent requests undo, or confirmation names a missing snapshot, earlier generation or non-admitted owner
- **THEN** an agent request can only raise the existing owner approval card; invalid confirmation cannot restore

### Requirement: Rewind never automatically replays restored work
The restored box SHALL validate its databases locally, invalidate restored pending approvals and pause queued turns/schedules before admitting owner work; shared services SHALL NOT inspect owner contents.

#### Scenario: Interrupted restore and pending work
- **WHEN** restore resumes after interruption and the snapshot contains a queued turn, due schedule or approval
- **THEN** the existing intent completes the image/generation commit, in-box validation runs, and nothing queued executes until explicit owner resumption
- **AND** earlier-generation operations refuse, fresh operations use the new generation, and the undo result is recorded inside the restored box
