## ADDED Requirements

### Requirement: Undo points use automatic sleep and explicit owner checkpoints
Undo SHALL reuse C2 sleep snapshots and owner-requested "save a restore point" checkpoints through the same S27 opaque-volume lifecycle, with no per-turn or per-session checkpoint or N6 pause between messages.

#### Scenario: Conversation continues
- **WHEN** interactive or scheduled turns start on an awake box
- **THEN** N6 introduces no checkpoint stop; existing C2 sleep and 24-hour durability behavior remain unchanged

#### Scenario: Owner saves a restore point
- **WHEN** the owner explicitly requests a checkpoint
- **THEN** in-box work admission closes, work drains/cancels, and ordinary C2 sleep flushes, detaches, copies, uploads, verifies and publishes the snapshot
- **AND** failed publication reports pending/failure and retries the same intent without inventing a usable point

### Requirement: Undo shares seven-snapshot retention and the C10 budget
Undo SHALL use the single C2 catalog, latest-seven retention and existing recovery references, without shared turn-checkpoint pins, another archive, reduced 5 GiB allowance or spending above C10's $15/month fleet cap.

#### Scenario: Manual checkpoints and retention
- **WHEN** checkpoints or pre-restore snapshots are published
- **THEN** they occupy ordinary retention slots; existing recovery references and the selected source of a pending restore survive pruning
- **AND** the app lists retained points only; completed operations release temporary source protection

#### Scenario: Storage or transfer budget is exhausted
- **WHEN** an optional checkpoint or restore lacks capacity for fleet retained/pinned snapshots, temporary publication/restore copies and verification transfers within C10
- **THEN** the action refuses visibly before replacing current state, without deleting protected references or shrinking the 5 GiB volume
- **AND** accounting includes about 35 GiB for seven full images, about 40 GiB during publication plus pins, and about 20 GiB local worst-case staging per full owner plus overhead

### Requirement: Only protected owner confirmation reaches restore
Undo SHALL take the N10 protected owner-session route to the broker and existing `VolumeLifecycle.restore`, bound to the authenticated owner, retained snapshot ID and expected volume generation; the box SHALL NOT possess confirmation authority.

#### Scenario: Protected restore path
- **WHEN** the owner confirms `POST /app/owner/restore` after seeing the whole-volume scope, saved-current-state retention and irreversible external effects
- **THEN** the router derives owner identity, validates same-origin JSON and the protected session, and forwards opaque fields/proof on its protected broker channel
- **AND** the broker revalidates the live owner session, admission, snapshot ownership and generation under the lifecycle lock; no proof enters the box and no shared service reads owner contents

#### Scenario: Agent or stale confirmation
- **WHEN** a box/agent calls restore, supplies an owner ID, or presents a forged, expired, wrong-owner or stale-generation confirmation
- **THEN** restore refuses; an agent can only propose an owner approval request

### Requirement: Publish current state before restoring
Undo SHALL drain/cancel running work and publish the pre-restore sleep snapshot before installing the selected image, preserving current state as an ordinary retained restore point.

#### Scenario: Publication fails or restore restarts
- **WHEN** pre-restore publication fails or the lifecycle process restarts
- **THEN** the durable intent resumes publication before replacement; failure preserves current state, and retention cannot delete the selected source
- **AND** only verified publication allows restore to install the image and commit its new generation before restart; platform authority remains current

#### Scenario: Undo the undo
- **WHEN** the owner selects the retained pre-restore snapshot
- **THEN** the same protected restore flow saves the current state first and restores the chosen whole volume; no permanent redo pin is added

### Requirement: Waiting calls and restored work never replay automatically
The in-box handler SHALL settle interrupted interactive calls before shutdown; restored startup SHALL validate databases locally, invalidate pending approvals and pause restored queues/schedules.

#### Scenario: Interactive request spans restore
- **WHEN** an app/MCP call is waiting for a turn during restore
- **THEN** it finishes before shutdown or receives `interrupted_by_restore` with its old operation ID/generation; the router holds no call or payload across restart
- **AND** a lost connection is an unknown outcome, not an automatic retry; reconnect checks current status and old-generation operations refuse

#### Scenario: Restored pending work
- **WHEN** the restored image contains queued turns, due schedules or approvals
- **THEN** no restored work runs until explicit owner resumption with fresh IDs; new calls during restore receive unavailable/retry status and are not queued in shared services
- **AND** validation and restore-result recording happen inside the box
