---
severity: P1
title: A jailed process can fill the shared disk; the storage quota only counts its writes afterwards
filed: '2026-10-01'
summary: 'The account storage quota is enforced only on platform-mediated writes. Bytes written inside the tool jail or the provider jail are never gated, and are counted only when the owner''s next gated write re-measures. The tool jail stops at a volume-wide 1 GiB floor, and the provider jail has no disk limit at all. /data is a Docker volume on the droplet''s root ext4, so one universe can fill the disk that every user, Docker and the OS share. Fix by construction: the sealed box (per-command-center disk), founder-approved 2026-10-01. Interim per-launch guards shipped in tinyassets/jail_disk.py.'
---

# A jailed process can fill the shared disk

**Filed:** 2026-10-01, from the gpt-6-astra refute of #4245.
**Verified:** 2026-10-01 against origin/main `9ff72abc` (code), plus a read-only check of production through `scripts/droplet.py ssh` (`df -T`, `/proc/mounts`, `docker inspect`).
**Severity:** P1. One user's writes take down every other user (the cross-user floor).

## What is true

**The quota is counted everywhere but gated only where the platform writes.**
`tinyassets/storage_accounting.py` keeps one pool per account. `reserve()` admits or refuses a write before it lands, and its callers are the platform write paths: wiki pages, uploads, run files, branch/version writes, workspaces, memory stores and the UI library. The tool jail (`universe_tools.py`), the engine tool server (`engine_mcp_server.py`) and the provider launch path (`providers/`) never call `storage_accounting`.

A jailed write lands in the universe directory, which is the `universe_files` store, a `_walk_bytes` of the directory. Nothing measures that store on a schedule. The only re-measure happens inside `reserve()`, for rows older than `FRESH_BEFORE_REFUSE_S`. So a universe can write without limit through bash or a provider CLI, and the overage first shows up on the owner's next platform write, which is then refused. The bytes are already on disk by then.

**The tool jail's disk limit is volume-wide, not per universe.**
`ToolLimits.min_free_disk_bytes` is 1 GiB and `min_free_inodes` is 4096. They are checked before launch and polled every 0.2 s by `_supervise`, which kills the jail with `disk_limit`. That stops the volume at 1 GiB free, but by then one universe has consumed everything above the floor. Every other universe's tool calls then refuse with "the shared disk is nearly full" until someone frees space. `RLIMIT_FSIZE` (32 MiB) limits the size of one file, not the total, so many small files pass it. The inode floor only fires at 4096 inodes left.

**The provider jail has no disk limit at all.**
`providers/provider_jail.py`:
- `default_view` binds the whole universe directory read-write.
- `jail_argv` sets no rlimits, no cgroup, no free-disk floor and no supervisor poll.

A provider CLI run is bounded only by its own turn.

**`/tmp` in both jails is an unsized tmpfs.**
`jail_argv` emits `--tmpfs /tmp` with no `--size`, so the kernel default applies (half of RAM). Those writes are memory, not disk. On the 2 GB box, a jail filling `/tmp` is memory pressure for the daemon, and the tool jail's process-tree RSS check does not see tmpfs pages.

**In production, the shared disk is the root disk.**
`/data` is the Docker volume `tinyassets-data` at `/var/lib/docker/volumes/tinyassets-data/_data`, on `/dev/vda1`: ext4, 49 GiB, 40% used on 2026-10-01, kernel 6.1. The same filesystem holds the OS, journald and Docker's images. A fill does more than refuse other users' writes: it can stop the host (no room to pull an image, write a log, or run SQLite WAL checkpoints for every user). Compare `2026-09-24-p0-disk-full-repair-uses-broad-prune.md`.

## Fix by construction: the sealed box (superseded the volume plan)

Founder-approved 2026-10-01: every command center owns its own fixed-size disk
inside its own box. A box filling its disk then affects only itself, which is
the cross-user floor by construction. The earlier proposal here (per-account
kernel project quotas on a dedicated data volume) is superseded and will not
be built.

## Interim guards (shipped; delete with the box)

All in `tinyassets/jail_disk.py`, so it goes in one place:

- every tmpfs a jail mounts is sized (`/tmp` 256 MiB, masks 16 MiB);
- both jails refuse to start below the volume floor (1 GiB / 4096 inodes) and
  are killed when a run crosses it -- the provider jail had neither;
- each launch measures its command center fresh and reserves what still fits
  in the owning account, capped at 1 GiB per launch, renewed while it runs;
- a run is killed (`storage_limit`) once everything it can write (including
  `.runtime` and `workspaces/`) grew past that bound; a volume-growth signal or
  a 5 s timer triggers the walk;
- a full account still runs, on a 16 MiB grace budget with a visible notice, so
  the owner can free space through their own agent.

What it does not close: one call can overshoot by its write rate times the
poll interval, and the volume floor still lets one account consume shared
space down to 1 GiB before anything stops it.

## Resolution

Resolved when command centers run in their sealed boxes: a box that fills its own disk leaves every other box writable (proof: a real test that fills one box and writes in another). Delete this file, and `tinyassets/jail_disk.py` with it, in that PR.
