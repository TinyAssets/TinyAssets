## ADDED Requirements

### Requirement: Storage is one quota per account, set by the account's tier

The platform SHALL bound the cloud storage of each account, meaning each person
and not each universe, by one quota taken from `usage_policy.limits_for(tier).storage_bytes`.
Every universe the account owns SHALL share that quota. No other storage quota
SHALL exist: any per-universe, per-workspace, per-feature or per-hour storage
number SHALL be removed rather than kept alongside it. Creating a universe SHALL
NOT be limited by storage or by tier. Free and paid tiers SHALL differ only in
the quota's size, and every feature SHALL work within the free quota.

#### Scenario: Two universes share one pool
- **WHEN** a free account owns two universes holding 1.2 GiB and 0.7 GiB
- **THEN** the account's usage is 1.9 GiB of its one free quota, and a 200 MiB gated write to either universe is refused

#### Scenario: Universe creation is not storage-limited
- **WHEN** an account at its storage quota creates a new universe
- **THEN** the universe is created

### Requirement: Every universe has exactly one owning account, recorded when it is created

The transaction that creates a universe SHALL record its owning account. A
universe's bytes SHALL be charged only to that account, whoever else holds a
grant on it. An owner that no stored binding records SHALL NOT be inferred from
correlated data. Pre-existing universes SHALL be backfilled only from the
explicit founder home binding. A universe without a recorded owner SHALL be
reported as unattributed on host-only surfaces, and its bytes SHALL be counted.
Its writes SHALL NOT be refused until an owner is known.

#### Scenario: Backfill uses the home binding only
- **WHEN** a pre-existing universe has an admin grant but is nobody's founder home
- **THEN** it stays unattributed, and its writes are counted and not refused

#### Scenario: A shared universe charges its owner only
- **WHEN** an owner grants a second user admin on their universe and that user writes a page to it
- **THEN** the bytes count toward the owner's quota and not the second user's

### Requirement: Accounting covers every store a user's bytes live in

The owning account's usage SHALL include:
- its universes' files, excluding the platform's provider runtime;
- published and reserved permanent workspace generations;
- run records and checkpoints in the shared run databases;
- uploaded and custody files;
- branch definitions and published versions;
- commons pages the account wrote.

It SHALL include those bytes whether the store sits inside a universe directory
or beside one. Live scratch leases SHALL NOT be charged to any account. Every
top-level store in the data directory SHALL be either registered with an
attribution rule or explicitly declared platform-owned, and an unregistered store
SHALL fail the test suite.

#### Scenario: Bytes beside the universe directory are counted
- **WHEN** an account's runs add 50 MiB of records to `<base>/.runs.db` and 30 MiB of uploads to `<base>/.run-file-custody`
- **THEN** its usage rises by 80 MiB

#### Scenario: A new store nobody registered
- **WHEN** a change adds a new top-level database under the data directory without registering it
- **THEN** the completeness test fails

### Requirement: Usage is measured plus pending, and a concurrent write is never lost from it

Usage SHALL be the sum of cached per-store measurements plus pending
reservations. A reservation SHALL be removed from pending only by a measurement
that provably started after that write committed. Admissions SHALL be serialized
so that two concurrent writes cannot both be admitted into the same headroom.
The bytes a write commits SHALL NOT exceed its reservation. When usage has never
been measurable, writes SHALL be admitted only while pending bytes stay within
the quota. Beyond that point they SHALL be refused as
`storage_accounting_unavailable`, which is distinct from `storage_quota_exceeded`.

#### Scenario: A write landing during a scan
- **WHEN** a write commits while its universe is being measured
- **THEN** the write's bytes remain in pending until a later measurement that started after the commit, and usage never drops below what is on disk

#### Scenario: Two writes race for the last headroom
- **WHEN** two 100 MiB writes are admitted concurrently with 150 MiB of headroom
- **THEN** exactly one is admitted

### Requirement: A refusal is always based on a fresh measurement, so deleting frees space immediately

Before refusing a write as over quota, the gate SHALL re-measure every one of the
account's stores whose measurement is older than 60 seconds, and SHALL then
decide again. A delete SHALL NOT be credited from what was requested. Space
SHALL be freed only when a measurement no longer sees the bytes.

#### Scenario: Delete, then retry
- **WHEN** a refused owner deletes a 500 MiB workspace and retries the same write
- **THEN** the retry is admitted

### Requirement: Enforcement is on user-driven write paths only, and reads never break

The gate SHALL be applied to writes of files, pages, uploads, workspaces, and
branch and version writes, including project memory, the daemon wiki and the app
UI library. Write-capable jail calls whose output size cannot be known in advance
SHALL be refused only when the account is already at or over quota. The
credential vault, session and auth stores, subscription state, chat history, run
records and checkpoints SHALL be counted and SHALL NOT be refused. No read, list,
delete or status path SHALL consult the quota.

#### Scenario: Over quota, sign-in and chat still work
- **WHEN** an account is over its quota
- **THEN** credential refresh, sign-in, chat turns and every read still succeed, and only gated writes are refused

### Requirement: The refusal is visible, actionable and carries the upgrade link inline

A refused write SHALL return a structured failure with
`failure_class: storage_quota_exceeded`, `actionable_by: user`, the used,
quota and requested bytes, the tier, and the account's own largest consumers.
Its message SHALL say how to free space and SHALL end with the sentence built by
`usage_policy.upgrade_sentence`. That sentence contains a clickable "Upgrade"
link to `usage_policy.upgrade_url`, and the link SHALL be omitted on the top
tier. There SHALL be no separate banner, card or modal. A refusal SHALL NOT
reveal one account's usage to another user.

#### Scenario: Free owner hits the quota
- **WHEN** a free owner's page write is refused
- **THEN** the message names the usage and quota and contains `[Upgrade](https://tinyassets.io/app?upgrade=1)`

#### Scenario: Top tier
- **WHEN** a paid owner's write is refused
- **THEN** the message has the numbers and no upgrade link
