**founder decision 2026-10-05: fold + build with probes.** D9 folds all seven
round-3 findings; no fourth design review, normal cross-family build review,
no deployment. D10 records the lead's technical decision resolving ACL-mask
access preservation with capability-free two-pass owner deletion and explicit
startup reverse migration before capability drop. The capability ambiguity is
resolved; documentation is not implementation acceptance.

## Why

The credential broker (S6, `broker-streaming-contract`, #4299) authenticates its callers by the
kernel uid on its socket (`SO_PEERCRED`). In production today every role runs as one uid (1001):
the daemon, its engine and provider children, and the workspace workers. A same-uid child can
therefore read the daemon's `owner.json` and act as the owner on the broker's owner channel. So
the broker refuses to start (`BrokerUidSplitRequired`, deviation (c)). The broker is also what
cuts the per-turn memory of a model round from about 29 MiB (one Python worker per proxy) to about
100 KiB (one stream), so this split gates both a security boundary and the platform's memory
headroom.

Measured on prod (2026-10-02, read-only):
- the daemon container runs as `tinyassets` (1001), with `cap_drop: ALL` and
  `no-new-privileges`;
- `/data` and every store in it are owned by that uid;
- no process in the container can change uid today.

## What Changes

- **Mandatory per-owner cells for every engine class.** The lead decision in D8 makes
  cross-user isolation non-negotiable now: extend the existing bubblewrap jail through
  the launcher to every owner-scoped child and descendant. Shared `ta-work` access is
  confined inside that namespace. The provider-only denial option is rejected. D8 lists
  all covered spawn sites and requires actual-process, per-class production-image oracle
  denial of other owners' data, owner.json, vault and owner channel token.

- **A uid per role, enforced by the kernel.** Owner/daemon 1001 (unchanged, it owns `/data`);
  broker 1002; engine and provider children 1003. Three service groups carry the cross-uid access
  the split needs: `ta-work` (1100) for workspaces, `ta-brk` (1101) for the broker socket,
  `ta-vault` (1102) for vault reads. The box-host and per-box ranges are reserved for S4/S5 and
  agreed with `openshell-spike`.
- **A tiny root launcher, not a privileged daemon.** The container starts as root with a
  phase-scoped capability set: the one-time ownership migration needs `CHOWN`, `FOWNER` and
  `DAC_OVERRIDE` because it re-modes paths euid 0 does not own; the launcher needs `SETUID`,
  `SETGID`, `SETPCAP` and `KILL`, and **drops the migration's three before it serves**.
  `deploy/native/ta_op.c` asserts set equality on root entry and the container healthcheck runs
  through it, so its `MASK` changes in the same commit. The launcher starts the broker and the
  daemon as their own uids, then serves exactly one request from the daemon's own pid: "spawn this
  allowlisted child as 1003". The daemon keeps no capability, so it cannot become the broker's uid
  and read the vault.
- **No owner-writable path on a privileged chain.** The entrypoint moves out of `/app` (where the
  image chowns it to 1001) to a root-owned path; the launcher runs `python -I -S` from a root-owned
  file so `PYTHONPATH=/app` and every `site-packages` `.pth` are out of the privileged process;
  `/app` itself becomes root-owned and read-only, with the owner's `HOME` moved to
  `/home/tinyassets`. Each child's environment is built from a per-kind **allowlist** — not from
  `platform_secrets.child_env`, which is a denylist — with that denylist still applied on top.
- **Owner-reachable IPC separated from private broker state.** The broker's own state stays
  1002-only at `/data/.broker/` 0700. The sockets move to the `/run` tmpfs with group `ta-brk`, and
  the launcher — not the owner — starts, restarts and stops the broker. The owner's
  `(socket, generation, token)` is held in process memory and `owner.json` is deleted.
- **Volume permissions follow role authority.** D11 assigns outbound.db and
  .outbound-proxy to broker uid 1002, group ta-brk, with daemon ledger/accounting/
  refresh operations mediated by authenticated broker IPC. Forward and reverse
  migration happen in D10's privileged startup window. This replaces the prior
  rule forbidding owner changes to any file an older image reads. Vault deposits
  remain daemon-written and broker-readable only; workspaces retain ta-work.
  One mode declaration governs startup and runtime creation. The ledger's
  physical parent remains pending because SQLite cannot create journals under
  D4's daemon-owned /data at 0755; see delivery.md. The access inventory names
  raw SQL, account deletion, backup and refresh dependencies as well as callers.
- `start_broker` replaces its refusal with the launcher-mediated start when it observes distinct
  uids. It still refuses when it does not.

## Impact

- **Deploy shape:** `Dockerfile` (users and groups, `/app` ownership, `HOME`, the launcher and the
  relocated entrypoint), `deploy/docker-entrypoint.sh` (install path only — contents unchanged),
  `deploy/compose.yml` (root entry, `cap_add`, `HOME`), and the deploy validator's capability
  assertions.
- **Rollback and deletion must be proven after engine writes.** Access/default ACLs
  for uid 1001 and child umask 007 are required, but explicit 0700 creation/chmod
  masks those ACLs. D10 requires engine 1003 to remove engine-owned entries
  inside the owner's cell through normal launcher spawn, then daemon 1001 to
  remove daemon-owned entries and empty structure, both without capabilities.
  Failures report the path loudly. Rollback is explicitly selected at startup in
  the forward migration's privileged window, before capability drop, with dry-run
  and idempotent recovery. No retained capability or privileged helper is added.
  Known owner-work creation modes remain group-preserving as defense in depth.

- **Code:** `tinyassets/role_launcher` ships as a root-owned file, not an importable module;
  `tinyassets/broker/supervisor.py` (refusal → launcher-mediated start; `owner.json`, `stop()` and
  `read_owner` deleted); `tinyassets/broker/process.py` (the generation is minted by the broker, so
  `lease_verifier` loses its `owner_generation` parameter, and the socket's umask changes);
  `tinyassets/credential_vault.py` (one mode declaration replacing the literals at 187-188, 1783,
  1784-1793, 1808, 1846, 2069 and the two write-path `chmod`s, plus the explicit group on the temp
  file); `tinyassets/storage/outbound_connections.py` (the brokered channel reads the fence from
  the live supervisor, and the legacy proxy worker refuses to spawn while the broker is selected);
  `tinyassets/workspace_worker.py` (its channel becomes an inherited socketpair so it can run as
  1003); `deploy/native/ta_op.c` (`MASK`); and every spawn site that starts an engine or provider
  child goes through the launcher client — `providers/owned_process.py`, `engine_mcp_http.py`,
  `node_sandbox.py`, and the four the first enumeration missed:
  `providers/native_jsonrpc_discovery.py` (reached from `providers/base.py`, historically outside the jail; now using the metadata view),
  `universe_tools.py`, `workspace_provision_process.py`, `workspace_registry_process.py`.
  New gate: `scripts/check_privileged_chain.py`.
- **Dependencies:** lands after #4299 (the broker) and #4267 (`platform_secrets`), amending both.
- **Specs:** new capability `runtime-process-roles`. `credential-vault` gains the vault's
  broker-readable group and the owner-only writer rule.
