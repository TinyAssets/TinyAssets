# Design: isolation by box

## D1. Two homes per center

| Home | Path | Writer | Holds |
|---|---|---|---|
| Owner box | `/var/lib/boxhostd/owners/<uid>/<center>` (box path `/cc/...`) | the owner's box only | all owner content |
| Platform sidecar | `<data>/.universe-sidecars/<center>/` | the platform only; mounted in no box | every per-center platform file |

`<data>/<center>/` no longer exists after the C6 move.

One resolver, `boxes/layout.platform_center_path(data_root, center, name)`,
names every platform file. It validates `center` as a single safe component.
Every platform store resolves its path through it. This is the pattern
`storage/effector_consents.consents_db_path` already uses, and that docstring
gives the reason: a directory the adversary can also write cannot be trusted
in place.

`_universe_dir(uid)` stays an identifier (`data_root / uid`) for code that
still passes a center around by path. Platform code never does filesystem I/O
under it. A guard (D6) enforces that.

## D2. Platform files that move (answers a)

Each of these moves from `<data>/<center>/<name>` to
`<data>/.universe-sidecars/<center>/<name>`, under the same name, so the key is
the center. The center maps to exactly one account through broker admission,
so account + center is implied.

The list comes from the 2026-10-16 inventory of platform readers and writers
(#5167 thread). A name stays here only if its writer is platform code:

- **SQLite stores** (each with its `-wal`/`-shm`): `.subscription_state.db`,
  `.usage_ledger.db`, `.external_write_receipts.db`, `.pending_requests.db`,
  `.wiki_write_back_destination_markers.db`, `.authoring.db`,
  `.idempotency.db`, `.conversation_memory.db`, `.conversation_attention.db`,
  `.runs.db`, `.automations.db`, `.tinyassets.db` (per-center copy, if
  present), `rules.db`, `steering.db`, `checkpoints.db`.
- **Files:** `.pause`, `.worker_supervisor.json`, `.worker_supervisor.<id>.json`,
  `auto_ship_attempts.jsonl`, `.auto_ship_attempts.jsonl.lock`,
  `provider_definitions.json`, `.runtime_status.json`,
  `.engine_mcp_config.json`, `.engine_mcp_http_routes.json`, `.soul.lock`,
  `.provider-assignment-admission.lock`.
- **Directories:** `.runtime/`, `.provider-authority/`, `.oauth-refresh/`,
  `.workspace-staging/`.
- **Already in the sidecar:** `.effector_consents.db`, seed locks. The legacy
  in-center copy, `.effector_consents.db.premigration`, is deleted by the move
  once `platform_state_move` has run, and is never relocated.

**Owner content:** `dispatcher_config.yaml`, `activity.log`, `status.json`,
`soul.md`, `soul_versions/`, `config.yaml`, `wiki/`, `canon/`, `output/`,
brain files, `agents/`, `notes*`. These are already in the box after the C6
copy. Their host copies are deleted with the host directory.

A name found in a host center during the move that is on neither list stops
the move (refuse, nothing written). The census names it, so the list is
complete before cutover rather than guessed.

## D3. Owner-facing reads (answers b)

An owner-facing read (the app's owner door, MCP read tools, inspect/status
for one center, first contact, the soul, the file browser) runs as the
**requesting account**:

- If the request already holds an admitted session binding for that center,
  use it (the agent's own turn).
- Otherwise bind with `provider.bind(center, account_id=<requester>)`. Broker
  box admission refuses unless the requester is the center's admitted owner,
  so the box host is the only isolation check. A refused bind returns that
  surface's not-found or unavailable shape, with a reason. It never retries as
  another account and never falls back to a host path.
- A bind wakes or attaches the box (`ensure_awake`). No live session is
  needed beforehand.

There is no platform-wide bind-as-owner. Reads that had none of their own
change as follows:

- **Universe listing word counts:** dropped from the listing (`null` with a
  reason). Single-center inspect computes them as the requester.
- **Branch-delete loop scan:** reads only the requester's own centers. The
  founder rule is that deleting a branch never affects other users. A public
  branch other owners copied is theirs. The "other owners blocking" count
  from #5168 is not carried.
- **Automation brain context:** runs inside the owner's run, which holds the
  session binding.
- **Effect authority from soul:** runs inside the owner's run binding. With no
  binding, the effector refuses (fail closed). It does not read as the owner.

**Open for the founder:** a co-admin of a shared center is not the box owner,
so the box refuses them. Until the founder rules, co-admin content reads fail
with `not_owner`, and sharing goes through published artifacts.

## D4. Scheduler pause with no box running (answers c)

`.pause` is a platform file in the sidecar. The scheduler (`tick._paused`),
`research_turn`, the pause/resume/status actions, heartbeats and the ledger all
read and write the sidecar through `platform_center_path`. No box is involved.
A missing `.pause` means not paused.

The sidecar is created by `admit_center` in the same step as admission, and is
removed only by account deletion. A missing sidecar for an admitted center
therefore breaks an invariant: `_paused` returns `True` and logs an error
(fail closed).

## D5. Migration inside the C6 one-pass move (answers d)

`box_layout_migration.apply` gains one step:

1. Read-only **preflight**, before the daemon stops: classify every entry of
   every admitted host center against D2. An unknown name refuses. A symlink
   or special file among platform names refuses. The sidecar must not already
   hold a different file of the same name.
2. The box copy excludes D2 platform names, so they are never copied into the
   box.
3. Within the one-shot window, after the box copy is verified, rename each D2
   name into the sidecar (same filesystem, atomic per entry). Verify each
   SQLite store with `PRAGMA integrity_check` and compare file digests.
4. Remove `<data>/<center>/`. The paired pre-move backup is the restore point.
5. The marker records `platform_sidecar: v1`. A restore of the paired backup
   returns the pre-move layout, as today.

**Rehearsal:** `scripts/rehearse_box_move.py --archive` on the private
production copy must print, per center:
- every D2 name moved, with digests equal;
- `integrity_check=ok` for each store;
- no host center dir left;
- no unknown name.

It also runs a post-move read of the subscription tier, pending requests and
pause, through the new resolver. The synthetic rehearsal does the same on
fixture data.

## D6. Deleted and guarded

**Deleted:**
- from #5168, which never lands: `center_platform_files` host I/O,
  `ensure_host_stub`, `center_admitted`-as-existence, `owner_box_reads`'
  admitted-owner bind, the `_universe_dir` symlink refusal, and
  `HOST_CENTER_ENTRIES`/`HOST_CENTER_MOVED` with their guard;
- on main: every platform `Path` operation on `<data>/<center>/...` (the
  inventory's roughly 50 existence gates and roughly 40 names).

**Guard** (`tests/test_no_host_center_io.py`): an AST scan of `tinyassets/`
fails on any filesystem call whose path is built from `_universe_dir(...)`,
`data_dir() / <center>` or `base_path / universe_id` other than through
`platform_center_path` or a box-routed helper. The allowlist holds only the
migration and account-deletion modules.
