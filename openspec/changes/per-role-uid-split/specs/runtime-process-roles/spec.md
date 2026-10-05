<!-- founder decision 2026-10-05: fold + build with probes.
     D10 resolves access with two-pass deletion and startup reverse migration;
     see delivery.md. -->

## ADDED Requirements

### Requirement: Distinct kernel uids per runtime role

The production runtime SHALL run the owner (daemon), the credential broker, and engine/provider child processes as distinct kernel uids: 1001, 1002 and 1003 respectively. The daemon SHALL hold no Linux capability that lets it change uid. Only a root launcher SHALL start other roles, and it SHALL start engine/provider children only from a static allowlist of kinds and argv templates, accepting a request only from the exact pid of the daemon it started. Cross-uid access SHALL be carried by named service groups rather than by widening ownership: `ta-work` (1100) for workspaces, `ta-brk` (1101) for the broker socket, `ta-vault` (1102) for vault reads. No supplementary membership SHALL be written into `/etc/group`; the launcher SHALL set each role's groups at spawn, so that an operator `docker exec` and the container healthcheck keep their single-group identity.

#### Scenario: A child cannot act as the owner
- **WHEN** an engine or provider child (uid 1003) tries to read the broker's state or connect to the broker's owner channel
- **THEN** the read fails with a permission error and the broker refuses the connection as an unmapped uid

#### Scenario: The owner cannot become the broker
- **WHEN** the daemon process (uid 1001) tries to change its uid to the broker's
- **THEN** the kernel refuses, because the daemon holds no `CAP_SETUID`

#### Scenario: A process at the owner's uid that is not the daemon cannot request a spawn
- **WHEN** a process running as uid 1001 that is not the daemon connects to the launcher socket and asks for a child
- **THEN** the launcher refuses it, because the peer pid is not the pid it started the daemon as

#### Scenario: The broker fails closed without the split
- **WHEN** the broker is selected on a host where the roles share one uid
- **THEN** the daemon refuses to start it and says the per-role uid split is required

### Requirement: No owner-writable path lies on a privileged chain

Every executable, interpreter, script and import-search-path entry reached by a process that still holds a capability SHALL be owned by root and SHALL NOT be group- or other-writable; the same SHALL hold for every ancestor directory of each, because write permission on a directory permits renaming any entry in it, and for every node of a symlink resolution — each link and what it resolves to — rather than symlinks being refused outright, since the image's own interpreter is a symlink. The privileged entrypoint and launcher SHALL live outside every tree the image grants to a role uid. The launcher SHALL run with the interpreter's isolated mode so that an inherited `PYTHONPATH` and any `site-packages` path-configuration file cannot enter the privileged process, SHALL drop the capabilities only the one-time ownership migration needs before it begins serving, and SHALL verify its own chain and refuse before it binds a socket. A build-time gate SHALL assert the same properties against the built image, so the runtime refusal is a backstop rather than the only check.

#### Scenario: The owner cannot rewrite what root will run
- **WHEN** the daemon (uid 1001) attempts to write, replace, rename or link over the entrypoint, the launcher, the broker's entry file, any privileged import-path entry, or any ancestor directory of one
- **THEN** the filesystem refuses it

#### Scenario: An inherited import path does not reach the launcher
- **WHEN** the launcher starts with an environment that sets an import search path into a role-writable tree
- **THEN** that path is absent from the privileged process's search path, and the launcher imports nothing from it

#### Scenario: A tampered chain refuses before service
- **WHEN** any element of the privileged chain, or an ancestor directory of one, is not root-owned or is group- or other-writable at launcher start
- **THEN** the launcher refuses loudly and binds no socket

### Requirement: A privileged child's environment is built from an allowlist

The launcher SHALL build each child's environment from a static per-kind allowlist of variable names, and SHALL then apply the platform's child-forbidden denylist on top, so that a name nobody enumerated is absent by default and the denylist's existing exclusions keep holding if an allowlist is ever widened. Each child SHALL be started with no Linux capability in any set, with no-new-privileges set, with its supplementary groups set explicitly, and with every inherited file descriptor closed except the descriptors that kind declares. The launcher's own listening socket SHALL be close-on-exec and SHALL NOT appear in any kind's passed descriptors.

#### Scenario: An unenumerated variable does not reach a child
- **WHEN** the daemon's environment carries a variable that is in neither the kind's allowlist nor the denylist
- **THEN** the child does not receive it

#### Scenario: A child cannot ask the launcher for another child
- **WHEN** a spawned child inspects its inherited descriptors
- **THEN** the launcher's listening socket is not among them

### Requirement: Owner-reachable IPC is separate from private broker state

The broker's private state SHALL live in a directory owned by the broker uid at mode 0700 that no other role opens. Anything the owner must reach SHALL live outside it, on a container-private tmpfs rather than on the data volume, so that no socket path sits in a role-writable directory and no stale socket survives a restart. The broker's socket SHALL be owned by the broker uid with the broker-client group at mode 0660, and its directory SHALL carry the setgid bit, because the broker creates the socket under its own primary group and performs no group change of its own — a socket directory without setgid yields a socket the owner cannot reach. The launcher's socket SHALL be root-owned with the owner's group at mode 0660. The launcher SHALL own the broker's lifecycle — start, readiness, restart and shutdown — because the owner can neither write the broker's directory nor signal a process of another uid, and the launcher's capability set SHALL include the capability to signal a process of another uid, since being its parent does not grant that. The owner SHALL publish no credential-bearing handshake result to disk.

#### Scenario: The owner fences without writing the broker's directory
- **WHEN** the daemon completes the owner's fence barrier
- **THEN** it holds the socket path, generation and token in process memory, and no file inside the broker's state directory was created or modified by the owner

#### Scenario: A restarted broker keeps the owner's pair valid
- **WHEN** the broker exits and the launcher restarts it with the same arguments
- **THEN** it reloads its persisted fence from its own state directory and admits the owner's existing generation and token

#### Scenario: Shutdown does not depend on a cross-uid signal
- **WHEN** the container is asked to stop
- **THEN** the launcher stops the daemon and then the broker, and the daemon never signals the broker

### Requirement: The owner channel requires a factor that is not on disk

Because every process at the owner's uid — including short-lived tools the daemon execs for its own work — is indistinguishable from the daemon by peer credentials, the uid SHALL be necessary but not sufficient to open an owner stream. The second factor SHALL be held only in the daemon's process memory and SHALL NOT be written to any file. The legacy per-grant credential worker SHALL NOT be spawnable while the broker is selected, so that the two credential paths never coexist at the owner's uid. The daemon and the broker SHALL each mark themselves non-dumpable after exec — not before it, because an ordinary exec resets that flag — so that a same-uid process cannot attach and read that factor on a host whose ptrace policy would otherwise permit it. The residual limits of that control SHALL be recorded rather than presented as a boundary.

#### Scenario: A sibling at the owner's uid cannot find the token
- **WHEN** a process running as uid 1001 that is not the daemon searches the data root for the broker's socket, generation and token
- **THEN** it finds no file containing them

#### Scenario: The legacy worker and the broker never coexist
- **WHEN** the broker is selected and a caller reaches the legacy per-grant worker spawn path
- **THEN** the spawn is refused loudly rather than starting a second credential-resolving process at the owner's uid

#### Scenario: A same-uid process cannot attach to the daemon
- **WHEN** a process at uid 1001 other than the daemon tries to attach to the daemon to read its memory, on a host whose ptrace policy does not already forbid it
- **THEN** the kernel refuses, because the daemon marked itself non-dumpable after exec

### Requirement: Volume ownership follows the roles and supports prepared rollback

The ownership migration SHALL NOT change the owning uid of any path an older image reads; it SHALL grant access by adding a service group, setting the setgid bit so new files inherit it, and tightening other-bits. Only the broker's own state directory, which no older image opens, SHALL change owner. The vault file and the materialized credential artifacts SHALL keep the owner uid as their only writer and SHALL become readable by the broker through the vault group, so that the owner's existing atomic sibling-temp-then-replace write keeps working with no privileged step. The vault's group SHALL be set explicitly on the temporary file before the atomic replace, not inherited from its directory, because that directory is the command-center root whose own group belongs to the work group; and because that assignment is a precondition of the write rather than a durability step, its failure SHALL propagate rather than commit a wrongly-grouped vault. Every mode and group these paths take SHALL come from one declaration read by both the migration and every runtime site that creates or re-modes them, so that a later provider launch cannot silently restore single-uid permissions. Child-writable workspaces SHALL be group-owned by the work group with setgid directories, and all other platform state SHALL stay owned by the owner uid. The migration SHALL be idempotent, SHALL run under the exclusive data-layout lock before any role starts, and SHALL hold the capabilities required to re-mode and traverse paths it does not own.

#### Scenario: Re-running the migration changes nothing
- **WHEN** the container restarts on a volume already migrated
- **THEN** the migration makes no ownership or mode changes and the roles start

#### Scenario: Rolling back preserves access after engine writes
- **WHEN** explicit opt-in startup reverse migration has completed on a migrated copy after uid 1003 has created files and directories, including explicit 0600/0700 modes and later chmod, and an older image starts as uid 1001 without supplementary work groups
- **THEN** it reads, writes and deletes the required workspace contents without losing user data
- **AND** merely retaining the uid of pre-existing files is not evidence that rollback succeeds

#### Scenario: A deposit keeps the broker's read access and does not widen it
- **WHEN** the owner writes a new credential through its sibling-temp-and-replace path
- **THEN** the replacement file is group-owned by the vault group, set on the temporary file before the replace, and the broker can still read it without any ownership change
- **AND** it is not group-owned by the work group, so no engine or provider child can read it

#### Scenario: A provider launch does not restore single-uid permissions
- **WHEN** a provider launch runs the code that creates or re-modes the artifact directory, the platform runtime directory, or a launch credential snapshot
- **THEN** those paths keep the modes and groups the migration set, because both read the same declaration

#### Scenario: The broker cannot write the vault
- **WHEN** the broker attempts to modify the vault file or a materialized credential artifact
- **THEN** the write fails with a permission error

#### Scenario: Workspace symlinks survive migration without target traversal
- **WHEN** migration encounters symlinks in ta-work trees, including .venv/bin/python and node_modules/.bin
- **THEN** it skips them without following or re-moding their targets and re-modes the actual workspace contents including executable files
- **AND** privileged, vault and broker sets retain link refusal; work-tree hardlinks are mutated only after all aliases are proven to lie within the same owner's work set, otherwise completion is blocked without changing that inode

#### Scenario: An interrupted migration completes rather than half-applying
- **WHEN** the migration is killed part-way through
- **THEN** the data-layout marker records that the role migration is in progress, and the next start re-runs it to completion before any role starts

### Requirement: Every owner-scoped engine process is isolated from other owners

The engine-MCP cell SHALL contain only a pinned thin proxy; canonical handlers and multi-tenant stores SHALL remain in the daemon control plane. The receiver SHALL bind the channel's owner/execution from trusted admission and SHALL refuse attempts to widen that scope through payload fields. `node_bid` SHALL remain a fixed control-plane operation; its shared repository SHALL NOT enter any cell. Shared root stores, database sidecars and replacements SHALL have other permissions removed at migration and runtime creation.

Every engine process executing owner-scoped work SHALL enter an owner-bound bubblewrap mount/PID/IPC/network boundary with an empty network namespace before executing application code, using the launcher and existing provider-jail validated views. This includes every D8 inventory class and every descendant: provider CLI, provider discovery, engine MCP, node sandbox, tool jail, workspace provision/registry/worker, preview, image decoder, auth probe, local box execution and owner-scoped utilities. The common uid 1003 and work group SHALL NOT be claimed as cross-owner isolation. Work-group rights SHALL be usable by engine payloads only inside that owner's namespace. No engine payload SHALL run unjailed or be reused across owners; missing, mismatched or unsupported owner scope SHALL fail closed.

Views SHALL expose at most the admitted owner's command-center tree/workspace subset, approved immutable runtime dependencies, exact owner-scoped relay sockets and private scratch. They SHALL NOT expose shared data/HOME/tmp/run roots, host procfs, sibling runtime snapshots, foreign directory descriptors, the vault, materialized credentials, owner.json or the owner channel token. Discovery SHALL retain its narrower exact-snapshot metadata view. The launcher SHALL validate and pin sources and reject cross-owner aliases and replacement races. Owner identity SHALL derive from the authenticated daemon's admitted execution and trusted root mapping, not child-controlled argv/env/cwd. Every class SHALL have private networking, with no host network, loopback or abstract-socket access. Namespace-local procfs and scoped IPC/egress SHALL prevent bypass through sibling processes or trusted receivers. Descendants and nested jails SHALL preserve or narrow this boundary. A class unable to use the boundary SHALL be refused, never launched with only uid/group separation.

#### Scenario: Every actual engine class is denied another owner's state
- **WHEN** the Linux oracle using the production image launches an actual owner-A engine process through each class and spawn site in D8 and attempts to read or write owner B's data, workspace, legacy owner.json fixture, vault or materialized credentials
- **THEN** every process runs with the production engine identity and owner boundary and obtains none of B's sentinel bytes or write authority, including via aliases, inherited descriptors, replaced bind sources or paths created after launch
- **AND** every class successfully performs its legitimate owner-A operation; a generic uid-switch probe or skipped class is not acceptance

#### Scenario: The owner channel token stays outside every engine cell
- **WHEN** each actual engine class attempts to obtain the owner token via files, inherited environment/descriptors, sibling procfs or ptrace, or to use the owner channel through IPC
- **THEN** all attempts fail, the real owner.json is removed and not recreated, and the daemon/broker retain the token only in protected memory

#### Scenario: Scope cannot be widened to preserve compatibility
- **WHEN** a spawn omits or mismatches the admitted owner, reuses another owner's engine server/worker, requests an out-of-scope mount or cannot initialize its jail or scoped transport
- **THEN** the launcher refuses before executing the payload, without an unjailed fallback

#### Scenario: A descendant cannot escape the work group's namespace restriction
- **WHEN** a preview, git command, provisioning command, nested tool/node jail or other helper runs for owner A
- **THEN** it inherits or enters A's boundary and cannot use shared ta-work membership, host procfs, a directory fd or a shared service to access owner B

#### Scenario: The vault is not reachable from a child at all
- **WHEN** any engine class looks for the vault or materialized artifact directory of its own command center
- **THEN** both are absent or masked, and its kernel identity also lacks access to either outside the jail

#### Scenario: Relay access works only for the admitted owner
- **WHEN** each actual engine class attempts to connect to owner B's engine-MCP port, relay socket or host abstract sockets
- **THEN** the attempts fail, while its legitimate owner-A relay connection works at uid 1003 using the exact socket bind and D4's sidecar directory/socket modes
- **AND** sidecar parent directories are never exposed inside the cell

### Requirement: Each class has a named seccomp policy and safe daemon readers

Each class SHALL use the named D9/F2 profile: cell-deny by default, cell-links only for symlink-requiring git/venv/npm operations, and cell-nested only for demonstrated nested sandboxes. The production-image oracle SHALL exercise every class and each cell-writable-path/daemon-reader pair, including inspect, preview, file reads, git_bridge and staging/publish. Seccomp denial of planting SHALL NOT replace a daemon-reader probe against preplanted fixtures.

#### Scenario: A cell-planted object never returns another owner's bytes
- **WHEN** A plants, or the adversarial fixture preplants, a symlink, FIFO or hardlink targeting B at each cell-writable path, then invokes each actual daemon reader/server of that path
- **THEN** no B bytes or B write authority are returned and no FIFO hangs the reader
- **AND** A's ordinary operation succeeds under the class's declared seccomp profile

### Requirement: Mount descriptors close before every payload

Every class SHALL execute a mandatory close-after-mount bootstrap before payload code, closing bind-source, seccomp and namespace descriptors while retaining only explicitly scoped stdio/IPC. Nested boundaries SHALL repeat this discipline.

#### Scenario: An owner directory descriptor cannot escape the mount view
- **WHEN** each actual payload lists /proc/self/fd and tries openat(fd, "..") and relative traversal through each retained directory descriptor
- **THEN** it cannot reach host ancestors, another owner's files, privileged state or a writable source behind a read-only bind

### Requirement: Workspace access uses ACLs plus capability-free two-pass deletion

Ta-work directories SHALL have access u:1001:rwx and default d:u:1001:rwx ACLs with effective masks; files SHALL have appropriate owner-daemon access while retaining executable bits. Children SHALL start with umask 007. Work trees SHALL require ACL support. Default ACL presence alone SHALL NOT be accepted as proof after explicit restrictive creation or chmod. Known explicit 0700/chmod sites creating owner-work content SHALL use group-preserving 0770/2770 modes consistent with umask 007, without widening any path outside the classified owner's work tree.

Per D10, owner-tree deletion/reset SHALL use two passes without capabilities, including workspace pool removal, scoped_reset and account deletion. Pass 1 SHALL run as engine uid 1003 inside the admitted owner's cell through normal launcher spawn, removing engine-owned entries with pinned no-follow openat traversal confined to that view. Pass 2 SHALL run as daemon uid 1001 and remove daemon-owned entries and the now-empty structure. A pass unable to remove an entry SHALL fail loudly with its path, never silently report partial deletion as success. Launcher peer validation and owner confinement SHALL remain unchanged. The launcher SHALL retire CHOWN/FOWNER/DAC_OVERRIDE from all five sets before service; no retained-capability helper or runtime root maintenance operation SHALL exist.

Rollback SHALL run only at container startup before capability drop in the same privileged window and code path as forward migration, selected by an explicit env/flag opt-in. It SHALL hold the exclusive layout lock before any role starts, be idempotent and crash-recoverable, and support a non-mutating dry-run. It SHALL restore engine-created content to uid-1001-readable, writable and deletable ownership/modes, SHALL preserve no-follow and hardlink alias protections, and SHALL never delete user data. Rollback startup SHALL exit before normal service or forward remigration. The tested rollback runbook SHALL record the actual invocation.

#### Scenario: Engine-owned restrictive paths remain deletable
- **WHEN** an engine creates directories/files with 0700/0600 or applies those modes afterward
- **THEN** actual daemon deletion/reset APIs complete through the two unprivileged passes without changing data outside the requested tree
- **AND** each pass's identity, namespace and zero capabilities are proven

#### Scenario: Pass 1 cannot reach another owner or follow a symlink escape
- **WHEN** an A-scoped deletion cell targets B's tree or attempts traversal through an escaping symlink
- **THEN** B and outside targets retain their contents and metadata
- **AND** the existing exact daemon uid-and-pid check protects the normal cell spawn

#### Scenario: Removal failure is explicit
- **WHEN** either pass cannot remove an entry
- **THEN** the operation fails loudly with the path and does not report partial deletion as success

#### Scenario: Reverse migration prepares restrictive engine content for an old image
- **WHEN** explicitly selected startup rollback dry-run, apply, interrupted resume and repeat run on a disposable migrated copy with engine-created 0600/0700 content and later chmod
- **THEN** dry-run changes no contents, ownership, modes, ACLs or layout markers, resume completes and repeat makes no changes
- **AND** the actual old image running as uid 1001 without supplementary work groups reads, writes and deletes the restored content successfully

#### Scenario: Capability retirement survives deletion and rollback changes
- **WHEN** the launcher begins normal service after migration
- **THEN** CHOWN/FOWNER/DAC_OVERRIDE are absent from all five capability sets and the existing capability-drop/refusal probe passes

#### Scenario: Migration dry-run is non-mutating
- **WHEN** migration dry-run inventories a copy containing workspaces, venvs, node_modules, shared stores and adversarial links
- **THEN** contents, ownership, modes, ACLs and layout markers remain unchanged and the report identifies planned changes and unresolved aliases

### Requirement: Production confinement requires no host SYS_ADMIN

Compose cap_add and ta_op.c MASK SHALL exclude CAP_SYS_ADMIN and retain exact parity. The entry set SHALL contain only CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, SETPCAP and KILL; the launcher SHALL retire migration capabilities before serving.

#### Scenario: Production namespaces and healthcheck work without SYS_ADMIN
- **WHEN** the production-image oracle runs with compose security options and the declared capability set
- **THEN** unprivileged bubblewrap, role/capability readbacks and the ta-op healthcheck succeed without SYS_ADMIN

### Requirement: Git trust is scoped to each owner cell

Every git-using cell SHALL receive protected per-cell safe.directory configuration for exact admitted repo paths. Wildcard and shared host-global trust SHALL NOT be used.

#### Scenario: Legitimate git works while other-owner access fails
- **WHEN** every git-using class runs its real owner-A git operation including checkout and workspace mutation
- **THEN** the operation succeeds despite mapped ownership and the same process cannot access B's repository

### Requirement: All refute findings and confirmed controls have executable evidence

The production-image Linux oracle SHALL implement D9's F1-F7 and C1-C6 matrix using every actual class/site and compose security options, recording image digest, command, identity, namespaces and pass/fail results. Skips, Windows-only checks and generic uid substitutes SHALL NOT count as passes. Broker launcher startup and stream, healthcheck, migration dry-run, idempotence, interrupted resume, rollback and deletion SHALL be proven separately. No deployment is authorized by this change's current build instruction.

#### Scenario: Confirmed controls remain true after integration
- **WHEN** the actual production image runs the complete refute matrix
- **THEN** namespace availability, spawn coverage, private network/IPC/procfs/tmp, explicit vault group, exact launcher peer validation and absence of unjailed fallbacks each pass their named probes
