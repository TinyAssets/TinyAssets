---
name: scratch
description: Where to clone repositories, install packages and build - your scratch directory $TA_SCRATCH (/scratch/<agent>), kept between runs but never synced or counted in your owner's storage. Use before any git clone, package install, build or large download.
metadata:
  ta-starter: "scratch@1"
---

# Scratch

Your scratch is `$TA_SCRATCH`, which is `/scratch/<your agent name>` on your sandbox's disk.
- It is never synced and never counts in your owner's storage. It is kept between runs as a
  cache, but it can go at any time: unused for a while, or when the disk is short of room. You
  are told when it was cleared.
- `/workspace` is your owner's durable files: everything there is kept, versioned and counted.

## Use it for
- `git clone` (a shallow clone, `--depth 1`, or `--filter=blob:none`, when the task allows),
  then `git fetch` on later runs instead of cloning again.
- Package installs, virtual environments, build output and caches (`node_modules`, `.venv`,
  `dist`, `target`). Your tools' caches already point there.
- Large downloads you only need for the task.

## Keep the result, not the work
- The durable result of work in scratch is a push to the owner's remote, or a small file or
  patch in `/workspace` (a report, a `git format-patch`, a built artifact your owner asked to
  keep).
- Never copy a dependency tree, a clone or a cache into `/workspace`.
- Before a long build, check the free space (`df -h "$TA_SCRATCH"`); when it is short, clean
  your own old build output first.
- If `$TA_SCRATCH` is not set, use `$TMPDIR`, which is gone when the run ends, and tell your
  owner that scratch was missing.
