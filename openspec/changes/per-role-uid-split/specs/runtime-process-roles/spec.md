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

### Requirement: Volume ownership follows the roles and never breaks an older image

The ownership migration SHALL NOT change the owning uid of any path an older image reads; it SHALL grant access by adding a service group, setting the setgid bit so new files inherit it, and tightening other-bits. Only the broker's own state directory, which no older image opens, SHALL change owner. The vault file and the materialized credential artifacts SHALL keep the owner uid as their only writer and SHALL become readable by the broker through the vault group, so that the owner's existing atomic sibling-temp-then-replace write keeps working with no privileged step. The vault's group SHALL be set explicitly on the temporary file before the atomic replace, not inherited from its directory, because that directory is the command-center root whose own group belongs to the work group; and because that assignment is a precondition of the write rather than a durability step, its failure SHALL propagate rather than commit a wrongly-grouped vault. Every mode and group these paths take SHALL come from one declaration read by both the migration and every runtime site that creates or re-modes them, so that a later provider launch cannot silently restore single-uid permissions. Child-writable workspaces SHALL be group-owned by the work group with setgid directories, and all other platform state SHALL stay owned by the owner uid. The migration SHALL be idempotent, SHALL run under the exclusive data-layout lock before any role starts, and SHALL hold the capabilities required to re-mode and traverse paths it does not own.

#### Scenario: Re-running the migration changes nothing
- **WHEN** the container restarts on a volume already migrated
- **THEN** the migration makes no ownership or mode changes and the roles start

#### Scenario: Rolling back to a single-uid image needs no reverse migration
- **WHEN** an older image that runs every role as the owner uid starts on a migrated volume
- **THEN** it reads and writes every store it used before, because no path it reads changed owner

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

#### Scenario: A planted link is refused, not followed
- **WHEN** the migration's traversal meets a symlink inside its set, or a regular file with more than one hard link
- **THEN** it refuses loudly and changes nothing on that path, rather than following the link or changing an inode a second name also reaches

#### Scenario: An interrupted migration completes rather than half-applying
- **WHEN** the migration is killed part-way through
- **THEN** the data-layout marker records that the role migration is in progress, and the next start re-runs it to completion before any role starts

### Requirement: Shared-uid children are not separated by uid, and not all of them are jailed

All engine and provider children SHALL share one uid across every command center, so cross-command-center separation for them SHALL NOT be claimed from the uid; the reserved per-box uid range SHALL be what closes it by uid. For the jailed provider child, containment SHALL remain the bubblewrap jail, which binds only the owning command center's paths, refuses a bind resolving outside it, and masks every hidden platform entry. The child classes that run outside any such jail — the engine MCP child, native provider discovery, and the agent's own tool jail — SHALL be enumerated as such, and for them the uid and the allowlisted environment SHALL be stated as the whole of the containment rather than the jail being claimed on their behalf.

#### Scenario: One child uid, jail-enforced separation where a jail exists
- **WHEN** a jailed provider child for one command center runs
- **THEN** its uid is the same as every other command center's provider child, and the paths it can reach are limited by its jail's binds and masks

#### Scenario: An unjailed child class is named, not assumed covered
- **WHEN** the engine MCP child or native provider discovery runs
- **THEN** it runs at the engine/provider uid outside the provider jail, with an environment built from its kind's allowlist rather than inherited whole, and the design records that the jail does not contain it

#### Scenario: The vault is not reachable from a child at all
- **WHEN** a provider child looks for the vault file or the materialized artifact directory of its own command center inside its jail
- **THEN** both are masked, and outside the jail the child's uid has no access to either
