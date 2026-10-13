# ADR-022: The Box Backend Is gVisor on the Existing Droplet

## Status

Accepted. Closes the question ADR-019 left open with evidence; supersedes
ADR-012's driver order ("Firecracker, gVisor where KVM is unusable").

## Date

2026-10-13

## Context

C6 (`openspec/changes/shape-convergence/tasks.md`) starts with an ADR that
settles which of three backends is the one behind `BoxProvider`:

- (a) the shipped **owner cell**: a permanent per-owner UID plus a `bwrap`
  namespace jail on the shared kernel, launched per path by the mapper
  (`deploy/role_owner_launcher.py`, 1,202 lines) into 33 `role_*` cells;
- (b) **gVisor** (`runsc`): a user-space kernel per owner;
- (c) **Firecracker**: a microVM per owner.

Measured facts, not estimates:

- Production is one DigitalOcean droplet (`s-4vcpu-8gb`, 49 GiB volume at
  53 % used, 24 GiB free on 2026-10-13). The daemon container runs with
  `seccomp:unconfined` so `bwrap` can unshare (`deploy/compose.yml:131-138`):
  today the box privilege lives in the daemon.
- S0 (`box-kvm-validation` run 36980072338, 2026-10-02, a throwaway
  `s-4vcpu-8gb`, "DO-Regular" CPU, kernel 6.12): `/dev/kvm` present and
  `kvm_nested=Y`. DigitalOcean does not support nested KVM; it appears on
  Intel hosts only and a droplet migration can remove it.

| Measured on S0 | gVisor release-20260928 | Firecracker v1.17 |
|---|---|---|
| Cold start to exec | 0.22 s | 0.98 s |
| Exec round trip p95 (10 boxes under load) | 48 ms (57 ms) | 7.7 ms (9.4 ms) |
| Idle awake box | 66 MiB PSS | 71 MiB RSS, 0.6 % of a core |
| Suspend (checkpoint) | 65 ms, 0.3 MiB on disk | 4.4 s, 512 MiB on disk |
| Wake (restore) to first exec | 0.22 s | 77 ms |
| 10 awake boxes | 230 MiB | 753 MiB, 1.6 % host steal |

The owner cell was not benchmarked: a `bwrap` exec is a fork (milliseconds)
and it has no checkpoint or restore at all.

## Decision

**gVisor is the one production backend.** One `runsc` sandbox per owner
account, created, woken, suspended and destroyed by `boxhostd`, a host
service outside the daemon container. Everything the floor needs follows
from the evidence:

- **Cross-user floor.** Two independent boundaries (the Sentry, then a
  seccomp-confined host kernel surface) replace one (the shared kernel
  behind `bwrap`). A kernel privilege escalation, the only thing that
  defeats the owner cell, must first escape the Sentry. Firecracker's
  hardware boundary is stronger still, but on DO it is nested under a
  hypervisor DO does not support, so it is not a floor we can stand on.
- **Runs today, no new spending.** `runsc` needs no KVM (systrap), installs
  on the droplet through the deploy workflow's existing SSH step, and the
  bench ran on the production size. The daemon container then drops
  `seccomp:unconfined`: box privilege moves out of the daemon.
- **Suspend is cheap enough to be the default.** 65 ms and 0.3 MiB per idle
  box make "checkpoint after 30 s idle, restore in 0.2 s" the normal
  lifecycle (design §4). Firecracker's 512 MiB memory image per suspended
  owner would fill the 24 GiB free in under 50 owners, and its 4.4 s
  snapshot makes the 30 s idle rule a storm.
- **Direction 8.** All three run on cloud compute with no host machine on a
  path. Only gVisor and Firecracker let a session outlive a daemon deploy
  (the box is not in the daemon's cgroup) and give N6 "undo" a box-level
  snapshot. The owner cell gives neither.
- **C9 native CLIs.** Node and headless Chromium run under gVisor (it is
  Cloud Run's first-generation sandbox); P3 and P4 prove ours in the Linux
  oracle. The 48 ms exec round trip is per `bash` call, not per model
  token, so it is invisible against a model turn.
- **Migration.** The owner cell is the smallest change and the wrong one:
  keeping it keeps the mapper, the per-class binds and the kernel the
  users share, and that is the path that drifted into 33 cells. gVisor
  deletes all 33 (design §8). Firecracker would add a guest kernel, a rootfs
  and a vsock agent to maintain for the same deletions.

**The owner cell is deleted, not wrapped** (ADR-019 holds). `bwrap` stays
only as the Linux oracle's test jail until P2 removes it with
`node_sandbox.py`.

**Firecracker stays an allowed second driver** behind the same `BoxProvider`
contract, built only when both hold: a measured need gVisor cannot meet (an
exec latency the product feels, or a vulnerability class the Sentry does
not cover) and a host with supported KVM. That host is new spending (a
bare-metal box host, about $77 a month at today's quotes) and a founder
decision at that time; nested KVM on a droplet is never the production
path.

**`BoxProvider` attaches once, at execution admission.** `session.start`
calls `admit(owner, seat)` once, binds `BoxProvider.bind(owner)`, wakes the
box and starts the loop process inside it (design §3). Child sessions
inherit the handle. No tool, node, CLI, decoder or git path binds a box of
its own; the per-path admissions (`role_scope`, `role_center_admission`,
the mapper's per-class bind, the tool-slot queue, the provider launch gate)
are deleted in the PRs that absorb their paths.

## Slices

The order is `openspec/changes/one-box-per-owner/tasks.md` §1, one PR each,
deletions in the same PR, measured net ≤ 0. P1 shipped (#4630). This ADR
changes it in two places, recorded there: P2 installs the pinned `runsc`
and the `boxhostd` unit through the deploy workflow in the same PR as the
driver, and the Firecracker trigger above replaces "on a measured need".

| PR | Leaves one path for | Floor round |
|---|---|---|
| P2 driver, admission once, tool family (the maintenance window) | tools, files, git, nodes, media | cross-user, data |
| P3 sessions in the box | the loop, children, residents, CLI packages | credential custody |
| P4 browser and the N4 view hook | Chromium | cross-user view |
| P5 egress gate (N5 point) | all box egress | exfiltration |
| P6 lifecycle, snapshot hook, mapper retired (`role-cells` = 0) | suspend, restore, destroy | data loss |
| P7 platform tables into `storage/` (S10) | platform state | none |
| P8 off-region backup and DR drill (S27) | backup | data loss |
| P9 close: S8, S10, S27, N10 converged; `target-architecture` deleted | | none |

## Needs the founder

- **No spending to start P2.** `runsc` is Apache-2 software on the droplet
  we already pay for.
- **P2's maintenance window:** pick the time; a verified full backup
  precedes it (design §11). Irreversible for the cell layout, not for data.
- **P8:** the off-region bucket, about $0-5 a month (existing
  `docs/host-actions.md` row). Blocks only P8.
- **Later, measured, not now:** awake-box capacity on an 8 GiB host shared
  with the daemon's 4 GiB cap is a handful of boxes until P3 shrinks the
  daemon; saturation shows as wake-queue wait, which the owner sees. A
  larger droplet, a DO volume for box storage, or a bare-metal box host
  (Firecracker's precondition) is each a spending decision raised with the
  measurement that needs it.

## Consequences

- ADR-019's driver choice now rests on S0's numbers; its status line cites
  this ADR. ADR-012's "Firecracker first" wording is superseded.
- `.github/workflows/box-kvm-validation.yml` and `scripts/box_bench/` have
  done their job; P9 deletes them with `target-architecture`.
- The `role-cells` guard (`check_shapes.py` S8, ceiling 26) falls with each
  PR and reaches 0 in P6.
