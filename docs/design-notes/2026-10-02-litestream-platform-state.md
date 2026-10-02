# Litestream for platform state (target-architecture S1a.2)

**Status:** DEFERRED (lead decision, 2026-10-02, under the founder's slim-until-paying-users
rule). Litestream waits for the command-center cutover (#4262), which puts platform state in
`.platform/` (D8a) with clear content classes. Interim: the brain tier (root stores, copied
through the SQLite backup API) goes off-region **hourly**. That gives about 1 h RPO for
platform state, with $0 cost and no new component. This note stays as the record of what a
later Litestream change must solve.
**Measured:** 2026-10-02 on the production droplet. Journal mode and size of every
non-archive `*.db` under `/data` (read-only `pragma journal_mode`), the Litestream release
notes v0.5.0 to v0.5.17, and `git grep` for checkpoint and journal pragmas.

## What the slice asks

"Run Litestream v0.5 for every SQLite store, to off-region", RPO about 10 s (lead,
2026-10-02: 10 s sync), to the Spaces nyc3 bucket that #4279 converges.

## Three findings that change "every SQLite store"

### 1. User content must not go into Litestream

Litestream keeps every committed page in LTX files until retention expires. Two user-content
paths depend on data actually disappearing:

- `conversation_custody` deletes a conversation logically and then runs
  `PRAGMA wal_checkpoint(TRUNCATE)` with `SECURE_DELETE`, so the content leaves the WAL
  (`tinyassets/storage/conversation_custody.py:651-666`). Under Litestream:
  - The deleted pages are already shipped off-region and stay there until retention expires.
  - Litestream's long read transaction makes that TRUNCATE checkpoint report busy. After
    30 s the deletion raises `ConversationCustodyCleanupPending` to the user.
- Per-universe stores (`u-*/.conversation_memory.db`, `knowledge.db`, `story.db`, ...) are
  user content. target-architecture puts user content into boxes, backed up by restic
  from quiesced images (D8, D11), not Litestream.

**So Litestream replicates platform state only:** the root stores. Per-universe stores stay
on the nightly tiers (sfo3 plus nyc3 since #4279) until the command-center cutover (#4262)
moves platform state into `.platform/` (D8a). Then Litestream watches `.platform/` and
nothing else.

### 2. Four root stores are not in WAL mode, and Litestream would switch them

Litestream requires WAL. On prod, these root stores run in rollback-journal (`delete`) mode:
`.account_seats.db`, `.engine_run_admissions.db`, `outbound.db`, `wiki_trigger_attempts.db`.
Litestream would flip them to WAL underneath code that never chose it. That code may hold
connections across processes (engine children) and copy files. A journal-mode change is a
code change with its own tests, not a side effect of a sidecar.

**So phase 1 replicates the WAL-mode root stores only:**

| Store | Size | Mode |
|---|---|---|
| `.tinyassets.db` | 58 MB | wal |
| `.runs.db` | 84 MB | wal |
| `.langgraph_runs.db` | 115 MB | wal |
| `.automations.db` | 1.8 MB | wal |
| `.auth.db` | 36 KB | wal |
| `.storage_accounting.db`, `.owner_devices.db`, `.effector_consents.db`, `.hosted-model-auth.db`, `.source_channel_policy.db` | < 60 KB each | wal |

The four `delete`-mode stores each get a follow-up: switch the owning module to WAL
explicitly, test it under the engine-child access pattern, then add it to the list.

### 3. Discovery must be an explicit list, not a directory watch over `/data`

v0.5.4+ can watch a directory and replicate every database that appears in it. Over `/data`
that would pull in per-universe user content (finding 1) and the 88 MB of
`_removed_universes_*` archives. Phase 1 uses an explicit `dbs:` list, generated from the
storage resolvers so it cannot drift from code. A test fails when a new root store is added
without a decision: replicate it, or exclude it with a reason.

## Shape

- **Sidecar** `litestream` service in `deploy/compose.yml`: the official image pinned by
  digest (v0.5.17). It mounts the `tinyassets-data` volume read-write; Litestream writes its
  `-litestream` meta next to each database. Its `env_file` holds the Spaces key and is
  mounted into this service only. Memory limit 256m.
- **Config:**
  - Replica `type: s3`, `endpoint: https://nyc3.digitaloceanspaces.com`,
    `bucket: tinyassets-offregion`, `path: litestream/<store>`.
  - `sync-interval: 10s` (lead). That is about 1.3M PUTs/month across five busy stores,
    inside the Spaces plan.
  - `retention: 72h` (below).
- **Credential:** a SEPARATE per-bucket readwrite key, minted by install-host-services like
  #4279's and written to a root-only `/etc/tinyassets/litestream.env` for the sidecar's
  `env_file`. It is not shared with backup.sh, so the two can be rotated independently.
- **Checkpointing:** Litestream v0.5 runs checkpoints itself. The app's only manual
  checkpoints are `node_eval`'s PASSIVE (harmless) and custody's TRUNCATE, which is on a user
  store and so excluded.

## Retention and deletion honesty

Account deletion removes a user's rows from `.tinyassets.db` and friends. Those rows stay in
LTX history for **72 h**. The nightly tarballs already keep deleted data for up to 6 months,
so this adds no new exposure window. It must still be written down, so the deletion runbook
can say how long backups retain deleted data: 72 h in the replica, 6 months in archives.

## Encryption

Litestream v0.5 removed client-side age encryption (#870, #791). Two options:

- **(a) S3 SSE-C** with an escrowed key (v0.5.6+), if Spaces honours SSE-C. This must be
  verified against the real bucket before it is relied on.
- **(b) Private-bucket plaintext,** with provider-side encryption at rest, under the
  slim-until-paying-users rule.

Recommendation: try (a) in the first deploy, and fall back to (b) explicitly if Spaces
rejects SSE-C. Either way the choice gets recorded; it is never silent.

## Restore and the drill

- **Restore:** `litestream restore -o <path> s3://...` per store, before the daemon starts,
  in a fresh host. The tarball tier supplies the per-universe stores and files.
- **Drill:** after its tarball restore (#4271), the weekly drill also restores the Litestream
  stores. It asserts each one is newer than the tarball's copy and passes
  `PRAGMA integrity_check`, and it records the measured RPO (Litestream position against the
  primary's last commit time).

## Alarms (S1a.6, same change family)

`litestream_replica_lag` is the age of each store's last synced position, from Litestream's
Prometheus endpoint, scraped by the existing watchdog path. Thresholds: page at 5 min, warn
at 1 min. Backup age gets its own alarm (nightly plus 2 h).

## Tasks (one change, ≤ 12)

1. The stores list is generated from the storage resolvers, with a drift test.
2. Install-host-services mints the Litestream key and writes `litestream.env`.
3. The compose sidecar plus config, with the validator updated for the new service.
4. A deploy acceptance check: every listed store has a replica position younger than 60 s
   after the converge.
5. The SSE-C probe against the real bucket, recording whether it was used or why not.
6. Drill restore of the Litestream stores, plus the RPO measurement.
7. Lag and backup-age alarms.
8. Runbook: restore, retention, deletion honesty.
9. Follow-ups filed for the four `delete`-mode stores.

## Questions for the refute

- Does Litestream v0.5's checkpoint ownership interact badly with the app's own connections:
  `wal_autocheckpoint` default 1000, many short-lived connections, engine-child processes?
- Is a sidecar reading the WAL of databases written by another container safe across the
  container boundary? It is the same host kernel and the same inode, but different PID
  namespaces, and SQLite's shm locks.
- Is 72 h retention long enough for point-in-time recovery from a bad deploy (the S1a.4
  restore test)?

## Refute outcome (gpt-6-astra, 2026-10-02): ADAPT. Why it is deferred, not shipped

The sidecar architecture is viable. The guarantees above were not supported:

- **Root location is not a content boundary.** `.tinyassets.db`, `.runs.db` (inputs, outputs,
  judgments) and `.langgraph_runs.db` hold user content that account deletion removes or
  redacts (`account_deletion.py:528,657`). `.hosted-model-auth.db` uses `secure_delete`
  (`connection_oauth/pkce.py:74`).
- **Point-in-time restore resurrects deleted accounts.** The tombstone lives in the database
  being rolled back. This is a property of every backup tier, so it is filed separately:
  `docs/concerns/2026-10-02-restores-resurrect-deleted-accounts.md`.
- **No cross-store restore atomicity.** Examples: `outbound.db` grants against
  `provider_serving_binding.py`, and `.runs.db` against `.langgraph_runs.db`. A restore set
  must be defined and reconciled.
- **v0.5.17 configuration differs from what this note wrote:** `snapshot.retention`, separate
  L0 retention (default 5 min), and no built-in replica-lag metric.
- **Maintenance exclusion:** the sidecar must take part in the layout lock and in
  migration, reset and restore exclusion (`storage_layout.py:11`, `scoped_reset.py:848`).
- The inventory is incomplete (`.node_eval.db`, `.authoring.db`, `.idempotency.db`,
  `daemon_brain.db`). Every store needs an explicit decision.

A later Litestream change starts from `.platform/` only, after the cutover, and must answer each
point above.

