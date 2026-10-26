# ADR-026: Boxes Sleep When Idle and Are Admitted by Size

## Status

Accepted (founder, 2026-10-26). Clarifies ADR-019's lifecycle rule and
amends ADR-022's interim "stop on idle".

## Date

2026-10-26

## Context

- **The rule:** ADR-019 says a box stays awake while work runs and suspends
  when none does.
- **What shipped:** the C6 cutover (#5182) set
  `TINYASSETS_BOX_IDLE_SECONDS=900` and capped awake boxes at a flat 6 per
  host (`TINYASSETS_BOX_AWAKE_CAP`), sized for each box's 2 GiB ceiling
  against boxhostd's `MemoryMax=7G`.
- **The effect:** together these made capacity look like a limit of about
  24 short sessions an hour across the whole fleet.
- **Measured in gVisor on 2026-10-26** (a workspace run, not production):
  checkpoint 0.10 to 0.12 s; restore to first exec about 0.15 s; a 99 MiB
  image per box; about 3.7 s cold start and 95 to 145 MiB awake for today's
  box app.

## Decision

- **Zero idle tail.** A box sleeps as soon as no turn, tool, CLI, browser or
  resident process is running. There is no idle timer.
- **Admission by memory.** Each wake reserves the thin class (256 MiB)
  against the host's memory budget, and every awake box is charged what its
  cgroup reports, never less, from the next lifecycle pass. A wake cannot
  know its class: a CLI or Chromium starts later, at the agent's choice.
  A box that grows past the budget before then is killed by the kernel
  first (`oomScoreAdj`), never the host. Over budget, an interactive wake
  evicts an idle box and a scheduled wake waits (#5319).
- **Suspend and restore** (ADR-022's P6) is part of the main build-out.
  Checkpoint images are kept for recently active owners; others cold-start.
- **The thin reservation** starts from the measured idle box (95 to 145 MiB
  awake) and is replaced by metering receipts (proposed row N12).

## Consequences

- `idle_limit` and the flat `awake_cap` are deleted.
- Stopping a box is not copying it. An idle stop waits only for the
  ten-second post-wake lease. Its off-host volume snapshot, which used to
  come with every sleep, waits until the box has been stopped 15 minutes;
  it is taken at once when the owner has no snapshot or the last is a day
  old. Explicit, deploy and crash stops still copy at once (#5316).
- Hosting capacity scales with simultaneous work, not with users.
- More box hosts are added behind ADR-012's cell seams when receipts show
  the need.
- Build order: `openspec/changes/device-first-build/`.
