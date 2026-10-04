# Leave ordinary-write room at launch admission, preserving recovery

This bounded replacement for the held policy in PR #4403 starts from main
`b8ffe66094f82495688707453816959381ff254f`, including #4418's atomic admission,
checked renewal and settlement fixes. It does not modify the held source branch.

Two newly admitted jail launches can reserve the entire 2 GiB free-account pool
even when retained files total zero or 100 MiB. A real 48 KiB UI save then fails
solely on pending reservations. The new regression reproduces that refusal in
all four combinations of retained size and same/different command center.

`reserve_fitted` accepts an optional nonnegative headroom value and subtracts
it while fitting under the existing admission transaction. Jail admission asks
to leave 16 MiB unreserved. Other callers retain the default zero holdback.
Quota, ownership, replacement credit, minimum, checked renewal, settlement,
physical disk/inode floors and provider/process paths retain their existing
contracts. No database shape or public tool surface changes.

Unlike #4403, this keeps `max(fitted, GRACE_BYTES)` and both grace fallbacks.
A full account retains the existing 16 MiB allowance for provider startup and
cleanup. Existing grace/recovery tests and assertions remain; their scaled
100 KiB fixture explicitly sets the new holdback to zero. A separate suite
exercises the production 16 MiB holdback and full-account grace together.

## Limits

The room exists at admission, not for the whole launch lifetime. Jail growth
and remeasurement can consume it; measurements can conservatively count both
written bytes and still-pending launch reservations. Grace can exceed a fitted
reservation or be unreserved, as on main. Workspace checkout can still reserve
the pool. The existing full-storage notice may also appear when the last
16 MiB is being held back. This is a bounded starvation mitigation, not a hard
filesystem quota, aggregate runtime bound or guarantee of all recovery sessions.

## Evidence

2026-10-04, Windows/Python 3.14, isolated checkout. Before product changes:
`python -m pytest -q -p no:randomly tests/test_jail_admission_headroom.py -k two_new_launches --tb=short`
produced four real `StorageRefused` UI-save failures on pinned main.

After the change:
`python -m pytest -q -p no:randomly tests/test_jail_admission_headroom.py tests/test_jail_disk.py tests/test_storage_accounting.py --tb=short`
produced 75 passed and one POSIX skip. The recovery case was then tightened to
force actual disk walks and rerun separately. Hosted Linux/jail proof remains
required; local skipped tests are not execution evidence.

Pre-build independent Claude review accepted the bounded mechanism and required
the admission-only qualification and explicit scaled-fixture adjustment above.
Its broader measured-plus-pending and recovery limitations remain, rather than
being represented as solved by this patch.
