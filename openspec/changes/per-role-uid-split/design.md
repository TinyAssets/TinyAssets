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
- **Non-goal:** per-command-center uids for user content. Boxes (S4/S5) bring their own isolation;
  this change only reserves their uid range.
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
| broker | 1002:1002 `ta-broker` | 1102 | `/data/.broker/` (0700) |
| engine / provider children | 1003:1003 `ta-engine` | 1100 | nothing; writes only through group `ta-work` |
| boxhostd (S4/S5) | 1004 reserved | — | — |
| per-box uids | 200000–299999 reserved | — | openshell-spike defines |

| gid | Name | Members | For |
|---|---|---|---|
| 1100 | `ta-work` | 1001, 1003 | workspace roots, `2770`, setgid |
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
| `SYS_ADMIN` | — | inherited from `ta-op`'s existing assertion; necessity unproven |

The **launcher drops `CHOWN`, `FOWNER` and `DAC_OVERRIDE` from all five of its own sets and reads
that back before it binds its socket.** The long-lived privileged process therefore never holds the
authority to read or re-mode arbitrary owner-owned files; that authority exists only during the
migration, while the container holds exactly one process.

`ta_op.c`'s `MASK` (`ta_op.c:81-82`) must equal the container's set, because the container
healthcheck enters `ta-op` at uid 0 and that file asserts *set equality* (`ta_op.c:206-208`).
So `MASK` changes in the same commit as `cap_add`, held by the existing
`tests/test_ta_op_modes.py` parity. **Never diverge from it silently** — a mismatch turns the
healthcheck red, which is an unhealthy daemon, which is a deploy rollback. `SYS_ADMIN` is the one
member whose necessity is not yet proven; narrowing it means changing both in one commit
(task 2.7), not dropping it from one side.

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
`launcher.spawn(kind, args) -> Popen-like`. Kinds and argv templates are a **static table in the
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

  The `engine-mcp` kind is where this matters most today: `engine_mcp_http.py:270` builds the
  child's environment as `dict(os.environ)` — the daemon's *whole* environment, not even
  `child_env` — and then adds four `TINYASSETS_ENGINE_*` names plus the port and shared secret
  (271-275). Its allowlist is exactly those six plus the `PATH`/`LANG`/`TZ`/`HOME` basics.

The `multiprocessing` spawn children do not go through the launcher as they stand: that bootstrap
passes a pipe handle and the resource-tracker descriptor through its own protocol, which an
`SCM_RIGHTS`-stdio `execve` does not reproduce. D7 decides each of them by name.

### D4. Volume ownership, and the one rule that makes rollback free

**Invariant: the migration never changes the OWNER of a path an older image reads.** It adds a
group, sets setgid, and tightens other-bits. Only `/data/.broker/**` — which is new in #4299 and
which no older image opens, because an older `start_broker` refuses outright
(`supervisor.py:177-183`) — changes owner to 1002.

That invariant is what answers rollback: an older image running everything as 1001 still owns
every vault file and every store, so it reads and writes them unchanged. **D4's previous
"temporary 1001 read ACL on the vault directory" is deleted** — there is no temporary widening,
and no reverse migration.

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
| workspace roots under `/data/<cc>/` | 1001:1100 | 2770 setgid | `workspace_pool.universe_paths` |
| `/data/.broker/`, `/data/.broker/state/` | **1002:1002** | 0700 | `supervisor.py:52-53`, `process.py:89-90` |
| `/data/.layout.lock` | 1001:1001 | 0666 | `storage_layout.py:63-70` creates it 0o666 for cross-uid `flock` |
| everything else | 1001:1001 | unchanged | — |

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
measured, not guessed. `provider-discovery` (D3) is the exception that proves the rule: it runs
*outside* the jail with the snapshot as its `cwd`, so its access comes from the uid and the group,
not from a bind.

**Authority.** The migration keeps owner 1001 on almost every path, so euid 0 is *not* the owner of
what it re-modes. `chmod`, `setfacl` and the setgid bit on a 1001-owned path therefore need
`CAP_FOWNER`, and traversing an existing `0700` owner directory needs `CAP_DAC_OVERRIDE`;
`CAP_CHOWN` alone covers only the `/data/.broker/**` owner change. All three are in the migration
phase of D2's capability table, and the launcher drops them before it serves — so this authority
exists only while the container holds a single process.

**Traversal safety.** The migration runs when the only process in the container is root, under an
exclusive `flock` on `/data/.layout.lock` — that is the primary mitigation, and it is cheap. It is
not sufficient on its own: the volume is a bind mount, so a *previous* container's compromised
1001 process can have planted links in it. So the traversal:
- walks with directory file descriptors it holds open, using `os.open(..., O_NOFOLLOW|O_DIRECTORY)`
  and `*at()` calls relative to them, so a rename between stat and change cannot redirect it;
- uses `os.lchown` / `fchownat(AT_SYMLINK_NOFOLLOW)`, never `chown`;
- **refuses, loudly, on any symlink inside the traversal set** rather than following it — the same
  rule and the same reason as `provider_jail.py:338-339` ("the command center's `<name>` is a link;
  it cannot be masked");
- **refuses on any regular file with `st_nlink > 1`** in the set: a hardlink means a second name
  exists, possibly outside the set, and changing the inode's group would hand that name the same
  access;
- refuses on anything that is not a directory or a regular file.

**Crash recovery.** Idempotent by construction — it computes the target owner/group/mode per path
and applies only differences, so re-running completes a partial run. Mirroring
`storage_layout.py:10-19`'s existing discipline, it writes `"roles": {"state": "migrating"}` into
`/data/.layout.json` durably before its first change and `"stable"` after its last; a start that
finds `migrating` re-runs from the beginning rather than assuming the volume is consistent. Bounded
by the volume (about 1.8 GB), and backups skip while the lock is held.

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
| 11 | short-lived owner tools: `gh` (`effectors/github_pr.py`), `git`, the `ta-op` canary | direct exec from the daemon | **stay at 1001** |

Rows 7-10 were missing from the first draft of this table. Row 7 is the one that changes the
picture: it launches the provider binary through `create_subprocess_exec` directly, bypassing
`owned_process.py` and therefore the bubblewrap jail, with a credential snapshot as its working
directory. An enumeration that misses it would have left a provider process at the owner's uid,
unjailed, holding credentials — the exact thing this change exists to prevent.

Row 11 is why "move everything off 1001" is not available: those are the owner's own tools, running
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
paths, which `ta-work` covers. Its channel has to change, because `multiprocessing` cannot be
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

### D8. What this change does not isolate

All engine and provider children share uid 1003 across every command center, so a 1003 child for
command center A is not separated *by uid* from command center B's files. That is not a regression
— today they share 1001 — and it is not what the uid split is for. The per-box uid range
200000–299999 (D1) is what closes it by uid, in S4/S5.

**Only some of those children are jailed, and the first draft said all of them were.** For
`provider-cli`, cross-command-center containment is the bubblewrap jail: `provider_jail` binds only
the owning command center's paths, refuses a bind whose source resolves outside it
(`provider_jail.py:169,386`), and masks every hidden root entry except `.runtime` (316-346). But
three of D3's kinds run **outside** any jail:

| Kind | Containment under this change |
|---|---|
| `engine-mcp` | not jailed (`engine_mcp_http.py:277-283` has no `provider_jail` import). Contained by the uid and by D3's allowlist environment, which is a real tightening: today it inherits `dict(os.environ)` (270) |
| `provider-discovery` | not jailed (`native_jsonrpc_discovery.py:130-134`). Uid and group only |
| `tool-jail` | its own jail (`universe_tools.py`), not `provider_jail` |

So for those three the uid *is* the containment, which is an argument for the split rather than
against it — but it is not the jail, and claiming the jail covers them would have been wrong.

## Risks / Trade-offs

- **The launcher is root-adjacent code.** One file, stdlib-only, run `-I -S`, a static kind table,
  no shell, an allowlist environment, a self-verified chain, and a cross-family security refute
  before build. It serves one pid.
- **`/app` becomes read-only and `HOME` moves.** The largest behavioural risk in this change, and
  the one with a named fallback (D2). Any runtime write under `/app` fails loudly rather than
  silently, which is the right direction; task 2.1 enumerates them first.
- **The capability set is wider than one would like, and wider than the first draft said.** Eight,
  because the migration needs DAC authority over paths it deliberately does not own and the
  launcher needs to signal two foreign uids. Mitigated structurally rather than by wishing: the
  migration's three are dropped before the launcher serves, and the whole set is held equal to
  `ta_op.c`'s `MASK` so the two cannot drift. `CAP_SYS_ADMIN`'s necessity is still unproven
  (task 2.7), and narrowing it must change `ta-op` in the same commit.
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
