# Evidence: measurements behind `target-architecture`

**Scope.** Raw numbers, versions and commands for the design's E-references. These are **baselines for hypotheses**, not production guarantees. S0 re-measures on DigitalOcean, and S0 task 1 commits the scripts under `scripts/box_bench/`.

**Where the measurements ran.**
- E2–E5 ran 2026-10-01/02 in throwaway containers on the founder's Windows 11 box: Docker Desktop on WSL2, kernel `6.6.87.2-microsoft-standard-WSL2`, nested KVM, 20 threads, 32 GB.
- The containers were `debian:trixie` (privileged, `/dev/kvm` passed through) running as uid 1001, the production daemon's uid.
- Production was read without changes (E1).

## E1. Production, read-only (2026-10-02 00:30–00:45 UTC)

| Fact | Value | How |
|---|---|---|
| Host | `s-4vcpu-8gb`, sfo3, Debian 12, kernel 6.1; 7,940 MiB RAM, 1,086 MiB used; CPU 97–99% idle | `nproc; free -m; uptime` over ssh |
| KVM | `/dev/kvm` `crw-rw---- root kvm`; `vmx` flag; `kvm_intel nested=Y` | `ls -l /dev/kvm`; `grep -o vmx /proc/cpuinfo`; `/sys/module/kvm_intel/parameters/nested` |
| Restarts | 81 in 4 days; p50 8 s, max 191 s; 14.8 min origin-down | journald "Shutting down" → "Application startup complete" pairs |
| Backups | Spaces sfo3 (same region as the droplet); the GitHub full tier fails (4,035,405,694 bytes > 2 GiB) | `journalctl -u tinyassets-backup.service` |
| DR drill | last run 2026-07-24, `workflow_dispatch` only | `gh run list --workflow dr-drill.yml` |
| SQLite | 3.46.1 (Python 3.11.15) | `python -c "import sqlite3; print(sqlite3.sqlite_version)"` in the daemon |
| Secrets in the daemon environment (names only) | `DO_API_TOKEN`, `STRIPE_SECRET_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `WORKOS_API_KEY` | `/proc/<pid>/environ` names for pids 1, 7, 12 |
| Bill | Sep 2026 $57.72 (droplet $48, Spaces $5, $4.72 residual) | DO invoices API (GET) |

## E2. Syscall surface: #4245 (`30b0249b`) against OpenShell

**Method.** `sysprobe.py` issues 34 syscalls with harmless arguments. Results are classified as:
- **F**: refused by the filter (EPERM or ENOSYS);
- **K**: refused by the kernel on capability, meaning the call reached the kernel;
- **R**: reached and ran.

**Configurations:**
- the real `universe_tools.bash` (tool jail);
- bwrap with `jail_seccomp.deny_program(nested_sandbox=True)` (provider jail filter);
- the same bwrap with no filter (baseline);
- OpenShell's filter, from source: `crates/openshell-sandbox/src/sandbox/linux/seccomp.rs:150-283`, v0.1.2.

**What #4245 filters:**
- in both jails: mknod, io_uring_*, bpf, perf_event_open, keyctl, add_key, request_key, setns, ptrace, userfaultfd;
- in the tool jail only: symlink, unshare/clone with CLONE_NEWUSER, and clone3 (→ ENOSYS).

**Still R in both #4245 jails, but filtered by OpenShell:** mount, umount2, open_tree, fsconfig, memfd_create, process_vm_readv/writev, pidfd_open/getfd/send_signal, execveat(AT_EMPTY_PATH), seccomp(SET_MODE_FILTER), socket(AF_NETLINK, non-route).

**Kernel-refused here but not filtered:** fsopen, fsmount, fspick, move_mount, pivot_root, socket(AF_PACKET).

**Provider jail after an in-jail `unshare -r --mount`:** mount, umount2, open_tree and fsconfig are R, with namespace capabilities.

**OpenShell does not filter** keyctl, add_key or request_key.

## E3. Firecracker v1.17.0 snapshot / restore / balloon

**Setup.**
- Guest kernel: CI `vmlinux-6.1.155` (`CONFIG_VIRTIO_BALLOON=y`, `CONFIG_PAGE_REPORTING=y`).
- Guest: 1 vCPU, 512 MiB, running as root in the container.
- Rootfs: the agent image (node 22, Claude Code 2.1.287, codex-cli 0.160.0) as a 2.5 GB ext4 image.
- Warm workspace: 2,000 random-base64 files (~54 MB), plus a node process holding 200 MiB of `crypto.randomBytes` heap.
- Driver: `fc_snap.py`, which talks to the Firecracker API socket. Snapshots are `Full` and `Diff`, with `track_dirty_pages`. Restore uses `snapshot/load` with a `File` memory backend and `resume_vm`.

| Metric | Value |
|---|---|
| Cold boot → warm workspace ready | 6.29–6.70 s |
| Running warm VMM RSS | 416–419 MiB (guest: 94 MiB free, 246 MiB available) |
| Pause | 0–1 ms |
| Full snapshot | 0.56–0.69 s; memory file 512 MiB (not sparse); vmstate 13 KiB; `zstd -1` 295 MiB |
| Diff snapshot after 10 s more work | 0.02 s; 2–8 MiB on disk (sparse) |
| Restore (load + resume) | 31–51 ms warm cache; 44–63 ms cold cache |
| Guest observed running (20 ms console tick) | 52–71 ms warm; 132–166 ms cold |
| RSS after restore | 15–22 MiB; 29–49 MiB after 5 s (lazy fault-in) |
| Balloon on the running warm box | inflate 128 MiB: 417 → 383 MiB in 0.4 s; 192 MiB: → 319 MiB in 0.2 s |

**A discarded run.** One earlier run filled the heap with a constant byte, and the snapshot compressed to 37 MiB. That run is discarded as unrepresentative.

## E4. gVisor release-20260928.0 (systrap; sha256 verified)

| Metric | Value |
|---|---|
| Rootless box start (`runsc run --detach`) | 42–59 ms (first 159 ms) |
| Tool call (`runsc exec`), 25 × {write, read, edit, bash} | median 16 ms, p90 16–22 ms |
| Idle box | 16.4 MiB host PSS (Sentry 10.9 + gofer 5.6); ~0 CPU (14 boxes: 0.01 CPU-s / 30 s) |
| Claude Code inside | box memory usage 338 MiB (`runsc events --stats`), against 227 MiB `VmHWM` natively |
| Without `/dev/kvm` | runs, including in a production-shaped unprivileged container (`--cap-drop ALL`, `no-new-privileges`, uid 1001, no kvm device) |
| Our egress floor through the box | `--network=none --host-uds=open` with `universe_egress.ensure_proxy()`: Anthropic 401 through the proxy, metadata 403, private 403, no direct path |
| Warm workspace build | 19–32 s (the same workload takes 6.5 s on Firecracker): small-file writes through the gofer are slow |
| Checkpoint | 0.32–0.51 s; 265 MiB (`pages.img` 263 MiB); `zstd -1` 241 MiB |
| Restore | rootless: **unsupported** ("Rootless mode not supported with \"restore\""). Rootful: 0.46 s, first exec at 0.50 s, eager (+420 MiB host PSS) |
| Reclaim after freeing ~255 MB in the box | box usage 370 → 2 MiB; host PSS +476 → +75 MiB within 5 s |

## E5. OpenShell v0.1.2 (Podman 5.4.2 rootless, and libkrun microVM)

| Metric | Podman | microVM |
|---|---|---|
| Warm create → Ready | ~1.2 s | ~5.9 s (31.5 s cold, 1 GB image) |
| `sandbox exec` (CLI) | 63 ms median | 63 ms median |
| Idle | 41–46 MiB PSS; 0.5% CPU | ~80 MiB PSS; 3.4% of a core |
| Smallest working VM | — | `mem_mib=64` boots |
| Free-page reporting | — | 126 of 136 MiB returned in 15 s |
| Memory snapshot | none | none |

Other findings:
- Rootless Podman runs all tenants under one host uid unless `userns=auto`.
- Without cgroup delegation, `--memory 128Mi` was recorded but not enforced: a 300 MB allocation succeeded.
- Placeholder credentials were proven for header auth: the echo server saw the real value.
- codex-cli 0.160 ignores the shipped profile's `CODEX_AUTH_*` environment variables.

## E6. Prices and research (read 2026-10-01/02; sources in the staged-architecture and box-cost research)

**Compute and storage prices:**
- DO Basic: $12 / $24 / $48 / $96 for 2 / 4 / 8 / 16 GB.
- OVH RISE-S Hillsboro: $77/mo for 64 GB with native KVM (setup waived on a 12-month term).
- R2: $0.015/GB-mo, no egress. B2: $6.95/TB-mo.
- DO managed Postgres: $15.15, or $30.30 with HA.
- Cloudflare Load Balancer: ~$5/mo (secondary source).

**Platform behaviour:**
- Litestream v0.5 supports point-in-time restore. LiteFS is unsupported.
- Cloudflare tunnel replicas route to the nearest replica.
- Firecracker supports `PATCH /drives` rescan of a resized backing file (v1.17 `docs/api_requests/patch-block.md`).
- Firecracker diff snapshots need a merged base: `rebase-snap` ships in the release.

## E7. Illustrative capacity, today's droplet (4 vCPU / 7.9 GB, ~6.8 GB available)

**Active CLI seats by box type:** bwrap ~29 at 235 MiB each, gVisor ~19 at 360 MiB, Firecracker ~16 at ~420 MiB.

**Thin loop.** A tools-only box is estimated at 80–130 MiB; that estimate is unmeasured and S7 measures it. On that estimate, the thin loop multiplies awake capacity roughly 3×.

**Idle cost.** A suspended user costs disk only: ~0.25–0.3 GB.
