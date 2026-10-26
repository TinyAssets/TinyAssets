# Operator-request v2 inventory

Freshness: 2026-07-24 UTC. Code baseline:
`origin/main@7553ba4b03abad796d3e969ce23ae0885a8b4da8`.

This is the pre-migration inventory required by
`operator-request-trigger-contract`. It records evidence; it does not relabel
v1 rows, enable the v2 writer, or assert that a worker supports queue epoch 2.

## Production-shaped data snapshot

Evidence source: the independently validated full-data backup
`tinyassets-data-2026-07-24T07-28-29Z.tar.gz`, published at
`2026-07-24T07:31:13Z`, size `476044957`, SHA-256
`e4fd01bb6c490ba59855d7502c3a166e1bea94ae494d72c447d78358c1607667`.
The archive was extracted in isolation during production backup run
`30075565479`; its 14 SQLite databases passed `PRAGMA integrity_check`.

Three universes contained v1 `branch_tasks.json`:

| Universe | Total | Statuses | Trigger sources |
|---|---:|---|---|
| `concordance` | 1 | failed 1 | owner_queued 1 |
| `echoes-of-the-cosmos` | 37 | succeeded 36, failed 1 | owner_queued 33, goal_pool 3, user_request 1 |
| `workflow-voice` | 19 | failed 12, cancelled 4, pending 3 | owner_queued 16, goal_pool 3 |
| **Total** | **57** | succeeded 36, failed 14, cancelled 4, pending 3 | owner_queued 50, goal_pool 6, user_request 1 |

Historical `host_request` rows: **0 pending, 0 running, 0 total**. No row is
relabelled by this finding. The three pending rows are the already-known
`workflow-voice` owner/goal rows and remain outside this migration.

Exactly one universe carried `dispatcher_config.yaml`:
`echoes-of-the-cosmos` with `accept_goal_pool: true`. No snapshot config
overrode `tier_weights`; the observed custom-weight count is **0**. Code
defaults still contain `host_request=100` and no `operator_request` entry.

## Public v1 writers

At the pinned `origin/main` inventory baseline there were two public MCP
server surfaces and one shared implementation:

1. `tinyassets/universe_server.py::write_graph(target="request")`
2. `tinyassets/directory_server.py::write_graph(target="request")`
3. both call `tinyassets.api.universe::_action_submit_request`

The shared action writes `requests.json`, then separately appends
`branch_tasks.json` and catches the append failure. It is therefore one
underlying non-transactional v1 writer that was exposed through two public
mounts at that baseline.

The 2026-07-24 MCP retirement removes `directory_server.py` and its public
mount. After that cutover, the same underlying non-transactional v1 writer is
exposed through one public surface:
`tinyassets/universe_server.py::write_graph(target="request")`. This route
retirement changes the number of public mounts, not the writer's transactional
behavior recorded by this inventory.

## Worker evidence

The snapshot contained four named supervisor records for `concordance`
(`claude-1`, `claude-2`, `codex-1`, `codex-2`) plus the legacy alias file.
At the snapshot boundary only `codex-2` reported `phase=polling` and
`subprocess_alive=true`; the other named records reported `phase=backoff`.

The current heartbeat shape contains:

`ts`, `phase`, `iteration`, `supervisor_started_at`, `last_spawn_at`,
`last_exit_rc`, `total_spawns`, `total_crashes`, `consec_crashes`,
`subprocess_pid`, `subprocess_alive`, `planned_sleep_s`, `worker_id`, and
`runtime_instance_id`.

It contains **no** build SHA, config hash, boot ID, universe ID, queue protocol
version, or capability list. Consequently the count of active workers proven
to run a particular build is **unknown**, not four. The backup's
`release-state.json` names image tag `519fb2ea98d2`, but that deploy receipt is
not per-worker protocol evidence and must not be promoted into one.

## Collision inventory

The exact prospective claim check was clear for the inventory, migration,
store, and focused test files.

- Draft PR #1606 touches future authority/public-route files and shared
  coordination files, but not this phase's new store or migration region.
- Draft PR #1472 touches future cloud-worker/dispatcher integration, but not
  this phase's store or migration region.
- Draft PR #1464 touches `tinyassets/daemon_server.py` only around reserved
  reference-seed branch-definition writes. This phase invokes a migration from
  the initialization region near the top of the same file. The semantic
  regions do not overlap, but a same-file rebase check remains required before
  foldback.

Later phases must repeat the collision check before claiming
`tinyassets/api/universe.py`, server shells, dispatcher, worker, registry,
status, mirror, rollout, or distributed-execution files.

## Reproduction and baseline

The stale fixture baseline initially failed 17 of 80 focused tests because
those tests created no declared Loop. After seeding an explicit legacy
compatibility Loop and removing host-identity authority assumptions, the
focused baseline is:

```text
python -m pytest -q tests/test_dispatcher_queue.py tests/test_submit_request_wiring.py tests/test_patch_request_incentives.py
80 passed in 10.01s
```

The intended bug reproduction uses a valid Loop plus an explicit
`submit_priority_request` grant. It failed before implementation:

```text
python -m pytest -q tests/test_dispatcher_queue.py::test_priority_grant_queues_operator_request
FAILED: assert resp["branch_task_id"]  # actual value: ""
WARNING: Invalid trigger_source: operator_request
```

The producer had already persisted the Request and returned `status=pending`;
the v1 BranchTask validator rejected `operator_request`, the exception was
swallowed, and no task was committed. The temporary red assertion was removed
after capture so the branch does not normalize a known failing test. The
transactional-store tests replace it with the durable red/green boundary for
this phase; public-surface red/green coverage remains a later task.

Focused legacy baselines retained for later reconciliation:

```text
tests/test_dispatcher_queue.py:398-469,785-823
tests/test_submit_request_wiring.py:204-294
tests/test_patch_request_incentives.py:19-77
```

These regions may be changed only to remove obsolete fixture/host assumptions
or to assert the final transactional protocol. They must not reintroduce a v1
`operator_request` row.

## Transactional storage proof

Freshness: 2026-07-24, Windows/SQLite, branch
`codex/operator-request-admission-storage`.

The epoch-2 migrator was run twice on an isolated copy of the exact validated
production backup database at
`%TEMP%\TinyAssetsBackupValidation-30075565479\restore-full\_data\.tinyassets.db`
(backup SHA-256
`e4fd01bb6c490ba59855d7502c3a166e1bea94ae494d72c447d78358c1607667`).
All 25 pre-existing table counts were unchanged, all five epoch-2 tables were
created empty, `PRAGMA integrity_check` returned `ok`, and
`PRAGMA foreign_key_check` returned zero rows. Repeating the migration was
idempotent.

Current focused evidence:

```text
22 passed in 1.68s

101 passed in 11.59s

python -m pytest -q tests/test_multi_tenant_isolation.py tests/test_storage_db_filename_migration.py tests/test_canonical_bindings_migration.py tests/test_node_registry_migration.py tests/test_runs_schema_migration.py tests/test_daemon_registry.py tests/test_storage_phase7_backend.py
94 passed in 9.88s
```

The public writer remains unreachable and disabled. These checks prove the
SQLite storage foundation only; they do not prove the later identity, public
surface, worker protocol, rollout, hosted-Postgres, or §14 load phases.
