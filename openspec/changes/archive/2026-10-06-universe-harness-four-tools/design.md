## Context

Full design: `design-notes/universe-harness-design.md` (sections 2-4 and slice
S1). This file records only the decisions S1 had to make that the design left
open, and what was measured to make them.

## Decisions

### D1. The platform runs the tools, through the engine route

The four tools are engine-MCP handles (`engine_mcp_server.py`), added to the
single served inventory (`served_tools.SERVED_ENGINE_MCP_TOOLS`). Every adapter
already reaches that route, so there is no per-vendor wiring and no vendor
tool list. The CLIs' own `Read`/`Bash`/... stay denied on every turn.

### D2. One jail builder, narrower view

`tool_jail_argv` calls `provider_jail.jail_argv` with new keyword arguments
`share_net=False`, `clearenv=True`, `seccomp_fd=`, and a `UniverseView` that
splits the folder into what the agent OWNS and what the platform does
(round-2 correction, below). Provider launches keep the defaults. Path policy
is the jail's: a path outside `/u` is passed through unchanged and simply does
not exist inside.

**The agent owns a small, explicit part of its folder.** `/u` is a tmpfs
holding one `-try` bind per visible root entry, then remounted READ-ONLY.
Read-write binds go only to
`universe_tools.AGENT_BRAIN_FILES` (identity, founder, origin, body, orgchart,
projects, goals, index, log, voice; bound only if present, since an empty brain
file reads as "learned") and `AGENT_HARNESS_DIRS` (skills, prompts, extensions,
workflows, bin, notes; created first). Hidden root entries are never bound, so
they do not exist in the jail. Everything else (`soul.md`, `config.yaml`,
`soul_versions/`, `wiki/`, `workspaces/` ...) is visible and read-only. No new
root entry can be created. The set is pinned by `test_agent_owned_paths_are_pinned`.

Why an allowlist (2026-09-29): the first shape bound the root read-only and
masked each hidden FILE with `/dev/null`. A WAL database's `-shm`/`-wal`
sidecar exists only while a connection is open; one scanned and then deleted
before bubblewrap ran left a mask with no mountpoint, which cannot be created
on the read-only root, and the call was refused ("Can't create file
/u/.effector_consents.db-shm"). A hidden file created after the scan was also
visible through the root bind. `-try` binds skip an entry that is gone at launch.

Why (found while fixing round 2): the credential vault
(`.credential-vault.json`, `.credentials/`) and the per-universe authority
databases (`.effector_consents.db`, `.usage_ledger.db`,
`.external_write_receipts.db`, `.subscription_state.db`) live in the universe
ROOT, not `.runtime/`. The round-1 view masked only `.runtime/`, so the
agent's `read` could return the owner's credentials and its `bash` could
forge consent (an act the platform reserves to the person) or create one of
those databases where none existed. `soul.md` carries the executable
`effect_authority`, so it is read-only too. The same rule closes the
round-2 class at its source: `config.yaml` is no longer agent-writable at
all.

Reads go through the jail too (a jailed `tail | head`), not a daemon-side
open: the jail is then the only thing deciding what is reachable.

### D3. Resource limits without a cgroup

Measured read-only on the production container, 2026-09-24
(`python scripts/droplet.py ssh -- 'docker exec -i tinyassets-daemon sh -s'`):
kernel 6.1.0-52, uid 1001, all capability sets 0, NoNewPrivs 1, cgroup2
mounted **read-only** (`memory.max` 4 GiB, `pids.max` 9482 for the
container), `prlimit` at `/usr/bin/prlimit`, bwrap 0.12. So no per-universe
cgroup can be created from inside the container.

Mechanism: `prlimit` runs INSIDE the jail, after bwrap created the user
namespace. On kernel >= 5.14 `RLIMIT_NPROC` is charged per user namespace:
measured with 9 daemon-uid processes and `--nproc=12`, the jail forked 10
children before `Cannot fork` (a per-uid count would have allowed 2). So the
process limit is per jail, not per daemon user.

The parent adds what an rlimit cannot: wall clock, output cap (killed while
reading), a process-tree watch (count and summed RSS, via
`node_sandbox.read_process_tree`, shared with the workspace watchdog), and a
free-space floor on the data volume.

Root is exempt from `RLIMIT_NPROC`, and a root-run bwrap gets no user
namespace of its own. The first real-jail run (linux-jail-proof 36069992431,
the hosted runner's sudo fallback) lost its VM to the exponential fork-bomb
case: a 0.2 s tree watch cannot catch a doubling. So a ROOT-run jail joins a
fresh cgroup v2 (`pids.max`, `memory.max`) before it becomes bwrap, and is
refused when no such cgroup can be made. Production never takes this path
(uid 1001); CI proves it, and the production path rests on the measurement
above.

Defaults: 512 MiB address space, 64 processes, cpu min(120 s, wall) soft
with hard one second later (so SIGXCPU names the limit), 32 MiB
per file, 256 files, 120 s wall (bash may ask up to 600 s), 64 KiB output,
768 MiB tree RSS, 1 GiB free disk, 4096 free inodes. Concurrency: 2 jails per
universe, 4 per host (flock slots under the data dir). Jail processes run
under `nice +10` and with `oom_score_adj = 1000` (set on the bwrap parent from
the daemon and again in-jail on `/proc/self`), so under CPU or memory pressure
the kernel takes a jail before the daemon.

Fail closed: no bwrap, no prlimit, or output without the marker the jailed
wrapper prints after prlimit succeeded, and the call is refused with nothing
run.

### D4. Every universe file is untrusted to the daemon (round 1 fix)

The jail makes the agent's view safe, but the daemon reads the same folder
from OUTSIDE (persona grounding, config, soul, the skill index). Since the
agent can write and link in its own folder, every such read is untrusted, and
round 1 (BLOCK) found two ways it bites. The fix is structural, in two layers:

- **One safe reader.** `tinyassets/universe_files` (`read_universe_file`,
  `read_universe_text`, `list_universe_dir`, `load_untrusted_yaml`) opens every
  path component with `O_NOFOLLOW` (POSIX: via `workspace_fs`; non-POSIX:
  `lstat` per component), requires a regular file, bounds the read (64 KiB for
  `config.yaml` and any frontmatter, 1 MiB for brain/soul/log markdown,
  256 KiB per skill), and parses YAML only after refusing anchors and aliases
  by scanning parser events (nothing is expanded). EVERY daemon read in the
  turn-path modules goes through it: grounding, soul, self-model, voice,
  `config.yaml` (all three readers), soul-edit policy / governed files /
  frontmatter / log / version index, `soul_versions/` (listed link-free, the
  newest 256 compared), and the skill index. A refused file is a fail-closed
  default plus a logged note, never an exception that breaks the turn.
  `tests/test_universe_file_reads_are_bounded.py` scans those modules' AST and
  fails on any raw `read_text`/`read_bytes`/`open`/`yaml.*load`/`json.load`
  outside four reasoned exemptions (cgroup files, slot locks, `.soul.lock`);
  on the pre-fix tree it reported 15 reads in 3 modules. The ~20 modules that
  read universe files outside the turn path, all of them read-only to the
  agent, are triaged in
  `docs/concerns/2026-09-24-universe-file-readers-outside-the-turn-path.md`.
- **The jail refuses to create the link.** A seccomp filter
  (`universe_tools.seccomp_program`, x86_64 and aarch64; x32 and unknown
  architectures get EPERM for everything) refuses `symlink`, `symlinkat`,
  `mknod`, `mknodat`, **and `io_uring_setup`/`enter`/`register`**.
  `IORING_OP_SYMLINKAT` (opcode 38, kernel 5.15+) creates a link through a
  submission queue that seccomp never inspects, and production is kernel 6.1
  with no `io_uring_disabled` sysctl (that arrived in 6.6); refusing the ring
  setup means no ring op can run. Hard links cannot leave `/u` (EXDEV). Cost:
  a `git clone` or install that creates symlinks fails for those entries, and
  a library that uses io_uring for I/O falls back or fails.

### D5. Vendor-native harness dirs (design risk 8)

Decision: masked at every provider launch, by a rule that names no vendor.
`provider_jail.default_view` puts an empty tmpfs over every hidden directory
at the universe root except `.runtime` (`hidden_root_masks`), and refuses the
launch if a hidden root entry is a symlink. The agent may write `.claude/`,
`.git/` or anything else in its own folder; the next `claude -p` launch
(cwd = universe, `--setting-sources project`) still sees an empty directory,
so a hook the agent wrote never runs next to the owner's subscription
credential. A first draft masked `.claude`/`.codex` by name in both jails;
`check_channel_agnostic.py` refused the vendor names in the shared jail, and
the rule without names is also the one that covers the next CLI.

### D6. The skill index

`harness_prompt()` is appended to the system prompt only when the turn has the
tools (`turn_config.engine_mcp_enabled`: a verified founder principal, founder
tier, flag on). It lists `skills/<name>/SKILL.md` with frontmatter
`description`, name = directory name, at most 64, descriptions one line and
<= 300 chars. The daemon reads them with `workspace_fs` descriptor walks and
`O_NOFOLLOW`, so a link is skipped, never read. POSIX only.

The frontmatter is a file the agent WROTE, so it is never handed to a YAML
loader (round 1 BLOCK: a 234-byte alias bomb expands to gigabytes under
`yaml.safe_load` and crash-loops the shared daemon, since `harness_prompt`
runs on every founder turn). `_skill_description` scans a bounded slice for a
single flat `description:` line, caps the length before building any string,
honours no anchors/aliases/tags/block scalars, and both it and `harness_prompt`
swallow any parse error, so one bad skill is left out and never breaks a turn.

## Residuals (tracked, not blocking S1)

- The persona prompt assembly still reads grounding files whole. With the
  32 MiB file cap and the link filter this is owner-scoped cost, not a
  cross-user read; S2 replaces the assembly.
- Total bytes per universe are not charged to a tier quota yet; the disk
  floor protects other users from exhaustion, not the owner from their own
  folder growing.
- `CLAUDE.md` in the universe root is still read by `claude -p`
  (`--setting-sources project`); it is prompt text, not code, but it is a
  vendor-specific harness path. Resolve in S2 when `AGENTS.md` becomes the
  persona.
- Per-universe cgroups (memory.max, pids.max, cpu.weight) need a delegated
  cgroup subtree in the container: a host change, not in this slice.
