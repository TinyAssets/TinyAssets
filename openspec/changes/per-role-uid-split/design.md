## Founder amendment status

**founder decision 2026-10-05: fold + build with probes.** D9 folds all seven
round-3 required changes and supersedes conflicting historical decisions below.
No fourth design review; the eventual build gets a normal cross-family code
review. Nothing deploys under this instruction.

**Lead technical decision: least-privilege two-pass deletion and startup rollback.**
D10 replaces runtime root maintenance. The launcher still retires all migration
capabilities before service, with no privileged helper. Deletion uses engine
1003 inside the owner's cell, then daemon 1001; rollback uses the startup
migration window. The capability ambiguity in delivery.md is resolved. Build
and production-image acceptance remain pending.

## Context

`broker-streaming-contract` design §Roles: "the owner process and boxhostd run as DISTINCT uids …
a connection from an unmapped uid is refused." The broker maps uid to role from static
configuration via `SO_PEERCRED` (`tinyassets/broker/server.py:87-94,163`, #4299). A uid cannot be
faked by a process that lacks `CAP_SETUID`, but every process in the daemon container is uid 1001
today, so the map cannot tell the owner from its children. `start_broker` refuses on purpose
(v1 deviation (c), `supervisor.py:166-183`).

Production facts (2026-10-02, read-only `docker inspect` / `docker exec id` / `stat`):
- the daemon is `User: tinyassets` (1001), with `CapDrop: [ALL]`, `no-new-privileges=true`,
  seccomp and apparmor unconfined (bubblewrap needs that);
- the `/data` volume and every store in it are owned by `workflow` (uid 1001 on the host),
  0644/0755.

Image facts (`Dockerfile` on `main`, re-read 2026-10-02 at the merge base of this branch):
- `WORKDIR /app` (287), `PYTHONPATH=/app` (331), `chown -R tinyassets:tinyassets /data /app` (339),
  `USER tinyassets` (341), `ENTRYPOINT [… /app/docker-entrypoint.sh]` (348);
- `/opt/venv` is `COPY`d without `--chown` (290) and never chowned, so the interpreter tree is
  root-owned;
- but the install is **editable** — `pip install -e ".[mcp]"` (180) under hatchling — so
  `site-packages` holds only a pointer to the builder's `/build`, which the final image does not
  have. `tinyassets` is therefore importable from exactly one place: owner-writable `/app`.

There is already a reviewed precedent for a privileged exec surface here. `deploy/native/ta_op.c`
is a fully static, root-owned `0555` binary at `/usr/local/libexec/ta-op`, compiled in the builder
(171-174), installed outside `/app` and `/data` (282-285), with a closed argv table, a
`/proc/self/fd` sweep (`ta_op.c:181-195`) and a complete identity retirement with readbacks
(`ta_op.c:199-231`). Its own header states the rule this design has to satisfy: *"a writable
directory must never hold a binary that root may one day exec."* It already anticipates this
change: its root branch is the "managed-bootstrap entry" this change finally uses, and it requires
**set equality** with `MASK = 0x2001c1` = CHOWN, SETGID, SETUID, SETPCAP, SYS_ADMIN today
(`ta_op.c:81-82,206-208`). Set equality, not a subset test, is why D2's capability set and that
constant have to move together.

## Goals / Non-goals

- **Goal:** distinct kernel uids for the owner (daemon), the broker and engine/provider children.
  The owner cannot become the broker, a child cannot become either, and the broker serves.
- **Goal:** no new long-lived privileged surface beyond one small, auditable launcher, and **no
  owner-writable path anywhere on a privileged import or exec chain.**
- **Goal:** every engine process executing owner-scoped work is inside that owner's
  isolation boundary before application code runs, including all helpers and descendants.
- **Non-goal:** allocating per-command-center uids; D8 establishes the boundary now with
  per-owner namespaces, independently of the reserved S4/S5 box uid range.
- **Non-goal:** splitting containers. One container, several uids, keeps the shared volume and the
  existing deploy transaction.
- **Non-goal:** moving credential *deposits* to the broker. The owner stays the vault's only
  writer; the broker becomes a read-only consumer (D4). Deposit-through-broker is S6's own lane.

## Dependencies

This change's build tasks land **after** both of these, and amend them:
- **#4299** (`feat/broker-frames`) — the broker itself. D5/D6 rewrite
  `tinyassets/broker/supervisor.py` and the `lease_verifier` signature in
  `tinyassets/broker/process.py:40-48`.
- **#4267** (`security/platform-secrets-scope`) — `tinyassets/platform_secrets.py`, which #4299
  already imports (`supervisor.py:92`) and which does not exist on `main`.

## Decisions

### D1. The uid and gid map (agreed with agent-loop and openshell-spike before build)

| Role | uid:gid | Supplementary | Owns |
|---|---|---|---|
| owner (daemon, frontends, scheduler) | 1001:1001 `tinyassets` | 1100, 1101, 1102 | `/data` (unchanged) |
| broker | 1002:1002 `ta-broker` | 1102 | `/data/.broker/` (0700), plus D11 broker egress ledger/proxy set (gid 1101) |
| engine / provider children | 1003:1003 `ta-engine` | 1100 | nothing; `ta-work` access only inside D8 owner namespace |
| boxhostd (S4/S5) | 1004 reserved | — | — |
| per-box uids | 200000–299999 reserved | — | openshell-spike defines |

| gid | Name | Members | For |
|---|---|---|---|
| 1100 | `ta-work` | 1001, 1003 | workspace roots, `2770`, setgid; engine access confined by D8 |
| 1101 | `ta-brk` | 1001 | connecting to the broker's socket |
| 1102 | `ta-vault` | 1001, 1002 | reading the vault, `0640` |

The owner is in `ta-vault` not to gain access — it owns those files already — but because
`chown(-1, gid)` is only permitted to a gid the caller holds, and the owner has to set that group
explicitly on each vault write (D4.1).

**`/etc/group` lists no supplementary membership for any of them.** The launcher sets each role's
groups with `setgroups()` at spawn, which needs no `/etc/group` entry. This is load-bearing:
`ta-op`'s legacy-rootless branch refuses any supplementary gid other than 1001
(`ta_op.c:240-243`), and the container healthcheck runs through `ta-op`. Writing 1001 into
`ta-work`/`ta-brk` in `/etc/group` would make every `docker exec` carry those gids and turn the
healthcheck red — an unhealthy daemon, which is a deploy rollback.

### D2. The privileged chain is root-owned end to end

The container starts as root under tini with `cap_drop: ALL` and `no-new-privileges` still on — it
allows `setresuid()` with the capability and only forbids gaining privilege through setuid
binaries.

**The capability set is phase-scoped, and it is larger than the first draft said.** Two phases need
different authority, and each capability here is one some step provably cannot do without:

| Capability | Phase | Why it is unavoidable |
|---|---|---|
| `CHOWN` | migration | changing the owner of `/data/.broker/**` to 1002 |
| `FOWNER` | migration | `chmod` and `setfacl` on paths owned by **1001**. euid 0 is not the owner, and without this they are `EPERM` |
| `DAC_OVERRIDE` | migration | traversing and reading existing owner-only (`0700`) directories as euid 0 |
| `SETUID`, `SETGID` | launcher | `setresuid`/`setresgid`/`setgroups` per role |
| `SETPCAP` | launcher | `PR_CAPBSET_DROP` — the retirement itself |
| `KILL` | launcher | signalling the daemon (1001) and broker (1002) at shutdown. euid 0 does not match either uid, so signal permission is `EPERM` without it |
| `SYS_ADMIN` | forbidden | remove from compose and ta-op MASK together (D9/F6) |

The **launcher drops `CHOWN`, `FOWNER` and `DAC_OVERRIDE` from all five of its own sets and reads
that back before it binds its socket.** The long-lived privileged process therefore never holds the
authority to read or re-mode arbitrary owner-owned files; that authority exists only during the
migration, while the container holds exactly one process.

`ta_op.c`'s `MASK` (`ta_op.c:81-82`) must equal the container's set, because the container
healthcheck enters `ta-op` at uid 0 and that file asserts *set equality* (`ta_op.c:206-208`).
So `MASK` changes in the same commit as `cap_add`, held by the existing
`tests/test_ta_op_modes.py` parity. **Never diverge from it silently** — a mismatch turns the
healthcheck red, which is an unhealthy daemon, which is a deploy rollback. `SYS_ADMIN`
is forbidden: remove it from both in one commit (task 2.7).

Three privileged artifacts, each root-owned, each outside `/app` and `/data`:

| Artifact | Path | Mode | Replaces |
|---|---|---|---|
| entrypoint | `/usr/local/libexec/ta-entry.sh` | root:root `0555` | `/app/docker-entrypoint.sh` (chowned to 1001 by Dockerfile:339) |
| launcher | `/usr/local/libexec/ta-launch.py` | root:root `0555` | the proposal's `python -m tinyassets.role_launcher` |
| drop-first exec | `/usr/local/libexec/ta-op` | root:root `0555` | unchanged |

**The entrypoint moves, it is not rewritten.** `deploy/docker-entrypoint.sh` keeps its contents
(the `_platform_credential_env` unset loop, the required-data-file check); only its install path
changes, so root never execs a file uid 1001 can rewrite. `ENTRYPOINT` becomes
`["/usr/bin/tini", "--", "/usr/local/libexec/ta-entry.sh"]`.

**The launcher is Python, isolated, with no owner-writable search path.** It is invoked as
`/opt/venv/bin/python -I -S -B /usr/local/libexec/ta-launch.py`:
- `-I` implies `-E` (no `PYTHON*` env vars, so `PYTHONPATH=/app` is dropped) and, on 3.11+, `-P`
  (the script's own directory is not prepended to `sys.path`);
- `-S` skips `site` entirely, so no `site-packages` `.pth` file — including the editable
  `tinyassets` pointer — executes in the privileged process. The launcher is stdlib-only, so it
  needs nothing from `site-packages`;
- `-B` writes no bytecode.

**The broker's import chain gets a root-owned source tree.** `-S` is wrong for the broker (it
needs `site-packages`: `httpx`, the ledger's dependencies), and `-I` alone would leave it with no
`tinyassets` at all, because the editable install points at the absent `/build`. So:

- the Dockerfile stops chowning the source: `chown -R tinyassets:tinyassets /data /app` (339)
  becomes `chown -R tinyassets:tinyassets /data`, and `/app` stays root-owned `0555` recursively;
- `HOME` moves off `/app` to `/home/tinyassets` (1001:1001 `0700`), created in the image and set
  in both the Dockerfile `ENV` and `deploy/compose.yml`'s `environment.HOME` (today `HOME: /app`,
  compose:147). This is the only reason `/app` was owner-writable;
- the launcher starts the broker as
  `/opt/venv/bin/python -I -B /app/broker_main.py …`, where `broker_main.py` is a root-owned
  `0555` file that does `sys.path.insert(0, "/app")` and then imports — explicit, because `-I`
  removes both `PYTHONPATH` and the script-directory default.

One source tree, no second copy, no parity gate. It also closes a latent instance of the same
class: `ta-op`'s `pulse`/`canary`/`bwrap-oracle` modes exec `/app/scripts/*.py`
(`ta_op.c:109-117`), which uid 1001 can rewrite today. That is **not** an escalation today, because
`ta-op` retires its identity *before* the exec — but it becomes one the moment any privileged step
reads `/app`, and a read-only `/app` retires the question.

*Fallback if a runtime write under `/app` turns up (task 2.1 enumerates them first):* add a root-owned
second copy at `/opt/tinyassets/tinyassets` (`COPY --from=builder` with no `--chown`, before line
339) and a build-time byte-parity gate against `/app/tinyassets`. Preferred only if the `HOME`
move proves to break something, because two copies drift.

**The launcher verifies its own chain before it binds anything.** Following `ta_op.c:202-205`, but
over the whole chain rather than one file: `/opt/venv/bin/python`,
`/usr/local/libexec/ta-launch.py`, `/app/broker_main.py`, every entry of the privileged `sys.path`,
and **every ancestor directory of each**. A writable ancestor is as good as a writable target —
write permission on a directory permits renaming any entry in it — which is why ancestors are in
the set.

The predicate is **root-owned and not group- or other-writable**, applied to every node of the
resolution: each symlink in a chain *and* what it resolves to. It is deliberately not "refuse any
symlink": `Dockerfile:178` builds the venv without `--copies`, and CPython's POSIX venv symlinks
`bin/python` by default, so a refuse-on-symlink check would reject the image's own interpreter.
A root-owned symlink to a root-owned target in a root-owned directory grants nobody anything;
what matters is that no non-root party can replace any link or any target. Task 2.1 also adds
`--copies` so the interpreter is a plain root-owned file, which makes the common case trivially
verifiable — but the resolution walk stays, because `sys.path` entries and `/usr/local/lib`
are not covered by it.

`scripts/check_privileged_chain.py` asserts the same properties at build time against the image,
so the refusal is a backstop and not the only check.

**Why not give the daemon `CAP_SETUID`:** with it, the daemon could `setuid(1002)` and read the
vault, which defeats the owner/broker split the role map exists for.

**Why not user namespaces:** a child in a user namespace mapped to outer 1001 appears to the broker
as 1001 (`SO_PEERCRED` translates to the receiver's namespace). Only distinct kernel uids separate.

### D3. Who spawns what, and with exactly what

Spawn sites move to the launcher client, one call shape:
`launcher.spawn(kind, owner_scope, args) -> Popen-like`. Kinds and argv templates are a **static table in the
launcher**, never caller-supplied strings:

| Kind | Site | Mechanism today |
|---|---|---|
| `provider-cli` | `providers/owned_process.py:539,650-652` | `create_subprocess_exec`, inside the bubblewrap jail |
| `provider-discovery` | `providers/base.py:1418-1420` → `providers/native_jsonrpc_discovery.py:130-134` | `create_subprocess_exec` — **bypasses `owned_process` and the jail entirely**, and runs with `cwd=credential_snapshot_dir` |
| `engine-mcp` | `engine_mcp_http.py:277-283` | `subprocess.Popen`, not jailed |
| `node-sandbox` | `node_sandbox.py` | `subprocess` |
| `tool-jail` | `universe_tools.py:777` | `subprocess.Popen` of the universe agent's own tool jail |
| `workspace-provision` | `workspace_provision_process.py:127` | `subprocess.Popen` with `pass_fds` |
| `workspace-registry` | `workspace_registry_process.py:165` | `subprocess.Popen` of `sys.executable -I -B -c …` over a `socketpair` |
| `workspace-worker` | `workspace_worker.py:677-684` | `multiprocessing` spawn — see D7(c) |

**Historical inventory:** the table above records the original review baseline. D8 below
is the amended, authoritative coverage inventory, including current discovery confinement
and additional children; no historical "not jailed" entry authorizes an unjailed launch.

The last four and `provider-discovery` were missing from the first draft of this table, and
`provider-discovery` is the one that matters: it launches the provider binary directly with a
credential snapshot as its working directory, outside the jail that contains `provider-cli`. The
three `workspace-*` sites are a useful precedent as well as a target —
`workspace_registry_process.py:165` already execs `sys.executable -I -B -c <bootstrap>` with the
package root passed as an argument, which is exactly the isolated-interpreter shape D2 asks for.

On POSIX none of these takes a shell: `owned_process.py:657-658` returns from `_aspawn_anchored`
before the `create_subprocess_shell` branch at 660, which is Windows-only. The launcher accepts no
shell string, no interpreter flag and no caller-supplied path, in either direction.

Per-kind spawn posture, all of it set by the launcher and read back before `execve`:

- **identity** — `setgroups(<the D1 list for the kind>)`, then `setresgid`, then `setresuid`;
  never the reverse order. Readback of all four positions from `/proc/self/status`, as
  `ta_op.c:167-177` does.
- **capabilities** — `PR_SET_NO_NEW_PRIVS`, `PR_SET_KEEPCAPS` clear, ambient cleared, the whole
  bounding set dropped, explicit `capset` to zero, then all five sets read back zero
  (`ta_op.c:209-229`). No child holds any capability. bubblewrap does not need one: it uses
  unprivileged user namespaces, which is why `provider-cli` keeps working.
- **non-dumpability is *not* set here.** `execve` resets the dumpable flag to 1 on an ordinary
  exec, so a `PR_SET_DUMPABLE(0)` in the launcher before `execve` is a no-op. The daemon and the
  broker each set it **on themselves, after exec and before any secret exists** (D7(d)).
- **inherited descriptors** — the launcher sweeps `/proc/self/fd` after the drop and closes
  everything except the three stdio descriptors it received over `SCM_RIGHTS` and the kind's
  declared `pass_fds` (`provider-cli` needs them: bubblewrap reads its seccomp filter from a
  passed descriptor, `owned_process.py:651` / `jailed.pass_fds`). The launcher's own **listening
  socket is `FD_CLOEXEC` and is never in any kind's `pass_fds`** — a child that inherited it could
  request spawns.
- **environment — an allowlist, not a denylist.** `platform_secrets.child_env()`
  (`platform_secrets.py:53-55` on #4267) is `{k: v for k, v in source.items() if k not in
  CHILD_FORBIDDEN_ENV}`: a *denylist*, which inherits every name nobody thought of. A privileged
  launcher must not propagate on that basis. The launcher builds each child's environment from a
  static per-kind allowlist of **names**, and *then* applies `CHILD_FORBIDDEN_ENV` on top, so
  #4267's exclusions keep holding if an allowlist ever widens. The broker's allowlist is
  `PATH=/usr/local/bin:/usr/bin:/bin`, `LANG=C.UTF-8`, `TZ`, `TINYASSETS_DATA_DIR`,
  `HOME=/var/lib/ta-broker`, `PYTHONDONTWRITEBYTECODE=1` — and nothing else. The broker still
  calls `_sanitize_child_environment()` itself (`broker/process.py:88`); the allowlist is the
  outer bound, not a replacement.

  Historical baseline (superseded by the current consumer audit below): the `engine-mcp` kind is where this matters most today: `engine_mcp_http.py:270` builds the
  child's environment as `dict(os.environ)` — the daemon's *whole* environment, not even
  `child_env` — and then adds four `TINYASSETS_ENGINE_*` names plus the port and shared secret
  (271-275). Its allowlist is exactly those six plus the `PATH`/`LANG`/`TZ`/`HOME` basics.

The current `engine-mcp` allowlist must also audit OAuth service and execution-owner tree
consumers (delivery.md), retaining only owner-scoped configuration and scoped service
capabilities. It must never carry the owner channel token or platform credentials.

The `multiprocessing` spawn children do not go through the launcher as they stand: that bootstrap
passes a pipe handle and the resource-tracker descriptor through its own protocol, which an
`SCM_RIGHTS`-stdio `execve` does not reproduce. D7 decides each of them by name.

### D4. Volume ownership and rollback requirements

**Ownership rule, amended by D11:** retain existing owners outside the broker
egress set. The broker owns its ledger and proxy state as uid 1002, group ta-brk;
the privileged startup migration transfers that set in both directions. Old
images require completed reverse migration before opening the ledger. This
replaces the earlier exception limited to `/data/.broker/**`.

This preserves access to pre-existing stores, but does not prove access to new
engine-owned files. D9/F5 adds access/default ACLs and umask 007; D10 requires
explicit startup reverse migration before rollback and capability-free two-pass
deletion/reset. Neither the old-image nor deletion proofs have passed yet.
No ACL widening applies to the vault.

Exact inventory, from the code rather than from the shape of the tree:

| Path | After | Mode | Source |
|---|---|---|---|
| `/data` | 1001:1001 | 0755 | `compose.yml:148`, `storage/__init__.py:185` |
| `/data/<cc>/` | 1001:1100 | 2711 + ACL `g:1102:x` | per-command-center root, `outbound_connections.py:5322` |
| `/data/<cc>/.credential-vault.json` | 1001:**1102** | 0640 | `credential_vault.py:26,155-156` |
| `/data/<cc>/.credentials/` | 1001:**1102** | 2750 | `credential_vault.py:27,180-185` |
| `/data/<cc>/.credentials/<service>/**` | 1001:**1102** | dirs 2750, files 0640 | `credential_vault.py:1100,1205,2235,2365`; `providers/base.py:603`; `credential_vault.py:1111` (`.credentials.json`) |
| `/data/<cc>/.runtime/` | 1001:1100 | 2750 | `credential_vault.py:1783` — created `0o700` today |
| `/data/<cc>/.runtime/provider-launch-credentials/` and each snapshot under it | 1001:1100 | dirs 2750, files 0440 | `provider_jail.py:199`; `credential_vault.py:1784-1808,1846,2069` — the 1003 child's own snapshot |
| workspace trees under `/data/<cc>/`, including `.venv` and `node_modules` | existing uid retained, gid 1100 | dirs 2770; files 0660 plus existing executable bits; uid-1001 ACLs (D9/F5) | `workspace_pool.universe_paths`; skip symlinks without following |
| `/data/.universe-sidecars/` | 1001:1001 | 0711 | relay parent, never mounted into a cell |
| `/data/.universe-sidecars/<cc>/` | 1001:1100 | 2710 | `universe_egress.py` egress and engine relay directory creation |
| exact `egress-*.sock` / `engine-*.sock` relay entries | 1001:1100 | 0660 | runtime-created sockets; only the admitted owner's exact socket is bound |
| `/data/.broker/`, `/data/.broker/state/` | **1002:1101** | 2700 (D12-D13) | `supervisor.py:52-53`, `process.py:89-90` |
| outbound ledger and SQLite sidecars | **1002:1101** | 0600; /data/.broker parent (D12) | `outbound_connections.py:5148-5213`; D12 fixes .broker/outbound.db |
| `/data/.broker/.outbound-proxy/` and private contents (D12) | **1002:1101** | dirs 2700, files 0600 | `outbound_connections.py:4931-4955,6087`; D11 |
| `/data/.layout.lock` | 1001:1001 | 0666 | `storage_layout.py:63-70` creates it 0o666 for cross-uid `flock` |
| shared root stores, sidecars and replacements | 1001:1001 | remove other permissions; retain owner access | D9/F1; never mount in cells |
| remaining classified platform state | 1001:1001 | preserve declared access without widening shared stores | inventory required |

**The shared work group is not an owner boundary.** Its host-side modes are retained for
rollback compatibility, but every 1003 application process receives those rights only
inside D8's namespace. No engine payload may execute with `ta-work` in the host mount
namespace. `/data` itself, sibling centers, shared runtime directories and their directory
fds are never exposed; a bind of the whole data root followed by partial masks is forbidden.

Four things this table settles that the previous draft did not:

**D4.1 The owner stays the vault's writer, and sets the group explicitly.**
`credential_vault.py:~605-632` writes a sibling temp file, `tmp.replace(path)`, then
`_chmod_best_effort(path, 0o600)`. A 1002-owned `0700` vault directory would have broken that, and
even a 1002-owned *file* would be silently replaced by a 1001-owned one on the next deposit.

Setgid inheritance **cannot** carry the vault group either, and the first draft was wrong to rely
on it: `tmp = path.with_name(f"{path.name}.tmp")` puts the temp file in the **command-center
root**, which this table makes setgid `ta-work` (1100) for the workspaces beneath it. The
replacement vault would come out `1001:1100 0640` — readable by every 1003 child and unreadable by
the broker, which is worse than today in both directions.

So the group is set, not inherited: the write path `fchown(handle.fileno(), -1, GID_VAULT)` on the
temp file **before** the atomic replace, so it is pre-commit and a failure propagates rather than
committing a wrongly-grouped vault (the file's existing post-commit block is best-effort by
design, `credential_vault.py:595-603`, and a silent security downgrade must not land there). The
owner holds `ta-vault` for exactly this (D1). The two `_chmod_best_effort(..., 0o600)` calls become
`0o640`. The broker reads; it cannot write. This is strictly tighter than today, where every child
shares the owner's uid and can read a `0600` vault.

**D4.2 One source for these modes, because runtime code re-tightens them.** Changing the two
write-path `chmod`s is not sufficient — four other sites reset the same paths to single-uid modes
every time they run:

| Site | Today | Under the split |
|---|---|---|
| `credential_vault.py:187-188` (`_secret_artifact_dir`) | `.credentials/` and `.credentials/<service>/` → `0o700` | `2750` |
| `credential_vault.py:1783` | `.runtime/` → `0o700` | `2750` |
| `credential_vault.py:1784-1793` | snapshot root → `0o700` | `2750` |
| `credential_vault.py:1808,1846,2069` | each snapshot dir `0o700`, files `0o400` | `2750`, files `0o440` |

The same declaration also covers `universe_egress.py`'s two sidecar directory
creation sites (`mkdir(0o700)`) and both relay socket `chmod(0o600)` sites: use the
D4 sidecar modes/groups on every creation, before publishing a socket. The daemon
already holds 1100 and can assign that group. Only exact validated socket entries
are mounted; directory traverse for jail setup never means exposing the parent.

A literal in each place is how the migration gets quietly undone by the next provider launch. The
modes become a single module-level map keyed on whether the role split is deployed, read by every
one of these sites and by the migration, so the two cannot disagree. That map is the unit under
test, not each call site.
**D4.3 Traverse without list, via ACL.** A directory has one group, and `/data/<cc>/` needs
traverse from both `ta-work` (1003, for workspaces) and `ta-vault` (1002, for the vault). `2711`
gives the group `--x` and `other` `--x`; a POSIX ACL `g:1102:x` gives the broker traverse without
widening `other` at all, and is preferred. Task 2.4 confirms the droplet volume is `ext4` mounted
with ACL support (read-only prod check) and falls back to `2711` if not. Note the ACL is traverse
only — it does not affect group inheritance, which is why D4.1 sets the vault's group explicitly.

**D4.4 The provider child's credential home is *not* `.credentials/`.** `provider_jail.py:316-346`
masks every hidden root entry except `.runtime` — `.credential-vault.json` under a read-only
`/dev/null` bind, `.credentials/` under an empty tmpfs — so a provider child cannot see either,
and `.credentials/` can stay owner+broker-only at 1003. What the child does read is its
per-launch snapshot under `.runtime/provider-launch-credentials` (`provider_jail.py:199`), which
is why those rows are in `ta-work`. Task 2.8 has the oracle enumerate which of the jail's binds
the child must *write* and sets `2770/0660` for exactly those, `2750/0440` for the rest —
measured, not guessed. `provider-discovery` uses the same boundary with the narrower
`metadata_view`: only its exact launch snapshot, no ordinary owner content. The original
unjailed exception is removed. All other engine classes use D8's owner-bound views too.

**Authority.** The migration keeps owner 1001 on almost every path, so euid 0 is *not* the owner of
what it re-modes. `chmod`, `setfacl` and the setgid bit on a 1001-owned path therefore need
`CAP_FOWNER`, and traversing an existing `0700` owner directory needs `CAP_DAC_OVERRIDE`;
`CAP_CHOWN` covers ownership transfers, including D11's egress set, but not the
required chmod/traversal. All three are in the migration
phase of D2's capability table, and the launcher drops them before it serves — so this authority
exists only while the container holds a single process.

**Traversal safety.** The migration runs when the only process in the container is root, under an
exclusive `flock` on `/data/.layout.lock` — that is the primary mitigation, and it is cheap. It is
not sufficient on its own: the volume is a bind mount, so a *previous* container's compromised
1001 process can have planted links in it. So the traversal:
- walks with directory file descriptors it holds open, using `os.open(..., O_NOFOLLOW|O_DIRECTORY)`
  and `*at()` calls relative to them, so a rename between stat and change cannot redirect it;
- uses `os.lchown` / `fchownat(AT_SYMLINK_NOFOLLOW)`, never `chown`;
- skips symlinks inside ta-work trees without following or chmodding targets;
  symlink refusal remains for privileged, vault and broker sets;
- refuses hardlinks in privileged, vault and broker sets. D9/F4 requires proof of
  all aliases within one owner's work set before mutating a work-tree inode;
  unresolved aliases remain untouched and block completion;
- refuses on anything that is not a directory or a regular file.

**Legacy runtime cleanup before traversal/chown.** Under the same exclusive lock,
the root migration idempotently unlinks the obsolete `/data/.broker/owner.json`
using its pinned parent dirfd and `unlinkat` without following links. Never log its
contents. It also removes only known stale relay socket entries (verified socket
type under pinned sidecar dirfds), which the daemon recreates at the declared mode;
other non-regular entries remain a loud refusal. No recursive deletion or traversal
through these entries. This permits the regular-file/directory traversal rule above
to remain intact and avoids asking uid 1001 to clean a broker-owned 0700 directory.

**Crash recovery.** Idempotent by construction — it computes the target owner/group/mode per path
and applies only differences, so re-running completes a partial run. Mirroring
`storage_layout.py:10-19`'s existing discipline, it writes `"roles": {"state": "migrating"}` into
`/data/.layout.json` durably before its first change and `"stable"` after its last; a start that
finds `migrating` re-runs from the beginning rather than assuming the volume is consistent. Bounded
by the volume (about 1.8 GB), and backups skip while the lock is held.

Shared-root-store and remaining-platform-state rows exclude the D11 broker
egress set. A ta-brk gid does not grant daemon file access: private egress files
have no group bits. Only the authenticated IPC endpoint has group socket access.

### D5. The broker's role map and the refusal

`start_broker` checks `os.getuid() == 1001`, that a launcher socket is present, and that the broker
answered with peer uid 1002. Only then does it keep the supervisor. Otherwise it raises
`BrokerUidSplitRequired`, exactly as today (`supervisor.py:162-183`), so a dev host or a misdeploy
fails closed.

### D6. Owner-reachable IPC, separated from private broker state

`/data/.broker/` at 1002:1002 `0700` with a `0600` socket is unreachable by the owner, and #4299's
supervisor touches it five times as uid 1001: `mkdir(mode=0o700)` (`supervisor.py:94`),
`self._socket.unlink()` (104), `Popen` of the broker (105), `connect()` to the socket (116-118),
and the `owner.json` write (127-132) — plus `terminate()` and a second `unlink` in `stop()`
(150-159). Every one of those breaks. They break because four different things share one directory
with one owner. Split them:

| Thing | Path | owner:group | Mode | Written by |
|---|---|---|---|---|
| private broker state (`ops.db`, `fence.json`) | `/data/.broker/state/` | 1002:1002 | 0700 | the broker only |
| its parent | `/data/.broker/` | 1002:1002 | 0700 | created by the migration |
| the socket's directory | `/run/tinyassets/broker/` | 1002:1101 | **2750** (setgid) | created by `ta-entry.sh` |
| the broker socket | `/run/tinyassets/broker/broker.sock` | 1002:**1101** | 0660 | the broker (`umask 0o117`, replacing `0o177` at `process.py:102`) |
| the launcher socket | `/run/tinyassets/launcher.sock` | **root**:1001 | 0660 | the launcher |
| the owner's `(socket, generation, token)` | **nothing on disk** | — | — | — (D7a) |

`/run` is a container tmpfs, root-owned and off the `/data` bind mount, so no socket path sits in
an owner-writable directory and no stale socket survives a restart. Group `ta-brk` (1101, member
1001 only) is what lets the owner connect and keeps 1003 out at the filesystem layer as well as at
the role map.

**The socket's directory must be setgid, and the first draft left it out.** The broker runs
1002:1002 with supplementary group 1102, and `process.py:101-106` only sets a umask — it performs
no `chown`. A socket created in a plain `0750` directory therefore takes the creator's primary
gid, 1002, and comes out `1002:1002 0660`, which excludes the owner just as completely as the
original `0600` did. The setgid bit on the directory is what makes the socket inherit gid 1101.
Mode on a Unix socket is not enough on its own; the group has to be right too, and nothing in the
broker's own code will set it.

**The launcher owns the broker's lifecycle; the owner only fences.** The exact handshake:

```
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/libexec/ta-entry.sh"]
CMD        ["/opt/venv/bin/python", "-I", "-S", "-B", "/usr/local/libexec/ta-launch.py"]
  — the image CMD stops being the daemon's start path (today it is
    `python -m tinyassets.serve`, Dockerfile:354). The daemon's argv moves into the
    launcher's static kind table, so `ta-entry.sh`'s existing `exec "$@"` (line 130)
    needs no change and starts the launcher.
ta-entry.sh (root, 0555, outside /app)
  0. its existing work, unchanged: the _platform_credential_env unset loop (66-92) and
     the required-data-file check (117-128).
  1. NEW: ownership migration (D4), exclusive flock on /data/.layout.lock, released after.
     Root removes legacy owner.json before the broker-directory chown (D4 cleanup).
     This is the one addition to the script's contents — "the entrypoint moves, it is not
     rewritten" (D2) means its install path and its existing logic, not that it gains
     nothing.
  2. NEW: mkdir /run/tinyassets (root 0755), /run/tinyassets/broker (1002:1101 2750)
  3. exec "$@"  → the launcher
ta-launch (root, caps == ta_op.c MASK)
  4. drops CHOWN, FOWNER and DAC_OVERRIDE from all five of its own sets and reads that
     back — the migration is done, and the serving launcher must not keep that authority.
  5. verifies its chain: self, interpreter, broker_main.py, every privileged sys.path
     entry, and every ancestor directory of each, following each symlink and checking
     what it resolves to — root-owned, not group/other writable. Refuses otherwise,
     before binding anything.
  6. binds /run/tinyassets/launcher.sock (root:1001 0660), FD_CLOEXEC, listen()
  7. spawns the DAEMON: 1001:1001, groups [1100, 1101, 1102], no capabilities, NNP,
     environment from the owner allowlist. The daemon sets PR_SET_DUMPABLE(0) on itself
     after exec (D7d) — the launcher cannot, execve resets it.
  8. serves only connections whose SO_PEERCRED reports uid 1001 AND the pid it spawned
     in step 7. Pid-pinning, not just uid: the 1003 kinds are spawned on the owner's
     request, so "any process at 1001" must not be able to ask. Pids are reusable in
     general, but the launcher is the daemon's parent and reaps it, so that pid is not
     reused while the launcher lives.
daemon (1001)
  9. proof = secrets.token_urlsafe(32)   (supervisor.py:79, unchanged)
 9b. START_BROKER{proof_sha256} on the launcher socket.  No generation — see below.
ta-launch (root)
 10. spawns the BROKER: 1002:1002, groups [1102], no capabilities, NNP, broker allowlist
     environment (the broker sets PR_SET_DUMPABLE(0) on itself at start), argv
       /opt/venv/bin/python -I -B /app/broker_main.py
         --socket /run/tinyassets/broker/broker.sock
         --state /data/.broker/state --data-root /data
         --owner-uid 1001 --proof-sha256 <H>
 11. waits, bounded, for the socket to appear; replies BROKER_READY{socket} or
     BROKER_FAILED{reason, exit status}.  A failure is loud: the daemon refuses to serve
     brokered traffic rather than falling back (outbound_connections.py:1099 already
     refuses "selected but not running").
daemon (1001)
 12. connects, FENCE{proof}, reads FENCE_ACK{generation, token}
 13. holds (socket, generation, token) in memory (D7a). Writes no file.
on broker exit
 14. the LAUNCHER restarts it with the identical argv, and increments a restart counter it
     reports on the next launcher call. fence.json is in the broker's own 1002 state
     directory on the volume, so the restarted broker reloads the same (generation, token)
     and the owner's in-memory pair stays valid.
 15. the daemon's client retries on ECONNREFUSED/EPIPE with bounded backoff, and re-runs
     FENCE only if the broker answers UNFENCED.
shutdown
 16. the daemon never signals the broker — it cannot, they are different uids and it holds
     no CAP_KILL. ta-launch forwards SIGTERM to the daemon, waits for it, then the broker,
     then unlinks both sockets.  BrokerSupervisor.stop()'s terminate+unlink
     (supervisor.py:150-159) is deleted.
     This is why CAP_KILL is in D2's table: the launcher is their parent, but euid 0 is
     neither 1001 nor 1002, and being the parent grants no signal permission. Without
     CAP_KILL every step 16 signal is EPERM and the container's stop becomes a 10-second
     SIGKILL from Docker.
```

**The generation moves to the broker.** Today the owner derives it from the previous `owner.json`
(`supervisor.py:75-78`) and the broker verifies the proof *for that generation*
(`process.py:40-48`, `generation != owner_generation`). With no owner file there is nothing for the
owner to read, and the monotonic counter belongs next to the thing that enforces it anyway: the
broker reads its persisted fence and mints `persisted.generation + 1`, returns it in `FENCE_ACK`,
and the owner learns it there. `lease_verifier` loses its `owner_generation` parameter and compares
against the generation the broker minted; `--generation` leaves the broker's argv. This is an
amendment to #4299, named as task 2.6.

### D7. uid 1001 is a trust class, so the owner channel needs a second factor

`broker/server.py` admits an owner stream on two conditions: `self._role == OWNER`, i.e. peer uid
1001 (163, 342-343), **and** `fence.admits(generation, token)` (369, 420). The token is published
to `/data/.broker/owner.json` at mode `0600` (`supervisor.py:125-132`) — and `0600` does not
separate same-uid processes. Every process at 1001 therefore holds both factors.

Every process that runs at 1001 today, from the code:

| # | Process | Site | Disposition |
|---|---|---|---|
| 1 | the daemon | `Dockerfile:354`, `serve.py` | the owner |
| 2 | `outbound-proxy-<grant>` | `outbound_connections.py:5338-5351`, `multiprocessing` | (b) cannot coexist with the broker |
| 3 | `workspace-git-worker` | `workspace_worker.py:677-684`, `multiprocessing` | (c) moves to 1003 |
| 4 | engine MCP child | `engine_mcp_http.py:277-283` | → 1003 (D3) |
| 5 | provider CLI children | `owned_process.py:539,664` | → 1003 (D3) |
| 6 | node-sandbox children | `node_sandbox.py` | → 1003 (D3) |
| 7 | native provider discovery | `providers/base.py:1418-1420` → `native_jsonrpc_discovery.py:130-134` | → 1003 (D3, `provider-discovery`) |
| 8 | the universe agent's tool jail | `universe_tools.py:777` | → 1003 (D3, `tool-jail`) |
| 9 | workspace provision child | `workspace_provision_process.py:127` | → 1003 (D3) |
| 10 | workspace registry child | `workspace_registry_process.py:165` | → 1003 (D3) |
| 11 | short-lived owner tools: `gh` (historical site, absent from this checkout), `git`, the `ta-op` canary | direct exec from the daemon | fixed platform operations only at 1001; owner-scoped work moves to D8 cell at 1003 |

Rows 7-10 were missing from the first draft of this table. Row 7 is the one that changes the
picture: it launches the provider binary through `create_subprocess_exec` directly, bypassing
`owned_process.py` and therefore the bubblewrap jail, with a credential snapshot as its working
directory. An enumeration that misses it would have left a provider process at the owner's uid,
unjailed, holding credentials — the exact thing this change exists to prevent.

Historical reasoning, narrowed by D8: owner-scoped `gh`/`git` must now be jailed.
Row 11 originally explained why "move everything off 1001" was not available: those are the owner's own tools, running
on the owner's own data, and no file permission separates them from the daemon. So the decision is
the explicit one the review asked for — **trust uid 1001 as the owner, and stop making the owner
channel reachable from a file.** Four measures:

**(a) No on-disk owner token.** The only runtime reader of `owner.json` is `_broker_channel`
(`outbound_connections.py:1086-1108`), and it runs *inside the daemon process* — it builds the
brokered channel for a `ScopedConnectionProxy`. Nothing else reads it (`read_owner`'s other call
sites are tests). So the pair moves to process memory: `BrokerSupervisor` exposes `socket_path` and
`fence() -> (generation, token)`, `_broker_channel` takes them from the live supervisor through a
module-level registry instead of calling `read_owner(data_root)` at 1097 and 1103, and
`read_owner`, `OWNER_FILE` and the file itself are deleted. `tests/test_broker_process.py:57-59`
and `98-129` change with it. This is what makes D6's "nothing on disk" row true, and it closes
rows 2, 3 and 7 against a trivial read in one move.

**(b) The legacy proxy worker never coexists with the broker.** `_broker_channel:1095-1099`
already refuses when the broker is selected but not running. Add the converse: when
`broker_selected()` is true, the proxy-worker spawn at `outbound_connections.py:5338` raises
instead of spawning. Child class 2 then does not exist while the broker serves — which closes it
now rather than waiting on S6 task 2.8 to delete the code. Its spawn arguments carry no token
anyway (`5342-5348`: channel, factory reference, config, grant id, scopes), and a spawn child
inherits no memory.

**(c) The workspace worker moves to 1003.** It needs no credential — it runs git over workspace
paths, which `ta-work` covers only inside its D8 owner namespace. Its channel has to change, because `multiprocessing` cannot be
launcher-mediated: the launcher kind `workspace-worker` execs a root-owned entry at 1003 with a
pre-connected `socketpair` passed by `SCM_RIGHTS`, and `run_workspace_worker` reads its channel
from that descriptor instead of `context.Pipe()`. The seam already exists — `workspace_worker.py:667`
takes an injectable `spawn=` for exactly this — so the rewrite is differential-tested against the
current implementation through that parameter (task 2.5).

**(d) The residual, stated rather than hidden — and smaller than the first draft claimed.** An
in-memory secret is reachable by `ptrace` from a process of the same uid, so the trust class
matters. But Yama's `ptrace_scope=1` permits attaching only to one's own **descendants**: a child
the daemon spawned is not the daemon's ancestor, so under the host default no member of rows 2-11
can attach to the daemon. The first draft had that relation backwards. The exposure is a host with
`ptrace_scope=0`, which the container does not own.

The in-container control for that case is `PR_SET_DUMPABLE(0)`, which makes the process's
`/proc/<pid>` root-owned and refuses same-uid `ptrace`. It must be set by the daemon and the broker
**on themselves after exec**, not by the launcher before it: `execve` resets the flag to 1 on an
ordinary exec, so a pre-exec setting is a no-op. The risk is that some 1001 reader of the daemon's
own `/proc` files breaks; none is known, and the oracle proves it (task 2.8). This is defence in
depth. **The hard boundary remains the uid split: 1002 cannot be reached from 1001 without a
capability, and 1003 cannot read the vault at all.**

### D8. Every owner-scoped engine runs inside an owner isolation boundary

**Lead decision, applying founder principles (2026-10-04): cross-user isolation is the
platform's ONLY invariant and is non-negotiable.** The reference shape is Meta Muse as
recorded in [the supplied research](../../../docs/design-notes/2026-10-04-muse-connection-methods.md):
each user's agent has its own `systemd-nspawn` runtime cell, root mapped to an unprivileged
host user, no `CAP_SYS_PTRACE` or `CAP_NET_ADMIN`, a separate credential daemon minting
surrogate tokens, and Sentinel as sole egress authority. This is the design reference,
not a claim that TinyAssets has implemented every Muse mechanism. The decision here is
that no engine identity may reach another owner's data or credential authority.
**"Deny only for jailed providers" is REJECTED.** Waiting for S4/S5 is also rejected.

**Chosen mechanism:** extend the existing bubblewrap provider jail into the mandatory
per-owner launch boundary for every engine kind. Keep uid 1003 and D1's role groups;
namespace reachability, not the shared uid or `ta-work`, separates owners. This is the
smallest sound extension: it reuses the existing jail's validated views, masks, egress
relay and fd protocol, with no per-owner identity allocator or ownership migration.
No class gets an unjailed fallback. If a class cannot run inside this boundary, its launch
fails and implementation must amend the design before substituting per-owner uids/groups.

**Launcher contract and namespace construction:**
- The authenticated daemon supplies a resolved owner/command-center scope, bound to the
  admitted execution, for every request. The launcher validates that scope against the
  trusted owner-to-root mapping and selects a static per-kind view; missing, mismatched or
  multi-owner scope is refused. An engine's argv, env or cwd cannot select an owner.
  Engine MCP servers, worker pools and discovery caches must be keyed by scope; no engine
  process is reused across owners. Multi-owner scheduling remains in the trusted daemon.
- The root-owned bootstrap drops identity/capabilities as D3 requires, then establishes
  bubblewrap confinement before executing any provider, engine or owner-controlled code.
  A fresh mount namespace exposes only the admitted command-center tree and workspace
  subset (at most that owner's data), immutable runtime dependencies and private scratch.
  Never bind `/`, the host `/data`, shared HOME, shared `/tmp` or the host `/run` wholesale.
  Discovery gets only `metadata_view`'s exact snapshot; the decoder needs only input pipes.
- Preserve hidden-root masks, including the vault and `.credentials`, and expose only the
  current launch's runtime subset/snapshot. Do not expose the entire `.runtime` tree.
  Resolve and pin bind sources without following substituted links; reject out-of-scope
  sources, escaping cwd, symlink/hardlink aliases and bind-source replacement races.
  Approved immutable installation mounts cannot contain owner data. A namespace must
  remain closed when owner B creates a new path after A starts.
- Use private PID, IPC and network namespaces (`--unshare-all` as in the provider jail),
  namespace-local procfs and private scratch. Every class gets an empty network namespace;
  no host loopback, host abstract sockets or host network fallback. Together these namespaces ensure a shared uid
  cannot reach sibling processes' `/proc/<pid>/{root,fd,mem,environ}` or ptrace them. Keep
  D7's post-exec non-dumpability for daemon/broker and all capability/no-new-privileges
  readbacks. No host namespace handle, foreign directory/file fd, owner token, or launcher
  socket may cross the fd sweep. Kind-declared IPC is scoped to the admitted owner and
  cannot request another owner's paths or effects. Validate payloads at the trusted receiver.
- Reuse the existing jail's network/egress relay. Bind only the exact owner-scoped relay
  sockets, never their parent sidecar directory or an owner-channel socket. `engine-mcp`
  transport must use an owner-scoped relay/Unix endpoint through that boundary, not regain
  host networking to keep its old listener reachable. Credential authority stays outside
  the cell. Descendants inherit the cell; a nested tool/node jail may narrow it, never widen it.

**Complete coverage contract (paths are under `tinyassets/`; line numbers in D3/D7 are
historical):**

| Engine class / helper | Spawn sites covered | Required disposition |
|---|---|---|
| `provider-cli` | `providers/owned_process.py`: `_aspawn_anchored` and `aspawn_owned` exec paths | launcher plus owner provider view; sync/async and descendants included |
| `provider-discovery` | `providers/base.py` -> `providers/native_jsonrpc_discovery.py` -> `aspawn_owned` | launcher plus exact-snapshot `metadata_view`; current code already requests confinement, preserve it |
| `engine-mcp` | `engine_mcp_http.py`: `_EngineServer.start` | pinned thin proxy in cell; canonical handlers and shared stores stay in daemon (D9/F1) |
| `node-sandbox` | `node_sandbox.py`: both `Popen` sites (workspace `_spawn` and main sandbox launch) | owner cell; nested workspace commands inherit it |
| `tool-jail` | `universe_tools.py`: tool `Popen` | existing tool jail inside/equivalent to mandatory owner cell, not an exception |
| `workspace-provision` | `workspace_provision_process.py`: `Popen`; `workspace_provision_execution.py`: embedded subprocess runner | owner cell; provisioning child inherits it |
| `workspace-registry` | `workspace_registry_process.py`: `Popen`, including calls from provisioning | owner-specific broker, scoped socketpair; no shared cross-owner worker |
| `workspace-worker` | `workspace_worker.py`: `context.Process`, subprocess runner; `workspace_git.py`: runner and `Popen` | D7(c) launcher/socketpair replacement inside owner cell; git descendants inherit |
| `ui-preview` | `ui_preview.py`: `_supervised` | owner view with scoped preview transport; PID supervision alone is insufficient |
| `image-decoder` | `tool_images.py`: `_decode_in_child` | owner-bound process with input/output pipes and private scratch, no data bind required |
| provider auth probe | `providers/base.py`: direct `subprocess.run` auth probe | owner-bound launcher kind and snapshot, never inherited host auth/HOME |
| local box execution | `boxes/local.py`: exec `Popen` | owner cell now for any served owner work; reserved box uids do not defer this requirement |
| owner-scoped utility descendants | `git_bridge.py`, `ingestion/video_extractor.py`, `workspace_git.py`, `workspace_worker.py` subprocess calls | execute within admitting owner's cell; daemon-direct owner-work calls must route through a static launcher kind |

**Remaining inventory dispositions:** `bid/node_bid.py` is control-plane: its shared
`bids/` repository never enters a cell; only fixed platform operations with validated
inputs execute there, never owner payloads or hooks. The daemon and broker are trusted control-plane
roles, not engine exceptions. `storage/outbound_connections.py`'s legacy proxy `Process`
is refused while the broker is selected (D7(b)). D7 row 11's `gh`/`git` designation is
narrowed: any invocation on an owner's behalf goes through the owner cell, including
any restored equivalent of the historical `effectors/github_pr.py` site (absent from this checkout). Only fixed platform operator/health commands
(`ta_cli.py`, `scoped_reset.py`, `ta-op` canary), runtime detection (`sandbox/detect.py`) and
desktop launcher/updater/open-URL helpers may remain control-plane/local tooling, with no
owner payload or credential snapshot. If used for owner work, they must be classified and
jailed before execution. Windows taskkill helpers in `providers/owned_process.py` are not
production Linux payloads. Task 2.5 must reconcile a fresh spawn search (including external
modules and indirect wrappers) with this inventory; any newly found owner-work child is
covered by this rule and added to the class-by-class oracle matrix, never silently exempted.

**Acceptance:** in the Linux oracle built from the production Dockerfile, start owner A's
actual engine-identity process through each production class/site above, not a generic
substitute that only sets uid 1003. Record outer identity/groups, namespace identities and
launch path, and attempt reads/writes of owner B's workspace/data, legacy `owner.json`,
vault/materialized credentials and owner channel token. Every class must deny access;
`ENOENT`/masked content as well as `EACCES` are valid filesystem denial, but B's sentinel
bytes must never be returned. Seed legacy `owner.json` as an adversarial fixture while
also proving startup removes the real obsolete file and never writes a replacement.
Exercise the token held in daemon/broker memory via procfs/ptrace, inherited fds/env and
owner-channel IPC; absence of a token file alone is not proof. Include sibling process
paths, links, bind races, new B files created after A starts, and scope reuse/mismatch.
For each class also attempt connections to owner B's engine-MCP port and relay socket
(and host abstract sockets): all must fail. Prove the legitimate owner-A operation and
relay connection work under uid 1003 with D4's socket/group modes. Preserve the existing
vault write-denial, socket setgid, capability parity, migration and healthcheck proofs.
The current `scripts/linux_oracle.py` builds `docker/linux-oracle.Dockerfile`, not the
production Dockerfile; task 2.8 must add an explicit production-image proof mode/harness
and record its image digest and launch configuration. Its default test image is insufficient.
No skipped/unavailable class or Windows-only check counts as a pass. Implementation and
production-image oracle execution remain pending; this amendment claims neither.

### D9. Round-3 fold and executable acceptance

**founder decision 2026-10-05: fold + build with probes.** F1-F7 are accepted.
This section replaces conflicting historical mechanisms above, not their security
requirements. Every row below is a required production-image Linux oracle probe.
The full refute, including confirmed items, was read. No fourth design review.

**F1 — control-plane stores stay outside cells.** Canonical engine-MCP handlers,
OAuth service configuration and multi-tenant store access move into the daemon.
The `engine-mcp` cell contains only a thin proxy with a preconnected, per-launch
channel pinned to the admitted owner/execution at the trusted receiver. The
receiver rechecks authority; child-supplied owner fields cannot widen it. No
platform credentials or owner-channel token cross that channel. Do not solve
compatibility by mounting shared databases. `node_bid` is control-plane, not an
owner utility. D4 strips other permissions from shared stores, their directories,
WAL/SHM files and atomic replacements; runtime creation preserves those modes.
Probe: A's actual proxy performs a real canonical operation, forged B scope is
refused, B's shared-store sentinel is unreachable, and node_bid still works from
the control plane without exposing its repository to cells.

**F2 — named seccomp profiles and daemon-side readers.** The profiles are:
`cell-deny` (existing default deny_program), `cell-links` (same deny profile
except symlink/symlinkat for git/venv/npm), and `cell-nested` (existing
nested_sandbox=True profile). `cell-links` still denies new user namespaces;
link creation alone must not grant the nested profile. All retain FIFO/device,
io_uring, ptrace and host namespace restrictions. Install after namespace setup.

| Class | Profile | Reason for exception |
|---|---|---|
| provider-cli | cell-deny; cell-nested only for the existing proven nested CLI path | nested CLI sandbox; recorded launch policy, never payload choice |
| provider-discovery, provider auth probe | cell-deny | exact metadata snapshot only |
| engine-mcp thin proxy | cell-deny | channel forwarding only |
| node-sandbox, tool-jail outer cell | cell-nested | nested bubblewrap; inner jail retains its own filter |
| workspace-provision | cell-links | venv/npm symlinks; namespace setup precedes filter |
| workspace-registry | cell-deny | registry channel only |
| workspace-worker, workspace-git, git_bridge | cell-links | git symlink checkout |
| ui-preview, image-decoder | cell-deny | no demonstrated nested requirement |
| local box execution | cell-deny | no exception without a measured nested operation |
| ingestion/video and other owner utilities | cell-deny | default for new kinds |

For every actual class and **each** cell-writable path (workspace, snapshot,
runtime subset, preview output, cache and scratch if a daemon consumes it), the
matrix must enumerate every daemon reader/server: inspect, preview, file reads,
git_bridge, staging/publish and any additional reader found in the code. Each
pair gets symlink-to-B, FIFO and hardlink-to-B probes. Even when cell seccomp
rejects planting, preplant a fixture before launch and exercise the actual
reader. Receivers must use confined reads or pinned no-follow traversal with
regular-file/type and alias validation; never resolve an untrusted path in the
daemon's unrestricted view. No B bytes, B writes or FIFO hang is allowed. A
blocked plant alone is not a reader proof. A path without a daemon consumer
needs inventory evidence, not a silently omitted row. No new nested exception
is accepted without its positive operation and all negative probes passing.

**F3 — close pinned mount descriptors after mount, for every class.** Keep the
pre-mount sweep, then execute a root-owned close-after-mount bootstrap equivalent
to node_sandbox's `_CLOSE_MOUNT_FDS_SCRIPT` before any payload. Close all bind-source,
seccomp and namespace descriptors; retain only declared stdio/scoped IPC. Apply
again at nested boundaries. Probe each actual payload's `/proc/self/fd`, exercise
every retained fd and try directory-relative `openat(fd, "..")`; no descriptor
may lead to a host ancestor, B's data, privileged state or a writable read-only
bind source. Closing merely foreign fds is insufficient.

**F4 — migration preserves real workspace structure.** Skip symlinks without
following them in ta-work trees; do not refuse venv/bin/python or node_modules/.bin.
Re-mode the entire tree, not just roots, preserving executable bits. Privileged,
vault and broker link refusals remain. For work-tree hardlinks, prove all aliases
are in the same owner's classified work set before changing the inode; unresolved
or cross-owner aliases remain untouched and block completion, never silently
widened. Dry-run reports this without mutation. Probe valid venv/npm symlinks,
outside-target sentinels and in-owner/cross-owner hardlinks; repeat migration and
interrupted resume must preserve contents, targets and existing uid ownership.

**F5 — mandatory ACLs, umask, rollback and deletion; resolved by D10.**
Every ta-work directory gets access `u:1001:rwx` and default `d:u:1001:rwx` with
an effective mask; regular files get appropriate read/write and existing execute
access. Require ACL support for work trees: no ACL-less fallback. Every engine
child starts with umask `007`. Probe newly created files and directories as well
as migrated files, including explicit `0600`/`0700` and later chmod; both the
current daemon and an old-image uid 1001 without supplementary groups must read,
write and delete as required. Exercise actual deletion APIs and old-image rollback.

**Resolved access-preservation ambiguity:** Linux intersects inherited ACL permissions with creation
mode and chmod changes the ACL mask. Engine-owned 0700 directories therefore
exclude uid 1001 despite the required ACL entry. Provisioning explicitly creates
such a `.venv`; changing that one call does not cover arbitrary engine code.
`delivery.md` has a reproducible Linux counterexample. The lead explicitly chose
capability-free two-pass owner deletion and startup reverse migration in D10.
ACLs and group-preserving creation remain defense in depth, not the guarantee.

**F6 — no CAP_SYS_ADMIN.** Remove it from compose cap_add and ta_op.c MASK in the
same implementation commit. Keep exactly CHOWN, DAC_OVERRIDE, FOWNER, SETUID,
SETGID, SETPCAP and KILL at entry, then retire migration caps before serving.
Probe capability parity/readbacks and real unprivileged bubblewrap plus ta-op
healthcheck under the production image and compose security options.

**F7 — git trusts only the cell's owner view.** Set safe.directory for exact
admitted repo paths in protected per-cell Git configuration, never `*` or a shared
host global config. Child configuration cannot add a host mount. Probe actual
git status/read/write/checkout in workspace-worker/git_bridge/provisioning and
every other git-using class; the same process remains denied B's repository.

| Confirmed refute item retained | Required pass/fail production probe |
|---|---|
| C1 bubblewrap available | production Dockerfile image, compose seccomp/AppArmor/systempaths options, unprivileged namespace creation without SYS_ADMIN |
| C2 spawn inventory complete at review | repeat repository-wide inventory; every site maps to a real class probe or explicit trusted control-plane disposition |
| C3 private network/IPC and egress | deny B ports/relay sockets, host loopback/abstract sockets; allow only A relay |
| C4 private procfs and non-dumpability | deny sibling procfs/ptrace and token extraction; daemon/broker post-exec readbacks |
| C5 no shared tmp, explicit vault group | tmp isolation; atomic deposit retains ta-vault, broker reads but cannot write, every engine denied |
| C6 exact launcher peer and no fallback | wrong uid, wrong pid, missing scope, failed jail all refuse before payload; PlainSubprocessLauncher remains tests-only |

The oracle must report F1-F7 and C1-C6 per applicable class/site/path/reader with
image digest, launch argv, identity and namespace evidence. No skipped or generic
uid-only substitute counts. Broker launch/stream, healthcheck, migration dry-run,
crash-resume, repeat no-op, rollback and deletion remain separate mandatory proofs.

### D10. Lead technical decision: two-pass deletion and startup rollback

This least-privilege lead decision replaces the prior launcher-maintenance
operation. D2/D6 capability retirement stays exactly as designed: no retained
DAC_OVERRIDE/FOWNER/CHOWN, no separate privileged helper, and no runtime root
maintenance API. All brief rules, probes and stop conditions remain in force.

1. Owner-tree deletion/reset is two-pass with **no capabilities**. Pass 1 runs
   **as engine uid 1003 inside that owner's cell**, through the launcher's normal
   authenticated cell spawn. It removes engine-owned entries using pinned,
   no-follow openat-based traversal confined to the cell's view of that owner's
   tree. Pass 2 runs as daemon uid 1001 and removes daemon-owned entries and the
   now-empty structure. Apply this to account deletion, scoped_reset, workspace
   pool removal and other owner-tree cleanup sites. Neither pass can silently
   report success after partial deletion: an entry it cannot remove fails loudly
   with the path (Hard Rule 8). A failure is not atomic rollback of prior unlinks.
2. The launcher binds pass 1 to the admitted owner's scope using its normal
   exact daemon uid-and-pid check and static cell view. No foreign tree, host
   ancestor fd or symlink traversal enters that view. Audit scope, operation,
   pass and outcome without contents, credentials or owner tokens. Do not add
   root delete-tree/reset-tree/chown-back operations.
3. Reverse migration runs **at container start before capability drop**, in the
   same privileged window and code path as forward migration, selected by an
   explicit opt-in env/flag. Hold the exclusive layout lock with no role running;
   use pinned no-follow traversal and the existing hardlink alias protections.
   It is idempotent, crash-recoverable and dry-run capable. Restore engine-created
   content, including 0600/0700 and later chmod, to uid 1001 read/write/delete
   access before an old image starts. Never delete user data. The startup rollback
   operation exits before normal service, so forward migration cannot undo it.
   The runbook is rollback.md; actual CLI spelling and proof remain build work.
4. Keep access/default ACLs and child umask 007. Known explicit owner-work
   0700/chmod sites, including venv creation and workspace lease directories,
   use shared group-preserving 0770/2770 modes. Do not widen vault, materialized
   credentials, broker state or paths outside classified owner work.

Mandatory production-image Linux oracle rows (compose security options):

| Probe | Required result |
|---|---|
| Actual account deletion/scoped_reset on engine-created 0700 trees | pass 1 is 1003 in A's cell with zero capabilities; pass 2 is 1001 with zero capabilities; deletion/reset succeeds |
| Pass 1 targets B or tries a symlink escape | no B/outside contents or metadata changed; no traversal outside A's view |
| A pass cannot remove an entry | loud failure includes its path; no false success or silent partial deletion |
| Startup reverse migration dry-run/apply/repeat/interrupted resume | dry-run changes nothing; repeat no-op; resume completes; no data deleted |
| Actual old image after startup reverse migration | uid 1001 without work group reads/writes/deletes restrictive engine-created content |
| Launcher capability retirement | CHOWN/FOWNER/DAC_OVERRIDE absent from all five sets before service; existing drop/refusal probe still passes |

The prior capability-lifetime ambiguity is resolved by moving reverse migration
to startup and performing deletion with the owning identities. No retirement
probe is weakened and no capability is reacquired after retirement.

### D11. Lead decision: the broker owns its egress state

The lead explicitly assigns `outbound.db` (ledger, accounting and refresh state)
and `.outbound-proxy` to broker uid **1002**, group **ta-brk (1101)**. The reference
shape is the supplied Meta Muse Sentinel + hatch-authd architecture: the sole
egress/credential authority lives outside the agent cell. This is the selected
TinyAssets authority boundary, not a claim of a new external security review.

Create/migrate the complete egress set during the privileged startup window,
including SQLite journals/WAL/SHM and proxy runtime files. Private files use
0600 and private directories 2700 (0700 access plus setgid ta-brk inheritance).
The socket exposed for authenticated daemon IPC retains D6's 2750 directory /
0660 socket policy. Private proxy state never becomes daemon-readable merely
because the daemon holds ta-brk. D10 reverse migration restores old-image access
and location before an old uid-1001 process starts; no data is deleted.

The daemon and every engine class never open these private paths directly.
Daemon ledger queries/mutations, accounting reads and refresh triggers use the
broker's authenticated daemon IPC (kernel role plus the live in-memory fence).
Preserve authenticated principal and command-center admission, operation
identity, revocation, accounting and cancellation checks. No raw SQL, arbitrary
method dispatch, caller-selected filesystem path or serialized callable crosses
the channel. Engine callers retain only the exact scoped proxy exposed into
their admitted cell; no owner-channel token, ledger fd or private directory fd.
An unsupported route fails loudly; no local-database or legacy-worker fallback.

[broker-access-inventory.md](broker-access-inventory.md) enumerates current
direct and indirect entry sites with their intended route. These are required
implementation dispositions, not claims that IPC routing already exists.
Account deletion's generic database walker and raw accounting SQL must be
adapted too. Existing accounting is actually in `.tinyassets.db`, not
`outbound.db`; its table migration and liveness preservation must accompany the
IPC route. D4's read-only broker access to the vault remains a constraint: the
current local refresh path cannot be called unchanged by uid 1002. Retain
admission-before-spend and durable rotation; never grant vault write as a shortcut.

**Historical physical-parent blocker (resolved by D12 below):** D4 keeps `/data` 1001:1001/0755. Chowning
`/data/outbound.db` alone permits file open but not a SQLite write requiring a
sibling journal, nor fresh database creation. The production-image diagnostic
in delivery.md proves both failures and a successful private-parent control.
Requested clarification: relocate the ledger to `/data/.broker/state/outbound.db`
with crash-safe forward/reverse relocation, or explicitly define another parent
authority. No broad write ACL on `/data`, journal disabling, broker capabilities
or symlink through a broker-set link refusal is inferred. Relocation also needs
the generic account-deletion and strict backup inventories updated; ledger parent
must cease to mean data root in broker dispatch/accounting configuration.

Mandatory additional production-image oracle rows, alongside all D8-D10 rows:

| Probe | Required result |
|---|---|
| Broker existing/fresh ledger and proxy state | actual uid-1002 create, schema upgrade, transactional write and proxy setup succeed with all capability sets zero |
| Daemon direct access | uid 1001 with its real supplementary groups cannot open private ledger, sidecars or proxy state |
| Each actual engine class direct access | no private egress bytes/fds through filesystem, procfs, IPC, aliases or inherited descriptors; legitimate scoped proxy still works |
| Daemon accounting and ledger IPC | real authenticated request succeeds; wrong peer, fence, principal or scope is refused; no fallback open |
| Refresh trigger | authenticated broker route preserves admission-before-spend, rotation durability and vault write denial |
| Forward/reverse migration | dry-run unchanged; interrupted resume; repeat no-op; old-image uid 1001 reads/writes after reverse migration, including ledger journals |

No build checkbox is proven by the diagnostic or this decision record.

### D12. Lead decision: relocate the ledger and proxy runtime

Relocate `/data/outbound.db` and its SQLite sidecars to
`/data/.broker/outbound.db`, and `/data/.outbound-proxy` to
`/data/.broker/.outbound-proxy`. Startup creates the broker-owned parent in
D10's privileged window. Never widen `/data` write access. This resolves D11's
physical-parent blocker. Every D11 inventory consumer must use the authenticated
broker interface; the logical data root is explicit, never the ledger parent.

Under the exclusive layout lock with all roles stopped, validate the complete
source/destination set without following links, checkpoint WAL before movement,
fsync files and directories, then rename on the same filesystem with durable
progress. Resume must distinguish source-only, destination-only and conflicting
copies; refuse conflicts without overwriting or deleting data. Include retained
sidecars and proxy contents. Dry-run does not checkpoint or change metadata.
Reverse startup migration checkpoints and restores the original paths, ownership
and usable journal parent before the old image starts. Backup and account deletion
must explicitly include relocated state. Broker existing/fresh ledger writes,
daemon/engine denials and old-image rollback are mandatory acceptance probes.

### D13. Mechanical decision: private broker parent permissions

Use uid 1002, gid 1101, mode 2700 for the private parent and private proxy
directories; files 0600. The lead's 0750 example would grant directory access to
the daemon through its IPC group 1101, conflicting with the required denial.
Setgid retains the specified group without granting it access. `/state` remains
private. D6's public IPC directory remains separate on `/run`, mode 2750.

### D14. Mechanical decision: immutable image foundation and chain checks

Keep the existing rootless CMD until migration and launcher integration are ready;
do not activate root with the old daemon CMD. The ordered foundation commit adds
role accounts without supplementary memberships, copied venv interpreters,
root-owned source and entrypoint, broker bootstrap, HOME relocation and the chain
gate. Task 2.1 remains unchecked until the launcher/CMD portion is integrated.
Runtime-write audit: configured stores and auth DB use TINYASSETS_DATA_DIR;
provider homes/snapshots and node workspace binds use owner trees; scratch uses
private temporary paths; the health canary is read-only. The Codex wrapper uses
CODEX_HOME or HOME, falling back to /tmp. No required /app write was found in
these paths. Use one immutable source tree, not the duplicate-copy fallback.
Symlink mode bits are not Linux access controls: check link ownership, all
ancestors and resolved target permissions (including intermediate targets).

### D15. Mechanical decision: relocation is a fenced startup substep

`deploy/role_egress_migration.py` is a stdlib-only substep of the forthcoming
startup migration, not an additional privileged service or an activated CLI.
It requires an initialized layout-2 marker/lock with the consent move complete,
and refuses overlapping migrations before mutation. Only its own interrupted
role progress is resumable. It records roles.egress progress and leaves the
TOP-LEVEL marker migrating in both directions; the complete role migration
alone may mark the layout stable after every forward/reverse substep. This
prevents the existing consent recovery path admitting an old daemon onto a
relocated ledger. No service-start wiring or path-consumer switch is activated
until the launcher, full migration and D11 IPC routes are ready.

The production oracle mode runs the shipped script from the image digest,
without live mounts or network and with exactly the planned entry capabilities.
Foundation/egress substep probes are labeled separately from the still-required
actual launcher, IPC, engine-class, full deletion and old-image proofs.

### D16. Mechanical decision: stage the broker lifecycle before startup admission

The installed stdlib launcher kernel retires migration capabilities, verifies the
immutable chain before binding, and accepts START_BROKER only from its exact
daemon child pid and uid. The socket uses SOCK_SEQPACKET with a 4096-byte bound;
extra fields, truncation and any descriptor transfer are refused. No generic
exec, shell, environment, caller-selected path or engine-without-cell operation
exists. Engine kinds remain unsupported until their owner cells are integrated.
The launcher uses its already-approved SETGID capability briefly to create the
root:1001 socket and inspect broker socket readiness after CHOWN retirement;
it restores egid 0 before handling another request. No privilege is added.

Broker-owned generation allocation persists the non-secret lease-proof hash
beside the existing fence, so the same acquisition recovers its generation/token
after crash or lost ACK and a new acquisition increments it. The role-split
broker accepts FENCE with proof only and rejects caller-selected generations.
The old explicit-generation mode remains only for the existing inactive legacy
supervisor/tests until task 2.6 removes that path; start_broker still refuses
production activation. No owner token file is written on the new path.

Broker-local ConnectionLedger receives an explicit logical data_root distinct
from its private physical parent. Command-center/authority/accounting references
use that logical root; proxy persistence follows the ledger into .broker.
This does not grant the broker access to daemon accounting or implement D11 RPC.
The private broker umask is 077 (except socket creation at 117); engine umask
007 remains required. The new broker checks retired identity/capability sets
and makes itself non-dumpable after exec before loading state.

This is an inactive integration substep under D14/D15. CMD, compose capabilities,
entrypoint migration, daemon environment/spawn and production broker selection
remain unchanged. The production-image oracle uses a trusted daemon fixture
child with the real launcher/broker; it must label that evidence separately
from real daemon startup, successful streams, every engine class, deletion and
old-image rollback. No build task is complete solely from this substep.

### D17. Mechanical decision: preserve setgid without CAP_FSETID

The production launcher probe demonstrated that fchmod silently clears a
requested directory setgid bit when the caller lacks that group and FSETID,
even with FOWNER. During the existing privileged startup window, temporarily
set egid to the target gid around directory fchmod, restore it in finally,
and assert uid/gid/mode from fstat. SETGID is already required; do not add
FSETID. Apply the same helper to relocation and startup IPC directory setup.
The oracle asserts actual 2700 private parents and 2750 socket parents.

### D18. Mechanical decision: daemon acquisition holds no disk credential

Replace the old spawning supervisor with a daemon-process registry of acquired
broker channels. The launcher parent is authenticated by SO_PEERCRED pid/uid/gid
before sending the lease hash; the broker uid/gid is authenticated before any
proof or owner token is sent, including each stream connection. Only uid 1001
with the declared groups, retired capabilities and NNP may acquire a channel;
it becomes non-dumpable before generating the proof. Registry objects reject
use after fork and stop invalidates existing clients without signalling a broker
or unlinking its socket. A repeated start in the same daemon reuses its acquisition.
Generation is allocated by the broker in both the deployed and test process
entry paths; remove the legacy generation argument and owner.json reader/writer.
The per-grant worker refuses before constructing any worker or OAuth channel
when broker mode is selected. These are D6/D7 consequences, not new authority.

Production startup remains gated by the unactivated launcher CMD. The ordinary
Linux unit fixture substitutes only process startup and peer identities so it
can run without capabilities; production-image acceptance uses the real launcher,
1001 supervisor and 1002 broker. Missing-grant refusal proves the live channel,
not successful streaming or D11 ledger/accounting/refresh consumer routing.

### D19. Mechanical decision: named consistent broker ledger reads

Route the two raw-SQL consumers `discovery_snapshot._context` and
`connection_uses.model_use_refusal` through `DISCOVERY_FACTS` and
`HAS_PRICED_SOURCE` on the existing daemon broker socket. Authenticate the
kernel owner role and live in-memory fence before constructing a ledger; hold
the fence across one transaction that checks the principal, command center,
live grant and live connection. Return an explicit projection, never a database
handle, caller-selected method, SQL or path. Discovery selects the priced
catalogue before a declared list in that same snapshot, retaining its existing
digest and typed refusal contract. Malformed pricing remains a refusal rather
than an unpriced declaration.

Connect asks precede first deposit. For the pricing-presence query only, a
transaction proving both proposed connection and grant IDs absent may return
false. Any existing row requires the full scoped live-grant check; missing,
foreign, mismatched or revoked authority is not treated as free. This query is
advisory: mutation-time pricing/admission checks remain required.

Select routing before ledger construction. Broker-selected-but-unavailable
fails loudly; the local route is only for unsplit runtimes with broker mode off.
This is an incremental D11 consumer conversion, not a generic RPC facade or
activation. Remaining discovery HTTP, mutation, accounting, refresh, deletion
and backup consumers stay unproven; startup remains disabled. The production
oracle seeds only synthetic ledger rows before capability retirement and then
exercises both actual daemon consumer functions through the launcher-owned
broker, before and after restart, while direct ledger access stays denied.

### D20. Mechanical decision: discovery HTTP uses scoped broker facts and streams

Add the named GRANTED_RESOURCE read using D19's authenticated owner channel,
live fence and single-transaction live grant/resource check. Its explicit
projection omits model profiles: bootstrap and profile repair must be able to
read a granted catalogue before a model descriptor exists or parses. This
does not bypass the endpoint, method or SSRF checks on the actual HTTP stream.

In broker-selected mode, discovery HTTP obtains that projection before URL
validation, then uses the existing exact grant/connection broker channel. The
broker rechecks authority at stream admission, including revocation after the
query. Unavailable or fenced queries never construct a daemon ledger. The
legacy local resolver is retained only when broker mode is off. Existing JSON
parsing, response bounds and credential-blind errors are preserved.

The production-image probe exercises scoped reads and actual HTTP consumer
refusals through the launcher-owned broker; the Linux regression exercises a
successful IPC stream with a scripted upstream. Neither is claimed as a
successful production HTTP stream. Activation and all remaining class,
migration, accounting, refresh, deletion and old-image proofs remain gated.

### D21. Mechanical decision: retain strict ledger backup after relocation

The host backup's existing shared layout lock also covers the explicit
`.broker/outbound.db` source. Include it in the strict SQLite-backup brain tier
at its original relative path, preserving its private parent and file uid/gid
and modes. The SQLite backup API includes committed WAL data; never substitute
a live file copy for this ledger. Full-volume tar already includes the private
subtree. Legacy root-level ledgers remain supported for reverse migration.

Verify source file and broker parent identity before and after the SQLite copy;
reject observed symlinks, aliases, nonregular sources, inaccessible broker
directories or failed copies before upload. These checks also tighten legacy
root-level databases, which previously followed aliases. They do not claim
race-proofness against a malicious writer performing an ABA replacement.
The existing backup trusts the running daemon/broker and holds the layout lock.
Omit the staging root header from the brain archive so repair cannot overwrite
the live volume root with the staging directory's root:root/0700 metadata;
the staging directory itself remains private throughout. This is host
maintenance within the existing backup authority, not daemon file access or a
new privileged service. This step proves ledger backup and archive metadata;
full role/ACL restore and actual old-image rollback remain separate obligations.

### D22. Mechanical decision: preserve HTTP opt-in and prove real broker streaming

The launcher preserves the existing nonsecret deployment switch
`TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` as a canonical 0/1 in its static
broker environment. Absent remains absent/disabled. The initial real HTTPS
probe exposed that the previous allowlist dropped this switch and therefore
disabled the trusted HTTP transport even on an opted-in deployment. No caller
can set it over IPC; no TLS, SSRF, grant, fence or vault check is bypassed.

The optional production-image `--production-stream` oracle creates a disposable
internal Docker network with public-numbered IPAM so the ordinary SSRF policy
can run unchanged, a synthetic HTTPS fixture and a volume containing only its
public certificate. It installs that CA only in the disposable probe container,
never the image or host. There is no external route, published port, real secret
or host-directory mount. The fixture has zero capabilities. The launcher and
broker retain the same entry/serving/child authority as the network-none oracle.
The actual discovery consumer streams a synthetic GET through the real launcher,
broker, credential resolver and TLS transport, including after broker restart.
This proves HTTP streaming, not inference accounting, refresh or engine classes;
startup activation remains gated on all of those and full migration/rollback.

### D23. Mechanical decision: reuse live scoped facts for three daemon consumers

Compute grant validation, model-access custody-incarnation capture and source
display naming use the existing GRANTED_RESOURCE query. Its transaction checks
the live grant, principal, center and connection together. Validate the received
projection before use; preserve the incarnation from that same snapshot rather
than opening a second ledger. No new broker operation or authority is added.
Foreign/missing/revoked compute grants retain uniform not_found; model-access
capture refuses changed authority. Display decoration retains its existing
empty-label failure contract. None falls back to a daemon ledger in broker mode.
The unsplit path uses the same query locally; a revoked source cannot now be
captured or displayed there either. The launcher oracle exercises all three
actual consumers before and after restart; this is partial D11 conversion only.

### D24. Mechanical decision: effector authority is one broker snapshot

Route authenticated external-call authority, proxy acquisition and bound-request
preview through a named AUTHORIZED_CONNECTION ledger query. The existing D11
principal/center/grant/connection checks apply to one transaction returning the
live resource, grant action cap and custody incarnation. Validate projection
scope before use. Effector authority comes from the admitted execution context
or authenticated ambient identity, never from the packet or inferred grant owner.
Preserve connection access mode in the credential-blind proxy; the broker still
rechecks live authority before sending. Bound preview hashes the same snapshot's
incarnation instead of opening a second ledger. Selected-but-unavailable broker
and malformed replies fail without a local fallback. This adds no privilege or
security scope; it implements three existing D11 inventory obligations.

### D25. Mechanical decision: serving custody reads reuse scoped broker facts

In selected mode, serving context and initial connection-id lookup use the
existing GRANTED_RESOURCE transaction. Thread the independently admitted owner
from serving validation and provider assignment into both lookups; do not infer
the actor from definition/grant rows. Require the definition owner to match,
then let the broker enforce the live principal/center/grant scope. Preserve
the existing subsequent custody-digest comparison against the current credential
reference. Refuse missing/foreign/revoked authority and unavailable broker without
local ledger construction. No new broker operation or privilege is introduced.
The unsplit path retains its existing API behavior, including test-only baseline
callers that omit the optional owner parameter. This is partial D11 conversion.

### D26. Mechanical decision: HTTP compute uses the admitted invocation owner

The router overwrites HTTP compute's internal invocation-owner field from the
validated serving authority or work-carrier receipt at dispatch, clearing caller
input first. A provider definition or grant cannot supply that principal. In
broker-selected mode the executor requires the definition, running center and
admitted owner to agree, obtains GRANTED_RESOURCE, then reacquires exact scoped
authority with AUTHORIZED_CONNECTION before opening the existing broker stream.
Preserve resource access mode, usage-reference forwarding, cleanup and response
decoding. Missing/revoked/foreign scope, unavailable broker and malformed replies
never construct a daemon ledger. The unsplit development path is unchanged.
Query/acquisition failures are known-not-sent ProviderUnavailableError outcomes,
so the router releases unused served reservations; errors after request dispatch
retain conservative usage semantics. No send error is relabeled as unsent.

No broker operation, privilege or isolation scope is added. Production-image
probes cover actual compute source reads and proxy acquisition before and after
broker restart; a separate Linux IPC test uses a scripted upstream to exercise
the complete executor. That scripted response is not production inference
acceptance. Actual inference POST remains gated by the D11 accounting migration;
do not weaken the required usage reference or grant broker access to daemon stores.
Startup remains unactivated pending the full acceptance matrix.

### D27. Mechanical decision: capability metadata uses scoped broker transactions

Add the named CAPABILITY read/configure operation on the existing fenced owner
channel, with a closed field set and four existing capability kinds. Resolve
principal/center/grant/connection scope, then recheck the grant timestamp, owner,
center and live resource inside the actual read or mutation transaction for every
kind. Existing discovery, pricing, endpoint and descriptor checks remain. No SQL,
path, callable or arbitrary method crosses IPC. A lost mutation acknowledgement
is reported as unavailable and never automatically replayed. Error projections
contain fixed classes, not persisted descriptors or secrets.

Connection-use configuration, provider capability configuration and voice binding
read/configuration use this route. Voice proxy acquisition reuses D24's exact
broker proxy. CONNECTION_GRANTS selects live grant IDs for one admitted principal,
center and connection in one transaction, replacing the configuration consumer's
local list. More than one grant still refuses configuration. The local development
route remains available only when broker selection is off. This is partial D11
integration; accounting, refresh and the other remaining consumers still gate
startup, together with all engine/migration/rollback acceptance.

### D28. Mechanical decision: grant catalogs page within one admitted scope

The named CONNECTION_CATALOG operation returns redacted connection views, grant
metadata and custody incarnation for one admitted principal/center. Each bounded
page uses a joined SQLite snapshot filtering both row owners and both revocations;
the cursor is the last grant ID, never a path or SQL. The daemon iterates pages
for consumers that require the complete catalog, retaining existing caller limits
where explicitly bounded. Pages are individually consistent, not a promised
multi-page snapshot; actual effects still reauthorize at use. No credential
reference or capability descriptor is included. Malformed replies or broker
outage never trigger a local fallback. This routes daemon catalogs, not engine
filesystem access or startup activation, and adds no privilege.

### D29. Mechanical decision: offline accounting transfer verifies before dropping

Move only the four agent_request_usage/attempts/usage_links/dispatches tables
from .tinyassets.db into the relocated broker ledger. The existing stdlib startup
migration owns this substep under the layout lock with all roles stopped. Copy
to a committed destination transaction, verify exact typed row fingerprints and
schemas, durably record progress, and only then drop source tables in one source
transaction. Unrelated tables remain untouched. An interrupted transfer resumes
from its manifest; divergent copies refuse before deletion. Reverse runs before
reverse egress relocation and restores these tables to .tinyassets.db. Both
directions leave top-level roles/layout migrating until full role admission.

Dry-run uses disposable copies outside the data root, including retained WAL,
so SQLite cannot modify source journals/SHM. Preflight refuses symlinks, hardlinks,
nonregular files, unknown accounting schemas and conflicting destination tables.
The known schema is static in the isolated migration and parity-tested against
the runtime schema. No service or capability is retained; this is accounting
table transfer only. Daemon accounting IPC, source/liveness checks, refresh and
full migration activation remain required after this substep is proven.

### D30. Mechanical decision: source-budget facts use live broker authority

In selected mode, request-budget source classification uses the existing scoped
GRANTED_RESOURCE transaction instead of opening outbound.db. Require the admitted
owner to agree with the installed definition. Preserve host-based source policy,
but refuse unavailable, foreign or malformed broker facts rather than classifying
an unreadable metered source as unmetered. Advisory budget rendering may still
report unknown through its existing wrapper. No new IPC operation or privilege
is added; usage-table IPC and kernel liveness preservation remain separate work.

### D31. Mechanical decision: bootstrap recovery reads remain inert and scoped

Bootstrap candidate capability reads use GRANTED_RESOURCE and CAPABILITY. Pending
confirmation recovery needs its prior ability to display an owner's revoked
connection, so add BOOTSTRAP_RECOVERY as a metadata-only query: exact grant,
principal and center join, projecting only destination and discovery descriptor.
It never returns credentials, authority, a proxy or permission to activate.
Foreign or absent records produce no match. Actual setup and consent activation
continue to require live grants through existing operations. Selected mode never
opens a local ledger, and broker errors remain explicit. This implements existing
D11 bootstrap readers without changing their owner scope or adding privilege.

### D32. Mechanical decision: graph connection inventory uses scoped pages and capabilities

The authenticated graph connection-list consumer uses CONNECTION_CATALOG pages
and CAPABILITY reads for model-use/constant-header metadata in selected mode.
Scope derives from the authenticated actor and already-authorized center, never
from returned rows. Each capability read rechecks live authority; revocation
between the page and detail read refuses, and outage never opens a daemon ledger.
Preserve the existing redacted projection, uses and per-center workspace consents.
This closes the injected connection_uses_view reader for this consumer, adds no
broker operation or privilege, and does not complete remaining mutations.

### D33. Mechanical decision: disconnect is a fenced, incarnation-bound broker operation

HTTP removal uses named inspect/fence/erase steps on the existing owner channel.
The broker derives the HTTP identity from the admitted center and destination,
checks the owner and custody slot in the same transaction, and requires the
observed incarnation for both mutations. Erase requires prior revocation and
removes only that connection's ledger rows. Missing rows are idempotent; a
replacement incarnation refuses. Revoked rows and interrupted deposits without
grants remain recoverable through the deterministic center identity. The daemon
keeps assignment admission and vault writes: fence egress before releasing
assignment admission, then delete custody, then erase ledger rows. A lost ACK
fails loudly without automatic replay or local fallback. This implements D11's
existing removal authority, adding no privilege or scope. Startup stays inactive.

## Risks / Trade-offs

- **The launcher is root-adjacent code.** One file, stdlib-only, run `-I -S`, a static kind table,
  no shell, an allowlist environment, a self-verified chain, and a cross-family security refute
  before build. It serves one pid.
- **`/app` becomes read-only and `HOME` moves.** The largest behavioural risk in this change, and
  the one with a named fallback (D2). Any runtime write under `/app` fails loudly rather than
  silently, which is the right direction; task 2.1 enumerates them first.
- **The capability set is wider than the first draft said.** Seven,
  because the migration needs DAC authority over paths it deliberately does not own and the
  launcher needs to signal two foreign uids. Mitigated structurally rather than by wishing: the
  migration's three are dropped before the launcher serves, and the whole set is held equal to
  `ta_op.c`'s `MASK` so the two cannot drift. `CAP_SYS_ADMIN` is forbidden
  (task 2.7); remove it from compose and `ta-op` in the same commit.
- **Two correctness traps this design walks into unless implemented exactly as written**, both
  found by review rather than by reasoning, and both now spec'd: a socket directory without the
  setgid bit yields a socket the owner cannot reach (D6), and a vault group left to setgid
  inheritance comes out as `ta-work` and is readable by every engine child (D4.1). Each is a
  one-line implementation detail whose absence silently inverts the boundary, so each has its own
  oracle proof in task 2.8.
- **Bubblewrap under a non-owner uid.** The provider jail binds workspace paths; group access must
  cover what the jail needs, and the Linux oracle — which runs non-root, like production — proves
  it rather than a local Windows run.
- **The migration touches every workspace file once**, bounded by the volume (about 1.8 GB), under
  the layout lock, with backups skipping while it is held.
- **Two unmerged dependencies** (#4299, #4267). The build tasks amend both; neither is vendored.

## Verification

- Prod read-only first: `docker exec … ps -eo uid,comm` shows 1001/1002/1003 per role after
  deploy, and `stat` shows the broker-owned state directory and the `ta-vault` group on the vault.
- `scripts/check_privileged_chain.py` against the built image: no node of the entrypoint, launcher,
  broker or privileged `sys.path` chain — no ancestor directory of one, and no link or target in
  one's resolution — is non-root-owned or group/other-writable.
- Every D8 class/site must pass the production-image Linux oracle cross-owner denial matrix
  and its own positive control; one jailed provider is not representative of other classes.
- A child kind spawned as 1003 gets `EACCES` on `/data/.broker/state/fence.json` and on
  `/data/<cc>/.credential-vault.json`, proven in the oracle and on prod by a probe child.
- The broker, as 1002, can read the vault and **cannot write it** (`0640`) — and the vault that a
  *deposit* leaves behind is still `ta-vault`, not `ta-work`.
- The owner connects to a `1002:1101` broker socket, which is what the setgid socket directory is
  for; and the launcher signals both children at shutdown.
- `ta-op pulse` stays green on the new root entry — the healthcheck is the deploy's own gate, so
  this is proven before the deploy, in the oracle.
- A provider launch after the migration leaves every mode the migration set, rather than resetting
  `.credentials/`, `.runtime/` or a snapshot to its single-uid mode.
- The migration run twice makes no second change, and a run killed mid-way leaves
  `"roles": {"state": "migrating"}` and completes on the next start.
- `TINYASSETS_CREDENTIAL_BROKER=process` starts and serves one owner stream, with per-turn RSS
  measured (the target is about 100 KiB per stream versus 29 MiB per worker).
