# Design: per-owner isolation as one clean cutover

The earlier design (D1–D87, D214–D221, the L0–L12 slices and lanes A–G) is in git history: main
`5c31bcb171`, U1 `3caeaece2f` and U2 `8c1f3a8c2c`. This file is the whole current design. Where
the two disagree, this file wins.

## 1. End state: the only path

| Process | uid:gid | Notes |
|---|---|---|
| PID1 bootstrap (D70) | 0 → 1001 | Forks the broker and the mapper, then retires to the daemon. It never serves with privilege. |
| daemon, frontends, scheduler | 1001:1001 | No capabilities. Holds the owner channel factor in memory only (D7). |
| broker | 1002:1002 | Owns `/data/.broker/` (ledger, proxy state, `owner-identities.db`, admission log). |
| bounded mapper (D62/D68) | host 300000 | `SETUID`/`SETGID` only inside its user namespace (`0 300000 100000`). |
| every owner-scoped child | `<owner uid>:<owner gid>` | Runs in that owner's cell, which sees only that owner's tree. Classes: decoder, git, git_bridge, preview, node, tool, video, provider-discovery, provider-exec, packages, owner-delete, center-root. |

- **Owner identity.** Every principal has a permanent UID and GID in 300001–399999, reserved
  append-only in `owner-identities.db` (D60/D61).
- **Labels.** A center root is labelled `1001:<owner gid>`. Modes come from one declaration
  (`tinyassets/role_modes.py`, the D4 table as amended by D65).
- **Readers.** Daemon readers check the descriptor label, refuse to follow links, and refuse to
  cross owners (D65).
- **Admission.** A center created after startup is admitted from the broker's append-only
  admission log. Its root gets a capability-free setgid hand-off; there is no restart and no
  relabelling.
- **Deletion.** Deletion is two-pass (D10/D85/D218):
  1. the owner-delete cell runs as the owner and removes the owner's entries;
  2. the daemon removes its own entries and the empty structure;
  3. the center's admission is retired.
- **Startup.** The bootstrap refuses to serve unless `/data/.layout.json` says
  `owner-split` (Fact 8). Nothing at startup migrates, reconciles a journal, or checks a
  principal set.

### Switches and dual paths deleted

| Switch / dual path | Where |
|---|---|
| `TINYASSETS_CREDENTIAL_BROKER`, `ENV_SWITCH`, `broker_selected()` | `tinyassets/broker/supervisor.py:21,34`, plus every caller: `broker/{capabilities,catalog,connection_authority,ledger_queries}.py`, `storage/outbound_connections.py:1178,6206`, and every L3 consumer from #4556 |
| `BrokerUidSplitRequired` refusal; `owner.json`; `stop()`/`read_owner` | `broker/supervisor.py` |
| Legacy per-grant proxy worker (`_run_proxy_worker`, `_ProxyChannel` legacy spawn) | `storage/outbound_connections.py:937,1060` |
| Reader-guard switch `_reader_guards_selected()`; the symlinked-root branch | `workspace_fs.py:593-603`; `universe_files.py:131` |
| `LEGACY_LIVENESS_*`; main's old `owner_state` probe kept beside the new one; `tests/test_owner_state_readonly_parity.py` | `role_modes.py`; `process_liveness` |
| Startup switch `role_startup.SWITCH`/`enabled()`, `reverse()`; the `deploy/compose.role-split.yml` overlay | U2. Fold the startup into `compose.yml` |
| Bounded client "installed only by the bootstrap" (DA8 "inert while absent", "legacy byte-for-byte unchanged") | every spawn site. The client is always installed. |
| Unconfined spawn fallbacks: the legacy branch of each spawn site, and `video_extractor` running ffmpeg as 1001 | `providers/owned_process.py`, `engine_mcp_http.py`, `node_sandbox.py`, `native_jsonrpc_discovery.py`, `universe_tools.py`, `workspace_provision_process.py`, `workspace_registry_process.py`, `video_extractor.py`, `git_bridge.py`, `ui_preview.py` |
| Legacy single-UID deletion traversal (`_layout_split` returning `None`) | `role_owner_tree_deletion.py`, `account_deletion.py`, `role_owner_delete_cell.py` |
| Image vestige: uid 1003 `ta-engine` | `Dockerfile` |
| Codex workdir defaulting into `/app` (`_codex_workdir`) | `providers/codex_provider.py` |

## 2. Keep / drop

### Code (branch → destination)

| Item | Verdict | Destination |
|---|---|---|
| U1: per-class cells `tinyassets/role_*.py` (~2.9k), `owner_launcher_client.py`, `deploy/{role_owner_launcher,role_launcher,role_decoder,role_git,broker_main}.py`, `check_privileged_chain.py`, Dockerfile chain, the `docker-build.yml` check, `compose.yml` HOME, `backup.sh` `.broker` paths | KEEP; delete about one switch check per file | PR1 (deploy modules, inert) / PR2 |
| U1: consumer edits (`credential_vault`, `tool_images`, `ui_preview`, `universe_egress`, `git_bridge`, `node_sandbox`, `video_extractor`, `owned_process`, `provider_jail`, `jail_seccomp`, `discovery_snapshot`, `native_jsonrpc_discovery`, `agent_request_usage`) | SIMPLIFY: keep only the broker/cell branch | PR2 |
| U1: center admission (`role_center_admission`, admission parts of `broker/server.py`, `owner_identities.py`) | KEEP | PR1 (broker side) / PR2 |
| `deploy/role_admission_contract.py` | SIMPLIFY. Keep `canonical_root_acl`, `canonical_label`, `read_label`, `clear_staging`, `broker_log`, `reservations`, `_broker_child`. Drop `legacy_label`, `_resume`, `_restore`, `_forward_stable`, `journal_fields`, `phase_explained`, `raise_alarms`/`_alarms` (the carried-missing alarms) | PR1 |
| U2: `role_volume_migration`, `role_owner_migration`, `role_metadata_migration`, `role_egress_migration`, `role_volume_inventory` | SIMPLIFY into one forward-only `deploy/role_migrate.py`. Keep `_allocate`, `_admission_rows`, `_identity_database`, `_inventory`, `_names`, `_mkdirs`, `_move`, `_permissions`, `_acl`, `_identity`, `relocate`, `transfer_accounting`, `migrate_liveness`, `_apply`, and `discover`/`principals`/`classify`/`reserved` | PR1 |
| U2 migration: `reverse=`, `after_step=`, `direction`, `volume.json`/`journal.json` journals, `checkpoint()`, `_mark`/`_checkpoint` resume markers, `_retain_originals`, `_migrated`, `_provenance`, `_generation`/`_statx`/`_FileHandle`, `STATE` manifests, `_escrow` / quarantine rows, `LEGACY_IDS`, `previous_bindings`, `reconcile_work` | DROP | — |
| U2: `role_startup.py` `boot`/`start`/`health`/`_run_root`; `role_owner_launcher.reap_orphans` (D220); `SETGID_PLATFORM_DIRS` (D219); `role_owner_tree_deletion.py` minus its legacy branch | KEEP | PR2 |
| Probes kept: `role_*_probe.py` (cells, admission, bootstrap, zombie, stream, account erasure), `linux_oracle.py`, `role_image_oracle.py`, `role_launcher_oracle.py` | KEEP; cut the env-switch setup and the OFF/reverse/legacy legs | PR1/PR2 |
| Probes dropped: `role_volume_rollback_probe.py`, `role_old_image_rollback_probe.py`, `role_owner_migration_provenance_probe.py`, `role_legacy_alias_scan.py` (its cross-owner check moves into the migration preflight); `test_role_legacy_alias_scan` | DROP | — |
| Tests (SIMPLIFY: drop the reverse, journal, resume, quarantine and OFF cases): `test_role_{owner,volume,metadata}_migration`, `test_admission_restart_contract`, `test_role_admission_startup`, `test_role_owner_tree_deletion`, `test_account_deletion`, `test_role_startup`, and the switch-off cases in the `test_broker_*`/`test_workspace_fs`/`test_role_reader_identity` tests | SIMPLIFY | PR1/PR2 |
| `rollback.md`, `broker-access-inventory.md`, `delivery*.md`, concern `…quarantine-escrow-retained.md` | DROP | — |
| Concern `…metadata-sidecar-mode.md` (U2) | KEEP; resolve in PR1 | PR1 |

### D-records

- **Kept as built.** D2, D3, D5–D8, D11–D14, D16–D50 (broker routing; only the switch branch
  goes), D51–D56, D57 follow-up (as D65), D60–D62, D65, D66, D68–D72, D73 (founder),
  D74–D87, D219, D220, and DA1–DA6 (admission log, mapper read channel, setgid root hand-off,
  ordered admit, deletion retire).
- **Simplified.**
  - D1: uid 1003 is removed.
  - D4: the inventory table stays. "Retain owners for old images" and the reverse transfer go.
  - D9: its acceptance matrix loses the rows for old image, reverse, dry-run reverse and
    rollback.
  - D10: two-pass deletion stays. Its part 3 (startup reverse migration) and the startup
    privileged migration window go.
  - D15: relocation becomes a migration step instead of a fenced startup substep.
  - Founder D61: reachability stays the ownership rule. A cross-owner inode now makes the
    migration refuse before any write; it is not quarantined.
  - D64: the scan becomes the migration preflight.
  - D218: two-pass deletion always. The `.layout.json` state branch and the legacy traversal go.
  - D221: the admission log stays. Restart reconciliation goes.
- **Superseded.**
  - D58 and D59 (by D60).
  - D63 and D57 (historical stops, resolved by founder D61 and D65).
  - D67 (rollback substep venue).
  - D214, D215 and D217 (inode-generation provenance existed only to resume and reverse).
  - D216 (principal-set refusal; startup no longer migrates).
  - DA7 (restart admission-generation contract).
  - DA8 (inert while absent).

## 3. The one-time migration

`deploy/role_migrate.py` runs as a one-shot container from the cutover image:
`--user 0`, `cap_add: [CHOWN, FOWNER, DAC_OVERRIDE]`, `--network none`, and the data volume
only. The service image never holds those three capabilities. The compose `cap_add` and the
`ta_op.c` `MASK` cover the D70 bootstrap's set only (SETUID, SETGID, SETPCAP, KILL), and
`SYS_ADMIN` leaves `MASK`.

**Preconditions.** The migration refuses before its first write unless all of these hold:
- no process holds the volume (exclusive `flock` on `/data/.layout.lock`, plus no container
  attached);
- the filesystem is ext4, not overlay, and an `setfacl` probe works;
- free space is above the size of the snapshot it is given;
- `--snapshot <id>` names the verified backup;
- the read-only preflight passes:
  - every inode's names lie in one class: one owner tree, or one platform area. Production on
    2026-10-02 had 52,167 names over 52,162 inodes, with 5 multi-link inodes inside one owner's
    `.runtime/` and 0 cross-owner inodes;
  - every entry is uid 1001 or already at its target;
  - no unexpected special files exist.

**Steps.** Each step sets the target or skips. None reads a previous run's progress.

1. **Identities.** For each principal found (D64 discovery), reserve its UID/GID in
   `owner-identities.db` if absent (D61), and append its `admit` rows if absent (DA1).
2. **Egress (D11/D12).** Checkpoint the WAL, then rename `outbound.db`, its sidecars and
   `.outbound-proxy` into `/data/.broker/`:
   - source only: rename;
   - destination only: skip;
   - both present: refuse.
3. **Labels.** Walk with pinned no-follow descriptors. Set uid, gid, mode and ACL on each entry
   to the role_modes target, and change only the fields that differ. Workspace symlinks are
   skipped and their targets are left alone.
4. **Marker.** Atomically write `/data/.layout.json`:
   `{"layout": "owner-split", "snapshot": "<id>", "at": "<utc>"}`. This is always the last write.

`--check` is read-only. It reports every entry not at its target and every precondition. It
runs before the apply, and again after it, when it must print zero diffs.

**Idempotency.** Each step's target is a pure function of the path and `owner-identities.db`.
Reservations are append-only, and each one is written before any chown uses it. So a crash at
any point is fixed by running the same command again, and a converged volume is a no-op. A
volume without the marker is not ready, and the service refuses it.

## 4. Maintenance-window runbook

PR1 adds this as `docs/ops/owner-split-cutover-runbook.md`. Budget about 60 minutes; the PR1
rehearsal sets the real number. The founder schedules the window.

1. **Announce.** Tell the testers the window and the expected downtime.
2. **Stop.**
   - Hold auto-deploy so nothing else merges.
   - Disable the backup, autoheal and watchdog timers, so nothing restarts the daemon.
   - `docker compose stop` every container on `tinyassets-data`.
3. **Snapshot.**
   - Take a full-volume tar of `tinyassets-data` with writers stopped:
     `--numeric-owner --acls --xattrs`.
   - Write a local copy and upload it to the Storage Box, using the `deploy/backup.sh` full tier.
   - Record its sha256 and name count. The count must match a fresh census.
   - This is the rollback point.
4. **Migrate.**
   - Run `role_migrate --check`, then `role_migrate --snapshot <id>`, then `--check` again, which
     must show zero diffs.
   - On a crash, run the same command again.
5. **Start.**
   - Merge PR2. `deploy-prod` deploys it, and the bootstrap starts with the marker present.
   - Do not trust `deploy_fail_safe.sh`'s automatic image revert here. The previous image on a
     migrated volume is not a supported state, so if it reverted, go to Rollback.
6. **Verify.**
   - `python scripts/deployed_sha.py --assert-contains <PR2 sha>`.
   - `python scripts/mcp_public_canary.py --assert-handles`.
   - `ps` shows 1001, 1002 and owner uids, with zero capabilities after retirement.
   - One founder app turn through the app agent, including a provider turn, which exercises
     provider-exec.
   - A cross-owner refusal probe in production: tester B's cell tries to read the founder's
     tree and vault, and B's daemon-side read names the founder's file. Both must refuse.
   - Re-enable the timers and announce the end.
7. **Rollback** (any verify step fails, or a regression later):
   1. Stop.
   2. If disk allows, move the migrated volume aside; otherwise remove it.
   3. Restore the snapshot with `deploy/backup-restore.sh`.
   4. Redeploy the previous image with `deploy-prod` `workflow_dispatch image_tag=<previous>`
      or `recovery-retag-image.yml`.
   5. Revert PR2 on main, so the next push does not redeploy it.
   6. Run `deployed_sha` and the canary.

   Once users have written after the cutover, rollback loses those writes. Decide at Verify.

## 5. Deletion list (PR3): app checks the kernel now enforces

The daemon stays uid 1001 and still opens every owner's tree for that owner. The kernel does
not stop a steered daemon, so most cross-owner checks stay. Only checks that guard an owner
child against another owner's files are deleted.

**Delete:**
- `tinyassets/workspace_fs.py:693` `_open_regular_beneath`, the `st_nlink != 1` refusal.
  Owners cannot hardlink a file they cannot open (`protected_hardlinks`, 0700 trees), and the
  D65 label check covers the daemon. `scripts/data_nlink_census.py` goes with it.
- `tinyassets/node_sandbox.py:1662-1681` `_validate_workspace_bind`, the plain-path realpath
  branch. Only the `/proc/self/fd` branch remains.
- `tinyassets/node_sandbox.py:738-756` `_WsPathRoot._resolve`, which is tests only.
- `tinyassets/providers/provider_jail.py:460-513` `_validated_view`, the cross-owner half. The
  sidecar half stays.
- Concerns the cutover resolves, deleted after Verify:
  `2026-10-04-engine-mcp-shares-daemon-uid`, `2026-10-02-native-model-discovery-runs-unjailed`,
  `2026-09-29-tool-jail-binds-by-pathname`, `2026-10-05-role-reader-hardlink-alias`.

**Keep. These guard what the OS does not:** a 1001 reader steered across owners, DB rows in
shared stores, and the network.
- `universe_files.py` no-follow walks: `read_universe_file`, `write_universe_file`,
  `open_lock_file`, `open_runtime_dir`, `*_data_path`, `connect_guarded`.
- `workspace_fs.py` `_read_owner_identity` and package manifest checks.
- `provider_jail.py` `hidden_root_masks`/`default_view` (platform state inside one owner's
  tree), `_forbidden_install_roots`, `ensure_agent_workspace`.
- `providers/base.py` `_preflight_vault_source`.
- `ingestion/canon_{names,io}.py`.
- `api/{wiki,helpers,universe_file_reads}.py`.
- `soul_edit`, `scoped_reset`, `conversation_reset`, `automations`, `automation_context`,
  `command_center_update_executor`.
- `run_file_crossowner.py`, `agent_loop/owner_reads.py`, `memory/scoping.py`.
- `universe_egress.EngineRelay` (loopback ports).
- The `jail_seccomp` deny on symlink and mknod.

## 6. Build order: three PRs

The founder's final integration instruction folds these into one cutover PR.
Workspace dependency provisioning is preserved: its acquisition and offline
installation run as the owner in a `workspace-provision` cell with a pinned
lease. Acquisition receives only an invocation-scoped registry relay (HTTPS to
the three existing registry hosts, shared transfer and connection budget).
The daemon closes that relay and confirms quiescence before installation.
Installation receives no network relay. Canonical manifest digests, storage,
output, memory, cancellation and deadline bounds remain mandatory. The daemon
retains the full transfer reservation whenever terminal evidence is uncertain.

| PR | Contents | Release-critical (cap 8) | Proof | Days |
|---|---|---|---|---|
| **1. Migration and runbook (inert)** | `deploy/role_migrate.py` (forward-only fold of the five U2 modules); `role_admission_contract.py` (kept parts); the inert deploy modules `role_owner_launcher`, `role_decoder`, `role_git`, `broker_main` (no Dockerfile `COPY` yet); the broker admission tables; probes and tests; the runbook | 6 | Root oracle (`linux_oracle.py`): kill at every step, then rerun, gives a byte-identical manifest (path, uid, gid, mode, ACL, sha), and a converged rerun is a no-op. Preflight refuses each precondition. Rehearsal on a restored copy of the latest production backup: `--check` 0 diffs, 0 cross-owner inodes, wall time recorded. Snapshot restore round-trip keeps numeric owners and ACLs. Merges like any PR. | 3–4 |
| **2. Cutover** | `Dockerfile` (`--copies`, `/app` read-only, HOME, no 1003, role module `COPY`s, ffmpeg and acl); `compose.yml` (PID1 bootstrap, `cap_add`, healthcheck through `ta-op`); `ta_op.c` `MASK`; `role_launcher.py` (with `role_startup` folded in); `backup.sh`; `docker-build.yml`. In `tinyassets/`: every U1 cell and consumer with its switch branch removed; providers through provider-exec (Codex/Claude adapters, engine-MCP thin proxy, workspace provision/registry/worker, discovery egress, the K1 consumer); center creation through admission; two-pass deletion everywhere, including the subtree cell for pool removal and `scoped_reset`; the `/app` write fix | 6 | Non-draft `build-smoke` and `check_privileged_chain`. `role_image_oracle` on the PR1-migrated restored clone: every class gives zero foreign bytes; signup → admit → cell without restart; two-pass delete; zero caps after retirement; `ta-op pulse`; a real Codex and Claude turn. Then the runbook's Verify. **Merged only inside the window.** | 11–15 |
| **3. Deletions** | § 5 deletions and concerns; the remaining dead legacy tests | 0 | Touched tests, `ruff`, `deployed_sha`, the cross-owner refusal probe re-run. Merged the same day as PR2. | 1 |

Total: about 15–20 builder-days. PR1 and the provider work in PR2 can run in parallel, so it
is about 2.5–3 weeks of wall time, against 6–8 weeks for the slice plan. The largest single
item is providers through provider-exec, about 8–10 days. A spawn class the founder retires
instead of celling drops out of PR2. There is no unconfined fallback.

## 7. Open PRs and branches (listed, nothing closed)

- **#4523** (U1, draft, `feat/per-role-uid-split` `3caeaece2f`): superseded by PR1 and PR2.
  Salvage all cells, the mapper and launcher, the decoder, git, `owner_launcher_client`,
  center admission, the image chain and the kept probes, using
  `git checkout origin/feat/per-role-uid-split -- <file>`. Leave behind the rollback, legacy
  alias and provenance probes and the OFF legs.
- **#4509** and **#4510** (U2, both draft, head `8c1f3a8c2c`): superseded by PR1, with the
  startup pieces going to PR2. Salvage:
  - the forward cores listed in § 2;
  - `role_volume_inventory`;
  - `role_startup` `boot`/`start`/`health`/`_run_root`;
  - `reap_orphans` (D220), `SETGID_PLATFORM_DIRS` (D219) and `role_zombie_probe`;
  - D218 without the legacy branch;
  - the metadata-sidecar-mode concern.

  Leave behind the reverse migration, the journals, quarantine and escrow,
  `compose.role-split.yml`, the rollback probe and the carried-missing alarms.
- **#4512** (browser custody, draft, stacked on U1): superseded only as a stack. Salvage its
  two docs commits, `cdc98985f7` and `f95175c8ee` (the `browser-login-custody` design and the
  prerequisites concern), rebased onto main after PR2. The D73 preview cell arrives in PR2.
  It stays blocked on D5.
- **#4556** (L3): not superseded. It lands, and PR2 deletes its switch branches.
- Keep both feature branches until PR2 has passed Verify (Fact 13).

### Engine endpoint completion

The authenticated per-center engine HTTP endpoint runs canonical control-plane handlers
in the daemon, where the bootstrap-installed broker and mapper clients exist. Each
endpoint has a separate handler module with fixed owner and graph pins; request and
lifespan context carry that endpoint and its grant key without changing process environment.
No daemon-UID engine subprocess is launched. Provider cells retain the same pinned relay
and bearer protocol. Port reuse requires endpoint retirement; tool authority is still
rechecked on every call.

Tool extension mounts carry one daemon-pinned directory descriptor in fixed slot 6.
The mapper checks daemon ownership and no group/other write access; both cell layers
bind it read-only and verify its inode. Because bubblewrap canonicalizes fd sources,
the private per-call temporary parent grants only that owner a traverse-only ACL.
It stays unlistable; the sibling relay socket remains daemon-only. Nested tool cells
reuse the explicitly prepared workspace and never call daemon preparation again.


### Owner-content publication after admission

Canonical center roots remain daemon-owned, with owner r-x only. Every daemon
write of visible center content (including replacement files and missing parent
directories) creates its output through a fixed owner-content cell. The daemon
resolves the recorded center owner through the broker; the mapper requires its
existing binding. No UID, executable, environment or host path comes from the
payload. A private random directory under the existing `.role-admission` staging root
admits only that owner and inherits the
canonical daemon-reader ACL. Bounded bytes create only fixed numbered output
entries. The daemon checks the completed cell, exact inventory, owner labels,
ACLs, regular-file single-link counts, SHA-256 bytes and directory shapes before
publication. Input arrives in fixed-size chunks with an exact declared length;
fixed memory/time bounds and that file-size rlimit preserve verbatim bytes without
adding a new generic-writer file-size ceiling. Crash leftovers stay unpublished
under the existing admission staging namespace and are removed by the existing
forward migration cleanup, never inventoried as center content.

Only the daemon publishes names under the canonical root. Rename preserves
atomic replace and exclusive creation (NOREPLACE); existing append targets use
O_APPEND on a pinned, no-follow owner inode, while absent append targets are
published exclusively and retry on a concurrent creator. Missing parents are
published one empty owner directory at a time, never replacing an existing tree.
Parent descriptors stay pinned throughout. Source bytes and old targets survive
failed creation or publication. Hidden platform state, provider_definitions.json and owner.json retain daemon
ownership; .agent-workspace remains owner content, matching migration. The central writer recognizes canonical owner roots structurally and
fails on malformed owner labels rather than starting an unconfined writer.

Tool maintenance repairs owner ACL masks and reports bounded eligible absent
brain names; it never creates root entries. After maintenance exits, the daemon
reads a bounded pinned single-link owner source and uses the same owner-content
publication operation with NOREPLACE. The source workspace bytes remain intact.
Strict tool mount and daemon-reader ownership/link checks remain unchanged.

Selected-model result ceilings travel on the authenticated engine HTTP route.
Handlers read the current MCP request (with request-context fallback) rather than
mutating process environment; the fixed maximum and deployment override still win.
Concurrent endpoint requests retain independent ceilings.
