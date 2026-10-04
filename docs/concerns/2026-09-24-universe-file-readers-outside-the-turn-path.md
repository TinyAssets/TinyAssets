---
severity: P1
title: Universe-file readers outside the turn path
filed: '2026-09-24'
summary: a workflow provider jail binds the universe read-write (hidden root FILES included) and allows symlink; daemon file reads/writes now go through universe_files read_data_path/write_data_path behind a shrink-only guard, but pinned raw sites and per-universe SQLite still follow a planted link
---

# Universe-file readers outside the served turn path (harness S1)

**Found:** 2026-09-24, harness S1 review round 2 (PR #3972). **Severity:** P2
today (none of these files is agent-writable), P0 the moment a slice widens
what the agent may write. **Owner:** whoever widens the tool jail's
read-write set.

## Context

Since S1 the universe agent writes its own folder, so a file under a universe
dir is untrusted input to any daemon code that reads it. Three review rounds
found the same class (a raw `read_text`/`yaml.safe_load` with no bound and no
no-follow). The fix has two halves:

1. **What the agent can write is small and explicit.** The tool jail mounts the
   universe root read-only and binds read-write only
   `universe_tools.AGENT_BRAIN_FILES` + `AGENT_HARNESS_DIRS`; every hidden root
   entry (credential vault, `.runtime`, consent/usage/receipt DBs) is masked.
   `tests/test_universe_tools.py::test_agent_owned_paths_are_pinned` pins that
   set.
2. **Every daemon read on the turn path goes through `universe_files`**
   (link-free, bounded, alias-free YAML), enforced by
   `tests/test_universe_file_reads_are_bounded.py` over `TURN_PATH`.


## What changed on 2026-10-01

The premise above ("none of these is agent-writable") is **false for a workflow
provider**. `providers/provider_jail.default_view` binds the WHOLE universe
read-write and masks only hidden *directories*, so hidden root *files*
(`.runs.db`, consent/usage DBs, `.credential-vault.json`) and every visible
path can be replaced by a link, and the jail allows `symlink`
(`docs/concerns/2026-10-01-provider-planted-link-reads-another-universe.md`).
The tool-jail half of the fix no longer bounds the daemon's exposure.

PR #4254 (`fix/daemon-link-refusing-writes`, superseding #4247) made two things true:

- **One reader, one writer.** `universe_files.read_data_path` /
  `write_data_path` (built on `read_universe_file` / `write_universe_file`)
  walk from the data dir with no link at any component; writes are temp +
  rename inside the verified directory. A refused read RAISES
  `UniverseFileError` -- never "absent", which a read-modify-write would
  overwrite. Converted: `api/helpers` `_read_json` / `_read_platform_text`
  (inspect, activity, events, memory-scope, every `_read_json` caller),
  `read_output`, `read_canon`, `read_source`, canon meta sidecars + manifest,
  `ingestion` canon/source/manifest writes (`canon_io`, `core`; a linked canon
  root is refused in `resolve_within_canon`), `dispatcher_config.yaml` (was a
  cross-user WRITE), requests, ledger, notes, `work_targets`, premise mirror,
  `.pause`, heartbeats, `soul/*.yaml`, the config probe, `status` activity,
  the `daemon_overview` tail, `soul.md` + `soul_versions/`, the OKF seed,
  enrichment signals, and every `api/wiki.py` write, append, exclusive
  create and delete (`unlink_data_path`). A universe wiki root or page
  path is no longer `resolve()`d before containment; a linked root refuses.
- **A shrink-only guard.** `tests/test_universe_path_io_guard.py` pins the raw
  file operations left in every module that mentions a universe or data-dir
  path, keyed by enclosing function and operation (reads, writes, rename,
  unlink, os.replace, shutil). A new one fails; a converted one must be
  deleted from the pin. The pin is the sealed-box migration checklist.
  It does not count `mkdir` or `Path.replace` (indistinguishable from
  `str.replace`).

## Still open

1. **Per-universe SQLite.** `sqlite3.connect` follows a link at the file. A
   planted `.runs.db -> /data/<B>/.runs.db` opens B's database in A's context
   (read and write). Open per-universe DBs with the `nofollow=1` URI
   (`SQLITE_OPEN_NOFOLLOW`), parent dirs being platform-owned. Not covered by
   the guard, which scans file calls only.
2. **The pinned raw sites** in `test_universe_path_io_guard.PINNED`. Many are
   platform files in no universe (package assets, the data root's own files);
   each still needs a look. `add_canon_from_path` reads any absolute server
   path when `TINYASSETS_UPLOAD_WHITELIST` is unset.
3. **Residual races.** `iter_canon_files` enumerates names and sizes under the
   resolved canon root (a root swapped between the link check and the listing
   discloses names); `_source_file_entry` stats the resolved target.
4. **Windows.** The non-POSIX fallback checks with `lstat` and then opens by
   path; it is the single-tenant tray, so the cross-user guarantee is POSIX-only.

The by-construction close is fix direction 2 (provider-egress): run codex with
its own sandbox off inside the provider jail so the jail can refuse `symlink`.

## Resolution rule

Route every new daemon read or write of a path under the data dir through
`tinyassets.universe_files`; the guard enforces it. Delete this file when the
pin is empty of universe paths, SQLite opens no-follow, and the races are closed
-- or when the provider jail refuses `symlink`.
