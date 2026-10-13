# ADR-022: The Box Backend Is gVisor on the Existing Droplet

## Status

Accepted. Closes the question ADR-019 left open; supersedes ADR-012's driver
order ("Firecracker, gVisor where KVM is unusable"). Revised after one
cross-family round (2026-10-13): S0 is quoted for what it measured, the
production facts are P2 gates, and P2 is the single cutover.

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

Facts on `origin/main`:

- Production is one DigitalOcean droplet (`s-4vcpu-8gb`; 49 GiB volume at
  53 % used, 24 GiB free on 2026-10-13). The daemon container runs with
  `seccomp`, `apparmor` and `systempaths` unconfined so `bwrap` can unshare
  (`deploy/compose.yml:128-138`): today the box privilege lives in the
  daemon.
- DigitalOcean does not support nested KVM. It appears on Intel hosts and a
  droplet migration can remove it.

**What S0 measured** (`box-kvm-validation` run 36980072338, 2026-10-02): on
a throwaway `s-4vcpu-8gb` ("DO-Regular", kernel 6.12, `/dev/kvm` present,
`kvm_nested=Y`), each driver ran a trivial process (`sleep`, `true`) over an
empty bind mount, outside any container:

| S0, trivial workload | gVisor release-20260928 | Firecracker v1.17 |
|---|---|---|
| Cold start to exec | 0.22 s | 0.98 s |
| Exec round trip p95 (10 boxes) | 48 ms (57 ms) | 7.7 ms (9.4 ms) |
| Idle box | 66 MiB PSS | 71 MiB RSS, 0.6 % of a core |
| Checkpoint | 65 ms, 0.3 MiB | 4.4 s, 512 MiB |
| Restore to first exec | 0.22 s | 77 ms |

S0 proves three things and nothing more: `runsc` runs on DO's kernel
without KVM; its start, exec and checkpoint mechanisms work at those
latencies for an idle process; Firecracker works there only because nested
KVM happened to be present. It does not measure a real agent, Chromium, an
owner tree, broker traffic, the production container layout, the daemon's
4 GiB cap or cross-user isolation. Those are P2a gates below.

The owner cell was not benchmarked: a `bwrap` exec is a fork, and it has no
checkpoint or restore at all.

## Decision

**gVisor is the one production backend.** One `runsc` sandbox per owner
account, created, started, stopped and destroyed by `boxhostd`, a host
service outside the daemon container.

- **Cross-user floor (design, proven at gate G2).** Two independent
  boundaries (the Sentry, then a seccomp-confined host kernel surface)
  replace one (the shared kernel behind `bwrap`). A kernel privilege
  escalation, the only thing that defeats the owner cell, must first escape
  the Sentry. Firecracker's hardware boundary is stronger, but on DO it is
  nested under a hypervisor DO does not support, so it is not a floor we
  can stand on.
- **No KVM, no new software spend.** `runsc` is Apache-2, needs no KVM
  (systrap), and installs on the droplet through the deploy workflow's
  existing SSH step. Whether it runs correctly on the production host under
  the production limits is gate G1, not an assumption.
- **Suspend economics are structural.** Firecracker's checkpoint is the
  whole guest memory regardless of use (512 MiB at the bench size): the
  24 GiB free holds under 50 suspended owners, and the 4.4 s write makes
  an idle rule a storm. gVisor's checkpoint is the box's resident set
  (0.3 MiB for an idle shell; a real box's size is measured in P6, which
  builds suspend). Until P6 a box is stopped on idle and cold-started
  (0.22 s for an idle box in S0; gate G3 for a real one).
- **Direction 8.** All three run on cloud compute. Only a box outside the
  daemon's cgroup lets a session outlive a daemon deploy and gives N6 a
  box-level snapshot. The owner cell gives neither.
- **C9 native CLIs.** Node and headless Chromium run under gVisor in common
  deployments; ours is gate G6.
- **Migration.** The owner cell is the smallest change and the wrong one:
  it keeps the mapper, the per-class binds and the kernel users share, and
  that is the path that drifted into 33 cells. Firecracker would add a
  guest kernel, a rootfs and a vsock agent for the same deletions.

**The owner cell is deleted, not wrapped** (ADR-019 holds). `bwrap` leaves
with `node_sandbox.py` in P2b.

**Firecracker stays an allowed second driver** behind the same `BoxProvider`
contract, built only when both hold: a measured need gVisor cannot meet (an
exec latency the product feels, or a vulnerability class the Sentry does
not cover) and a host with supported KVM, which is new spending (a
bare-metal box host, about $77 a month) and a founder decision then.
Nested KVM on a droplet is never the production path.

**`BoxProvider` attaches once, at execution admission.** `session.start`
(until C8, the one surviving starter) calls `admit(owner, seat)` once,
binds `BoxProvider.bind(owner)`, wakes the box, and every execution of that
session (tool, node, decoder, git, CLI package, browser) is an exec on that
handle. Child sessions inherit it. No path binds a box of its own.

## P2a gates (production host, pass or fail)

`scripts/box_preflight.py` runs these on the production droplet against two
synthetic owners, with `boxhostd` serving nothing. A skip is a fail. P2b
does not merge until every number is recorded in its PR.

| Gate | Pass |
|---|---|
| G1 host and limits | pinned `runsc` under the `boxhostd` systemd unit, `--network=none`, cgroup v2 per box (`memory.max`, `pids.max` 1024, `cpu.weight`); a 1.5 GiB allocation inside a 1 GiB box dies inside the box; no host OOM event; the daemon canary p95 stays within +20 % of baseline meanwhile |
| G2 cross-user | from inside A's box: B's tree, `/data`, `.broker/`, `.platform/`, `.runtime/` are absent (ENOENT, not EACCES); the environment carries no secret; exactly one socket, A's owner-bound UDS; B's handle on A's channel raises `BoxAuthError`; a stale epoch raises `StaleHandle`; a write past A's quota is ENOSPC in A only. Also green in `scripts/linux_oracle.py` |
| G3 capacity | realistic box (harness rootfs, a Node CLI package process, headless Chromium with one page, 1 GiB owner tree): awake PSS ≤ 700 MiB with Chromium, ≤ 250 MiB without; start to first exec ≤ 1 s; exec p95 ≤ 100 ms; with the daemon at its 4 GiB cap and 4 boxes awake (one with Chromium) the canary p95 stays within +20 % and host `MemAvailable` ≥ 1 GiB |
| G4 volume | project quotas enabled on the data volume; `BoxProvider.usage` matches `du` within 5 % |
| G5 deploy independence | a daemon container restart during a 60 s box exec: the exec completes, its result is journaled, no orphaned sandbox |
| G6 C9 workloads | one CLI package job and one Chromium page render complete under `runsc` on the production kernel |

A failed G3 sets the awake-box count and the daemon cap from the measured
numbers before the window; a larger droplet is a spending decision raised
with those numbers, never assumed.

## Slices (Direction 5: one cutover, the isolated path the only path)

The order is `openspec/changes/one-box-per-owner/tasks.md` §1, one PR each,
deletions in the same PR, measured net ≤ 0. P1 shipped (#4630).

| PR | Ships | Deletes in the same PR |
|---|---|---|
| P2a preflight | `runsc` and `boxhostd` installed by the deploy workflow, serving nothing; `box_preflight.py`; the gate numbers | `box-kvm-validation.yml`, `scripts/box_bench/` (one measurement path) |
| **P2b cutover** (one window, verified backup first) | every owner-space execution in the owner's box: tools, files, git, nodes, decoding, media, CLI packages (token leased for one run, ADR-012 D5), Chromium, one per-box egress relay at the broker under the platform floor, `destroy` for account deletion; admission once; stop on idle; the daemon container unprivileged | all 33 `role_*` modules, the mapper, launcher, provision and migrate, `node_sandbox.py`, `universe_tools.py` jail, `jail_disk.py`, `sandbox/`, `provider_jail.py`, `browser_cell.py`, `browser_egress.py`, `universe_egress.py`, `git_egress.py`, `owner_launcher_client.py`, `owner_socket.py`, `mapper_channel.py`, `cell_diagnostics.py`, the role probes, the old `rules.db` files, the unconfined `security_opt` lines. Guard `role-cells` = 0 |
| P3 loop in the box (after C3, C8) | the loop process, children, residents run inside the box; broker in-box endpoint; survives app close and deploys | the daemon-side loop placement |
| P4 N4 view hook | `BoxProvider.view`, owner door only | |
| P5 N5 owner egress policy at the one relay | `open`, `allowlist`, `ask` | |
| P6 suspend, restore, snapshot (N6) | checkpoint and restore replace stop and start; `snapshot`/`restore` | |
| P7 S10 | `daemon_server.py` tables into `storage/` | duplicate schema helpers |
| P8 S27 | off-region backup from the one snapshot path; DR drill | `backup.sh`, `backup-restore.sh` |
| P9 close | S8, S10, S27, N10 converged | `target-architecture`, `box-provider-foundation` |

**Why the loop is the one thing that moves later.** After P2b no owner
byte is read, written or executed outside its owner's box: the cross-user
floor is complete in one window, with no cell and no mapper left to fall
back to. The loop is trusted platform code in the daemon that reaches the
box only through `BoxProvider` (`test_universe_path_io_guard` at 0). Moving
it into the untrusted box before C3 would put model credentials in reach
of untrusted code, which ADR-019 forbids; moving it after C3 changes where
a process runs, not what it can reach. That is placement, not isolation,
and it adds no second execution path: P3's in-box tools are the same
`boxd` operations P2b ships.

## Needs the founder

- **No spending to start P2a.** `runsc` is Apache-2 on the droplet we pay
  for; the preflight runs in a quiet window and serves nothing.
- **P2b's window:** pick the time; a verified full backup precedes it
  (design §11). Irreversible for the cell layout, not for data.
- **P8:** the off-region bucket, about $0-5 a month (existing
  `docs/host-actions.md` row). Blocks only P8.
- **Later, measured:** a larger droplet, a DO volume for box storage, or a
  bare-metal KVM host (Firecracker's precondition) is each a spending
  decision raised with the G3 or wake-queue numbers that need it.

## Consequences

- ADR-019's driver choice now rests on S0 for the mechanism and on P2a for
  production; its status line cites this ADR. ADR-012's "Firecracker first"
  wording is superseded.
- The `role-cells` guard (`check_shapes.py` S8, ceiling 26) falls to 0 in
  P2b, not over four PRs.
