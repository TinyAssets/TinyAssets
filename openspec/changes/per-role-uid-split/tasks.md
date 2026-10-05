**founder decision 2026-10-05: fold + build with probes.** D9 accepts F1-F7 and
retains the confirmed controls. No fourth design review; normal cross-family
code review for the eventual build. D10 records the lead's least-privilege
amendment: capability-free two-pass deletion and opt-in startup reverse migration.
The capability-lifetime conflict is resolved; no build task is proven yet.
D11 records the lead decision assigning egress persistence to the broker and
requiring authenticated daemon IPC. D12 resolves the ledger-parent issue by relocation to /data/.broker; D13
keeps private directories inaccessible to the daemon IPC group.
See delivery.md and broker-access-inventory.md: runtime routing is partially
implemented through D43, including injected cloud/effect consumers, read-only
kernel liveness and accounting source binding. Accounting runtime/daily evidence
IPC, refresh, real engine classes, full migration/deletion, old-image rollback
and startup remain.

## 1. Design (this change)

- [x] 1.1 Proposal, design and spec delta; uid map agreed with agent-loop and openshell-spike.
- [x] 1.2 Cross-family security refute of the design, two rounds, both ADAPT, both folded in.
      Round 1 raised four P1s, now answered by D2 (the privileged chain), D6 (owner-reachable
      IPC), D4 (the migration) and D7 (the owner trust class). Round 2 refuted each of those
      answers on a specific mechanism and was right every time: a venv's symlinked interpreter
      versus a refuse-on-symlink check; a socket directory without setgid; `CAP_KILL` and
      `CAP_FOWNER`/`CAP_DAC_OVERRIDE` missing from the capability set; setgid inheritance putting
      the vault in the wrong group; four runtime sites that re-mode the migrated paths; four spawn
      sites missing from the uid-1001 enumeration, one of them an unjailed provider launch;
      `PR_SET_DUMPABLE` reset by `execve`; and a reversed reading of Yama's ptrace rule. A third
      round was the last available under the then-current three-round cap. These two
      rounds and their fixes remain intact; D8 now supersedes the deferred isolation scope.

## 2. Build (Codex implements; deploy-incident reviews and verifies)

Lands after #4299 (the broker) and #4267 (`platform_secrets`), amending both.

- [ ] 2.1 **Image: users, groups, and a root-owned privileged chain** (closes P1-1, part 1)
      - users 1002 `ta-broker`, 1003 `ta-engine`; groups 1100 `ta-work`, 1101 `ta-brk`,
        1102 `ta-vault`, with **no** supplementary membership in `/etc/group` — `ta_op.c:240-243`
        refuses a second gid and the healthcheck runs through it, so the launcher sets groups
        instead (D1).
      - `ta-entry.sh`, `ta-launch.py` install root-owned `0555` under `/usr/local/libexec`;
        `broker_main.py` ships root-owned `0555`.
      - `Dockerfile:339` chowns `/data` only; `/app` stays root-owned and read-only; `HOME` moves
        to `/home/tinyassets` (1001:1001 `0700`).
      - `Dockerfile:178` gains `--copies` so `/opt/venv/bin/python` is a plain root-owned file
        rather than the venv default symlink.
      - `CMD` becomes the launcher, so `ta-entry.sh`'s `exec "$@"` (line 130) is unchanged and the
        daemon's argv lives in the launcher's static table instead of the image CMD
        (today `Dockerfile:354`).
      - *Precondition:* enumerate every runtime write under `/app` first (daemon, healthcheck,
        provider jail, node sandbox). If one is load-bearing, take D2's fallback — a root-owned
        `/opt/tinyassets` copy plus a byte-parity gate — and record which was used.
- [ ] 2.2 **`ta-launch.py`: the launcher** (closes P1-1, part 2)
      - stdlib-only, run `-I -S -B`; static kind/argv table; no shell string in either direction.
      - `SO_PEERCRED` **uid-and-pid** check against the daemon it started, so no other process at
        1001 can request a spawn.
      - per-kind **allowlist** environment with `CHILD_FORBIDDEN_ENV` applied on top —
        `platform_secrets.child_env` is a denylist (`platform_secrets.py:53-55`) and must not be
        the basis for a privileged exec.
      - per-kind `setgroups`/`setresgid`/`setresuid` with `/proc/self/status` readbacks; full
        capability drop with readbacks; `PR_SET_NO_NEW_PRIVS`; `/proc/self/fd` sweep keeping only
        each kind's declared descriptors (`provider-cli` needs bubblewrap's seccomp fds,
        `owned_process.py:651`); listening socket `FD_CLOEXEC` and never in any kind's passed set.
      - the launcher drops `CHOWN`, `FOWNER` and `DAC_OVERRIDE` from all five of its own sets,
        with readback, **before** it binds — the migration is over and the serving process must
        not keep that authority (D2 phase table).
      - D10 deletion/reset uses two capability-free passes: engine 1003 in the
        admitted owner's cell through normal launcher spawn removes engine-owned
        entries with no-follow openat traversal; daemon 1001 removes daemon-owned
        entries and empty structure. Route pool removal, scoped_reset and account
        deletion through this path. Any removal failure reports the path loudly.
        No retained migration capabilities or separate privileged helper.
      - **not** `PR_SET_DUMPABLE(0)` here: `execve` resets it, so the daemon and broker each set
        it on themselves after exec, before any secret exists.
      - audit the current `engine-mcp` environment consumers (including OAuth service and
        execution-owner configuration); preserve required scoped configuration without
        passing owner channel tokens or platform credentials (D3/D8).
      - validate owner scope, select and pin a static per-kind jail view, and complete D8
        namespace/fd/IPC confinement before payload exec; no unjailed fallback.
      - D9's named per-class seccomp profile, mandatory post-mount fd closure,
        child umask 007 and exact per-cell safe.directory entries.
      - unit tests in the Linux oracle.
- [ ] 2.3 **Chain verification** (closes P1-1, part 3)
      - the launcher checks itself, the interpreter, `broker_main.py`, every privileged `sys.path`
        entry, **every ancestor directory of each**, and **every node of each symlink
        resolution**, refusing on a non-root owner or a group/other-writable mode — before it
        binds a socket. Not refuse-on-symlink: that would reject `/opt/venv/bin/python`, which
        CPython's POSIX venv symlinks by default.
      - `scripts/check_privileged_chain.py` asserts the same against the built image, wired into
        the docker-build CI job, so the runtime refusal is a backstop not the only check.
- [ ] 2.4 **Volume migration and vault permissions** (closes P1-3)
      - D11 broker egress ownership (1002:1101), including SQLite sidecars and
        .outbound-proxy, forward and reverse in the startup window. D12 relocates both beneath /data/.broker with checkpoint/fsync/rename
        and reverse relocation; never widen /data writes.
      - D4's exact inventory, under the exclusive layout lock, idempotent, with
        `"roles": {"state": "migrating"}` in `/data/.layout.json` for crash recovery.
      - before traversal/chown, root idempotently unlinks legacy owner.json and only known
        stale relay sockets via pinned dirfds without following links (D4 cleanup). Add D4
        sidecar directory/socket modes to the shared declaration and both egress/engine
        relay runtime creation paths; bind only exact sockets, never sidecar parents.
      - traversal holds directory fds with `O_NOFOLLOW|O_DIRECTORY`, uses `*at()`/`lchown` only;
        skip workspace symlinks, re-mode contents including .venv/node_modules, preserve
        executable bits. Refuse links in privileged/vault/broker sets; D9/F4 governs
        work-tree hardlink aliases. No user-data deletion; dry-run changes no metadata/marker.
      - remove other permissions from shared stores, including runtime replacements/sidecars.
      - add access u:1001:rwx and default d:u:1001:rwx ACLs on ta-work directories,
        appropriate file access ACLs; require ACL support. D10 resolves D9/F5's
        access mechanism without changing capability retirement.
      - implement explicit opt-in startup reverse migration before capability drop,
        in the forward migration code path, with dry-run,
        crash recovery and idempotence; restore engine-owned restrictive content
        to the 1001-readable layout before old-image startup. Complete and test
        `rollback.md` with the actual CLI. Reverse migration never deletes data.
      - inventory explicit 0700/chmod sites inside ta-work trees (including venv
        creation) and use shared group-preserving 0770/2770 modes there only.
      - prove rollback against engine-created files, not merely the initial inventory;
        prove daemon deletion and old-image uid-1001 read/write/delete without work groups.
      - runs with `CHOWN` + `FOWNER` + `DAC_OVERRIDE`: it keeps owner 1001 on almost everything,
        so euid 0 is not the owner of what it re-modes, and `CHOWN` alone is `EPERM` (D4
        §Authority).
      - **one declaration for these modes**, read by the migration and by every runtime site that
        re-modes the same paths — otherwise the next provider launch silently restores single-uid
        permissions: `credential_vault.py:187-188` (`0o700`), `:1783`, `:1784-1793`,
        `:1808,1846,2069` (`0o700` dirs, `0o400` files), plus the two write-path
        `_chmod_best_effort(..., 0o600)` calls around 624 and 632 → `0o640`.
      - the vault's group is set on the **temp file before** `tmp.replace(path)`
        (`fchown(fd, -1, GID_VAULT)`), pre-commit so a failure propagates. Setgid inheritance
        cannot do it: the temp file is a sibling in the command-center root, whose group is
        `ta-work` — an inherited group would make every replacement vault readable by 1003.
      - confirm read-only on prod that `/data` is `ext4` with ACL support so `g:1102:x` gives the
        broker traverse on `/data/<cc>/` without widening `other`; else mode `2711`, recorded.
- [ ] 2.5 **Every owner-scoped spawn through the launcher and owner cell** (closes P1-4, part 1)
      - reconcile D8's full inventory with a fresh repository-wide spawn search, including
        indirect helpers; add every owner-work class/site to the oracle matrix. Route preview,
        decoder, auth probe, local box and owner-scoped utility calls through the same owner
        boundary. Scope engine-MCP servers, worker reuse and IPC to one owner, with private
        PID/IPC/network namespaces, procfs/scratch and exact relay sockets; preserve narrower existing jail views.
      - provider-cli, engine-mcp thin proxy and node-sandbox move to the launcher client
        at 1003. Canonical engine-mcp handlers and shared stores stay in the daemon;
        pin proxy scope at the receiver from admission, never child owner fields.
        Reclassify node_bid as control-plane; shared stores never enter cells.
      - inventory each daemon reader/server of each cell-writable path. D9/F2's planted
        symlink/FIFO/hardlink probes cover inspect, preview, file reads, git_bridge,
        staging/publish and every additional reader; a denied plant alone is insufficient.
      - and the four sites the first enumeration missed: `provider-discovery`
        (`providers/base.py:1418-1420` → `native_jsonrpc_discovery.py:130-134`, historically bypassing
        the jail; current code uses `aspawn_owned` and the narrow metadata view),
        `tool-jail` (`universe_tools.py:777`), `workspace_provision_process.py:127`, and
        `workspace_registry_process.py:165`.
      - `workspace-worker` to 1003: its `multiprocessing.Pipe` channel
        (`workspace_worker.py:677-684`) becomes a pre-connected `socketpair` passed by
        `SCM_RIGHTS` and `run_workspace_worker` reads it from that descriptor. Differential-test
        the new channel against the current one through the existing injectable `spawn=`
        (`workspace_worker.py:667`).
- [ ] 2.6 **`start_broker`, the in-memory fence, and the legacy path** (closes P1-2 and P1-4, part 2)
      - launcher-mediated start when the uids are distinct; refuse otherwise.
      - route every broker-access-inventory.md entry: daemon ledger/accounting/
        refresh operations through authenticated broker IPC, engine egress through
        its cell proxy only. No direct daemon opens, raw-SQL RPC or silent fallback.
        Preserve accounting liveness, refresh admission-before-spend, account
        deletion and backup coverage; fail loudly on an unsupported route.
      - delete `owner.json`, `OWNER_FILE`, `read_owner` and `stop()`'s terminate-and-unlink
        (`supervisor.py:125-132,150-159`); the socket and the `(generation, token)` pair live in
        memory, and `_broker_channel` (`outbound_connections.py:1086-1108`) takes them from the
        live supervisor instead of re-reading a file at 1097 and 1103. Update
        `tests/test_broker_process.py:57-59,98-129`.
      - amend #4299: the broker mints the generation from its persisted fence, so `lease_verifier`
        (`broker/process.py:40-48`) loses its `owner_generation` parameter and `--generation`
        leaves the broker's argv.
      - the legacy per-grant proxy worker refuses to spawn while `broker_selected()`
        (`outbound_connections.py:5338`), so the two credential paths never coexist at uid 1001.
        Test both directions.
- [ ] 2.7 **Entrypoint, compose, and the capability set**
      - start as root with `cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, SETPCAP, KILL]`,
        and change `ta_op.c`'s `MASK` (81-82) to match **in the same commit** — that
        file asserts set *equality* on root entry (206-208) and the healthcheck runs through it,
        so a mismatch is a red healthcheck and a deploy rollback. `tests/test_ta_op_modes.py`
        holds the parity.
      - `deploy/docker-entrypoint.sh` keeps its existing logic (the `_platform_credential_env`
        loop 66-92, the data-file check 117-128, `exec "$@"` 130) and its install path changes;
        it gains exactly two steps — the D4 migration and the `/run/tinyassets` directories.
      - `HOME` updated in `deploy/compose.yml` (today `HOME: /app`, compose:147); the deploy
        validator's capability assertions updated.
      - remove `CAP_SYS_ADMIN` from both `cap_add` and `MASK` in one commit; no exception.
        Never diverge from `ta-op` silently.
- [ ] 2.8 **Production-image Linux oracle proofs** (non-root payloads, like production)
      - D11 actual broker opens/creates/transacts in ledger and proxy state;
        daemon and every actual engine class denied direct access; daemon
        accounting over IPC succeeds; startup rollback restores old-image
        ledger ownership/location and working journals. Diagnostics are not
        substitutes for these launcher/IPC/migration acceptance probes.
      - add production-image support to the oracle proof harness (the existing runner
        builds `docker/linux-oracle.Dockerfile`, which is not acceptance), then run
        `python scripts/linux_oracle.py` with that explicit mode and record image digest
        and launch configuration: D8's complete
        per-class/per-site actual-process matrix denies B's data, owner.json, vault and
        owner token to A, including procfs/ptrace, fd/env/IPC, alias/race and scope-reuse
        probes, B-engine-port/relay/host-abstract-socket denial, plus legitimate A operations
        and A relay access with D4 sidecar modes. Record identities/namespaces and deny results;
        no generic jailed substitute or skip counts as a pass. Prove obsolete owner.json
        removal separately from the seeded legacy-file denial fixture.
      - every D9 F1-F7 and confirmed C1-C6 row becomes a pass/fail probe using the
        production image and compose security options. Per class list /proc/self/fd,
        try openat(fd, ".."), and run actual positive operations including git and venv.
        Cover every writable-path/daemon-reader pair, including preplanted fixtures.
      - migration on a disposable copy: dry-run unchanged, repeat no-op, interrupted
        resume, symlink targets unchanged; old-image rollback and actual daemon deletion
        after engine 0700/0600 creations and chmod. No skip counts as a pass.
      - actual daemon delete/reset of engine-created 0700 trees through D10 two
        capability-free passes; failures report their path; reject A requests
        targeting B and symlink escapes, proving
        outside contents/metadata unchanged. Startup reverse migration dry-run/apply/repeat
        then actual old-image uid-1001 read/write/delete without work groups.
      - a 1003 child gets `EACCES` on `/data/.broker/state/fence.json` and on
        `/data/<cc>/.credential-vault.json`.
      - the broker reads the vault and **cannot write** it; the provider jail works under 1003
        with group workspace access.
      - enumerate which `provider_jail` binds the 1003 child must **write** (the
        `.runtime/provider-launch-credentials` snapshot in particular) and set `2770/0660` for
        exactly those, `2750/0440` for the rest — measured, not guessed.
      - the owner connects to the broker socket: prove the **setgid** socket directory yields a
        `1002:1101` socket. The broker sets only a umask and changes no group
        (`process.py:101-106`), so a plain `0750` directory yields `1002:1002` and locks the owner
        out exactly as the original `0600` did.
      - the launcher can signal both children at shutdown (`CAP_KILL`), and refuses to serve
        while it still holds `FOWNER` or `DAC_OVERRIDE`.
      - `ta-op pulse` stays green on the new root entry, and `PR_SET_DUMPABLE(0)` set by the
        daemon **on itself after exec** breaks no 1001 reader of its `/proc`. The healthcheck is
        the deploy's own gate, so both are proven before the deploy.
- [ ] 2.9 Prod verification: per-role uids in `ps`, the broker serves one owner stream, the
      measured RSS per stream; `deployed_sha` and canary.
- [ ] 2.10 Spec sync and archive.
