# Ordinary-write headroom with provider recovery

## Reproduction and design

Base: current main `d4a3a422730cd8f9e944f33b3c93debb25e1e2e7`, containing
merged #4418. Reviewed #4403 at `85b0f5f7eb` and its audit; retain main's
atomic admission, measurement sequencing, checked renewal, and recovery policy.

Before implementation, the new test
`test_overlapping_launches_leave_room_for_an_ordinary_ui_save` failed:
two real `open_budget` calls reserved 1 GiB each against the real 2 GiB quota;
`admitted(..., nbytes=49_101)` raised `StorageRefused` with used=quota=2 GiB.
Command: `python -m pytest tests/test_jail_reservation_headroom.py -q
--basetemp C:/Users/Jonathan/AppData/Local/Temp/wf-headroom-repro` (1 failed).

Reservation policy: add optional nonnegative `headroom=0` to `reserve_fitted`.
Inside its existing transaction, fit at most
`min(cap, max(0, quota - used - headroom) + credit)` and reserve only the
increment over replacement credit. Default workspace behavior is preserved.
Jails request `WRITE_HEADROOM_BYTES = 16 MiB`; ordinary writes keep their
existing quota gate. No schema, quota, provider, or supervision changes.

Recovery policy: retain `max(fitted_bound, GRACE_BYTES)` and main's unreserved
`GRACE_BYTES` fallback when the fit is refused. With remaining quota at or below
headroom, no positive launch reservation fits; recovery still starts, can write
bounded session files, reaches cleanup, and stops on excess growth or a shared
disk/inode floor. Grace is a per-launch growth floor, NOT an extra ledger
reservation or an allowance added to a large fit. It does not reserve the
protected write allowance. This preserves main's intentional bounded recovery
exception, including accounting-unavailable and unattributed behavior.

The guarantee concerns speculative reservations only: actual ordinary writes,
new measurements, or recovery growth can use remaining quota. Concurrent grace
launches, polling overshoot, release-before-remeasurement settlement, and TTL
behavior remain existing limitations; this is not a hard filesystem quota.

Owner refusals distinguish measured, reserved in-flight, and committed pending
remeasurement components. Components can conservatively overlap until reconciled.
Other viewers continue receiving the fixed generic record, without owner counts,
scope paths, or reservation identifiers.

## Validation

Added `WRITE_HEADROOM_BYTES=0` to the existing 100 KiB jail fixture so its exact
allocation and lease assertions remain meaningful. Every existing test body,
name and assertion is unchanged (AST comparison: 32 accounting and 24 jail
test functions). The new headroom module uses the real 16 MiB constants, covers
startup before cleanup with remaining quota 0, 1 KiB, 16 MiB, 16 MiB + 1 KiB,
and 32 MiB, and tests six overlapping launches plus an ordinary write.

Interim checks:
- Accounting + headroom: 49 passed.
- Related storage gates/registry/slice + tool/provider jail modules on Windows:
  53 passed, 29 POSIX skips; skips are not jail proof.
- Original jail module before adding the fixture setting: 10 failed, 19 passed,
  1 POSIX skip, because its entire scaled quota was below production headroom.
- Mirror build/import probe and Ruff passed; both mirrors are byte-identical.
- First Linux invocation stopped during snapshot creation (`tar: .: file
  changed as we read it`); it ran no tests. Restarted against stable files.
- Linux accounting/headroom + related storage and tool/provider jail modules:
  130 passed, 1 failed, zero skips, Python 3.11.16, bwrap 0.12.0, uid 1001.
  Failure: `test_a_background_run_reads_and_writes_its_notes_while_a_database_closes`
  lacked its SQLite `-shm` precondition. Repeated the single case with both
  canonical changed modules replaced INSIDE the oracle container by their exact
  main/HEAD blobs: same failure (1 failed). No worktree source was reverted.
  Main blobs were read-only mounted from an external temporary directory;
  oracle snapshot/setup/security options were unchanged. This pre-existing
  failure is handed off in `docs/concerns/2026-10-04-linux-jail-shm-precondition.md`.

Final checks:
- Required three modules on Windows: 78 passed, 1 POSIX skip (32.79 s).
- Required three modules plus provider jail on Linux: 86 passed, zero skips
  (20.42 s). Includes every existing #4408/#4418 recovery/renewal/accounting case.
- Ruff on all seven changed Python files and `git diff --check`: passed.
- No existing test renamed, removed, or changed; only the fixture setting and
  new tests were added. The fixture change is explicit above, not a claim that
  existing test setup is byte-identical.

Commands (all pytest temporary roots outside the repository):

```powershell
python packaging/claude-plugin/build_plugin.py
python -m ruff check tinyassets/storage_accounting.py tinyassets/jail_disk.py tests/test_storage_accounting.py tests/test_jail_disk.py tests/test_jail_reservation_headroom.py packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/storage_accounting.py packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/jail_disk.py
python -m pytest tests/test_storage_accounting.py tests/test_jail_disk.py tests/test_jail_reservation_headroom.py -q --basetemp C:/Users/Jonathan/AppData/Local/Temp/wf-headroom-final
python -m pytest tests/test_storage_gates.py tests/test_storage_registry.py tests/test_storage_registry_complete.py tests/test_storage_slice_gates.py tests/test_universe_tools_jail.py tests/test_provider_universe_jail.py -q --basetemp C:/Users/Jonathan/AppData/Local/Temp/wf-headroom-related
python scripts/linux_oracle.py -- tests/test_storage_accounting.py tests/test_jail_reservation_headroom.py tests/test_storage_gates.py tests/test_storage_registry.py tests/test_storage_registry_complete.py tests/test_storage_slice_gates.py tests/test_universe_tools_jail.py tests/test_provider_universe_jail.py -q
python scripts/linux_oracle.py -- tests/test_storage_accounting.py tests/test_jail_disk.py tests/test_jail_reservation_headroom.py tests/test_provider_universe_jail.py -q
git diff --check
```

Oracle supplies `--basetemp=/tmp/b`. No sub-agents, external agent processes,
new worktrees, full-suite run, PR creation, or deployment performed.
