<!-- founder decision 2026-10-05: fold + build with probes.
     D10 resolves access with two-pass deletion and startup reverse migration;
     see delivery.md. -->

## ADDED Requirements

### Requirement: TOOL files recover under the admitted owner identity

The daemon SHALL prepare fixed TOOL directories before accounting admission and
recover owner modes and persistent brain publication after payload reaping in a
fixed, strict owner cell. It SHALL retain queueing and accounting settlement.
The maintenance cell SHALL accept no caller path, executable or credential and
SHALL refuse required directory aliases. It SHALL not read or mutate foreign
inodes, follow links, or remode multiply-linked files. Brain publication SHALL
be bounded, descriptor-sourced, atomic and no-replace, preserving source bytes.

#### Scenario: Restrictive TOOL modes do not strand later accounting
- **WHEN** an owner tool makes its own file or directory mode 000
- **THEN** trusted owner maintenance restores its inherited daemon ACL mask
- **AND** settlement runs even on a failed maintenance receipt
- **AND** a foreign alias remains unchanged and unread

#### Scenario: A tool creates a previously absent brain file
- **WHEN** the admitted main agent writes MEMORY.md in its persistent workspace
- **THEN** trusted maintenance publishes the exact bytes at the canonical root
- **AND** an existing canonical name is never replaced
- **AND** a secondary agent cannot publish the main agent identity.md


### Requirement: Provider metadata discovery uses a dedicated owner cell

Selected native model discovery SHALL run only in the fixed provider-discovery
class with cell-deny, the admitted principal/center and broker-resolved
dedicated UID/GID. The only owner input SHALL be the exact sealed daemon-owned
launch snapshot, pinned by descriptor under the admitted center's
`.runtime/provider-launch-credentials/<name>` and mounted read-only at a fixed
cell path. Only the shipped CLI install trees and an exact admitted egress
socket MAY additionally enter. The privileged request SHALL carry only static
kind, principal, center and booleans plus descriptors; argv/env SHALL arrive
as one bounded config line after confinement and descriptor closure, naming a
shipped executable with no host data path. Existing metadata byte/page/model
bounds and timeout SHALL hold. Teardown SHALL revoke through the lifetime
channel, never a local PID signal, and the authenticated receipt SHALL be
required. A selected broker without scope or bounded client SHALL refuse with
no daemon subprocess fallback.

The immutable snapshot SHALL be copied only after confinement to disposable
owner-private scratch, bounded to 128 entries, 8 MiB and depth 8, rejecting
symlinks, non-regular files and multiple links. No scratch state SHALL be
promoted to daemon custody. Environment forwarding SHALL admit only bounded
display settings and in-cell paths, excluding ambient tokens and loader
settings. One receipt reader SHALL own cleanup across cancellation; revocation
SHALL use write-side EOF without a queued cancellation packet.

#### Scenario: Metadata discovery cannot reach foreign or host state
- **WHEN** Alice's daemon lists models from her sealed launch snapshot
- **THEN** the CLI runs in her dedicated strict cell with only that snapshot read-only
- **AND** a foreign-center, center-root, nested or non-daemon snapshot is refused by the mapper
- **AND** a foreign, other-daemon or non-socket egress descriptor is refused
- **AND** an inherited descriptor, nested user namespace or missing receipt fails the catalogue

### Requirement: Video parser subprocesses use data-only owner cells

Selected video ingestion SHALL admit the current principal and command center
through the bounded launcher and broker identity. The fixed ingestion-video
entry SHALL run installed ffprobe/ffmpeg with cell-deny, dedicated owner UID/GID,
private scratch/namespaces, zero capabilities and closed bootstrap descriptors.
Input SHALL be bounded verbatim bytes at a fixed scratch filename, with fixed
process/resource/deadline limits; no owner filesystem, credential or network
mount SHALL enter this class. Only bounded duration/frame bytes SHALL return.
The caller SHALL supply an explicit owner-scoped frame-description callback.
Selected failures SHALL propagate without a daemon subprocess or platform-model
fallback. Missing scope or launcher SHALL refuse.

#### Scenario: Actual video parsing preserves the owner boundary
- **WHEN** Alice and Bob extract video frames and descriptions in selected mode
- **THEN** actual installed ffprobe/ffmpeg run inside their dedicated strict cells
- **AND** foreign-center admission and foreign-path playlists fail with zero foreign bytes
- **AND** a caller filename cannot choose a scratch path or executable option
- **AND** ordinary extraction succeeds after a refused request

### Requirement: Tool relay sockets are exact owner-scoped inode capabilities

The tool class SHALL admit only the existing egress relay and per-invocation
ta socket under its admitted center's protected sidecar. The daemon SHALL pin
socket inodes without following links and grant only the dedicated owner UID
socket access and parent traverse, without listing or parent write access.
The mapper SHALL validate fixed presence flags, descriptor counts, protected
center paths, socket type, link count and protected service ownership. Cells
SHALL mount only exact sockets, verify their inode identities, and close all
source descriptors before application code. Existing destination restrictions,
callback authority and invocation revocation SHALL remain unchanged.

#### Scenario: Actual bash uses scoped capabilities and public egress
- **WHEN** Alice and Bob execute bash and ta through their owner cells
- **THEN** the existing proxy carries permitted HTTP and ta dispatch keeps each invocation's owner context
- **AND** direct networking and metadata-address proxy requests fail
- **AND** application and mapper refuse foreign-center sockets and the mapper refuses a revoked invocation's held socket
- **AND** subsequent ordinary owner tools still work after each refusal

### Requirement: Staged tool execution retains daemon accounting

The staged offline tool-jail class SHALL use its admitted owner's dedicated
UID/GID, a fixed cell-nested outer profile and the existing strict inner tool
jail. It SHALL pin exclusive owner content descriptors without mounting the
command-center root, protected metadata or parent sidecar directories. Source
descriptors SHALL close before application imports and inner descriptors SHALL
close before executing a tool. Queue slots, storage reservations, budget polls
and settlement SHALL remain in the daemon. Missing owner scope or launcher,
unprepared directories and unadmitted relay sockets SHALL refuse.

#### Scenario: Actual offline tools and bounded image transport
- **WHEN** Alice and Bob use file tools in prepared owner centers
- **THEN** read, write, edit, image reads and bounded stdin execute inside their owner cells
- **AND** existing timeout, output and disk-floor behavior remains enforced
- **AND** foreign aliases, host descriptors and inner nested-userns requests fail
- **AND** full tool-class acceptance remains pending until relay sockets, preparation and persistent brain-file promotion are verified

### Requirement: Code-node execution retains its inner jail inside the owner cell

When the broker role split is selected, code-node execution SHALL enter the
bounded owner launcher with the admitted command center and broker-resolved
dedicated UID/GID. The fixed node-sandbox class SHALL retain D9's cell-nested
profile and the existing inner sandbox. Its optional workspace SHALL be a pinned
directory with exact owner UID/GID beneath that center; source descriptors SHALL
close before application code. Missing scope or launcher SHALL refuse without
daemon subprocess fallback. Callback actions SHALL retain the daemon's existing
per-run authorization, without exposing broker credentials to either jail.

#### Scenario: Actual owner node execution and cancellation
- **WHEN** Alice and Bob execute code nodes with data, scoped action callbacks and their own workspaces
- **THEN** actual nested execution and workspace git/venv operations succeed under their dedicated identities
- **AND** foreign workspaces, planted aliases and inherited host descriptors are denied
- **AND** cancellation reaps the cell even while a daemon callback is blocked, and a subsequent node still succeeds

### Requirement: Independent bounded owner-cell lifetimes

The bounded launcher SHALL support independent bidirectional data streams and
per-launch lifetime channels for admitted static engine classes. Each START
SHALL authenticate the daemon and bind its principal and center to the existing
broker identity mapping. The mapper SHALL retain process supervision, fixed
class deadlines and reaping. Lifetime-channel data or EOF SHALL revoke only
that launch. No caller-selected executable, numeric identity, profile or PID
SHALL be accepted. Fork descendants SHALL close inherited client handles.

#### Scenario: One blocked owner cannot hold the control channel until exit
- **WHEN** Alice's admitted decoder waits for input and Bob starts his admitted decoder
- **THEN** Bob's real decode completes under Bob's dedicated UID/GID before Alice exits
- **AND** Alice's cancellation produces an authenticated receipt only after reaping
- **AND** legacy blocking spawn and STOP refuse while independent cells remain active

#### Scenario: Losing a lifetime handle revokes the child
- **WHEN** a caller closes its lifetime channel or its fixed class deadline expires
- **THEN** the mapper kills and reaps that cell without additional privileges
- **AND** no cell can choose a foreign owner scope or forge a completion as daemon UID

### Requirement: Preview keeps Chromium sandboxed inside its owner cell

The ui-preview class SHALL use the fixed cell-nested profile under founder D73;
other D9 class assignments SHALL remain unchanged. Preview SHALL receive only
the admitted owner's bounded UI and asset bytes and SHALL have no shared store,
owner tree, credential or broker state mount. Chromium's sandbox SHALL remain
enabled. Selected role mode SHALL refuse missing bounded launcher admission.

#### Scenario: Actual preview renders with two isolation layers
- **WHEN** each dedicated owner launches an actual preview through the launcher
- **THEN** Chromium renders that owner's assets with no --no-sandbox argument
- **AND** foreign scope, descriptor, filesystem, IPC and network probes deny
- **AND** strict-profile classes continue to deny nested user namespaces

### Requirement: Bounded launcher authenticates every private-channel packet

Under D62/D68, the launcher SHALL retain only SETUID/SETGID within the fixed
`0 300000 100000` owner user namespace, with zero bounding, ambient and
inheritable capabilities. Host 300000 SHALL remain reserved for the launcher.
The inherited private seqpacket channel SHALL authenticate kernel credentials
for the exact live daemon PID on every packet, including empty messages and
shutdown. A startup-pinned pidfd SHALL prevent acceptance after daemon death.
Numeric identities, executable paths, profiles and environment SHALL NOT be
request fields. Identities SHALL come from broker-resolved owner admissions.

#### Scenario: A descendant inherits a launcher endpoint
- **WHEN** a non-daemon descendant sends spawn, empty-with-descriptor, or STOP
  packets on the inherited pair
- **THEN** the launcher refuses each packet and closes received descriptors
- **AND** subsequent authenticated daemon operations still succeed

#### Scenario: A decoder runs under a dedicated identity
- **WHEN** two admitted owners launch actual image decoders through the bounded launcher
- **THEN** each decoder runs under its own mapped UID/GID with zero capabilities,
  no supplementary groups, private namespaces, only declared stdio and cell-deny
- **AND** actual PNG decoding succeeds while listed foreign/host data paths,
  host network and abstract sockets remain inaccessible

#### Scenario: The daemon client preserves request boundaries and authority
- **WHEN** concurrent admitted owners use the startup-installed client
- **THEN** requests and replies are serialized, each reply has exact launcher
  PID and reserved-mapper UID/GID kernel credentials, and returned identity
  matches the requesting owner's broker-resolved identity
- **AND** a fork child loses the channel and cannot reuse the client
- **AND** a complete authenticated refusal fails only that request, while a
  transport, authentication or framing failure closes the client without fallback

### Requirement: Durable dedicated owner UID and GID (founder D60)

D60 SHALL supersede historical shared engine UID/GID statements in this delta.
The broker SHALL durably allocate each owner a dedicated UID AND GID from the
reserved range. Identities SHALL NOT be reused while files bearing them exist;
permanent reservation is permitted. Each cell SHALL run as its admitted owner's
identity without another owner's groups. Daemon, inspect and broker readers
SHALL validate both UID and GID on the open descriptor, plus no-follow and
link-count checks. Migration SHALL support dry-run, repeat, reverse and crash
recovery and SHALL NOT overwrite identified foreign inode provenance. The
launcher SHALL retain only bounded identity mapping authority, with all other
privileges retired. D59's relabel/copy acceptance SHALL return zero foreign bytes.

#### Scenario: Foreign inode provenance survives retired names and engine mutation
- **WHEN** a foreign inode is preplanted into an owner's tree and its original
  name is removed, including an inode written by another owner's engine
- **THEN** neither the cell nor its daemon-side readers return its foreign bytes
- **AND** changing its group or copying its bytes to a fresh own-group inode
  cannot bypass that boundary

#### Scenario: Repeated and reversed owner identity migration retains data
- **WHEN** owner-group migration runs dry-run, apply, repeat, interrupted resume
  and reverse against a disposable copy
- **THEN** owner identities remain unambiguous, no user bytes are deleted, and
  reverse migration restores actual old-image access before capability drop

#### Scenario: Founder D61 classifies legacy reachability before assignment
- **WHEN** the quiescent legacy inventory finds all names of an inode in one
  owner tree, with no unresolved names or foreign dedicated identity
- **THEN** that inode belongs to the reachable owner for initial migration,
  including a sole surviving name; these are allowed legacy reads, not foreign
- **AND** an inode with names across owner trees is preserved in platform-only
  quarantine, alarmed, and assigned to neither owner
- **AND** the production preflight reads only metadata, never follows symlinks
  or modifies data, and reports special/foreign entries and incomplete coverage

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

The ownership migration SHALL preserve existing owning uids outside the broker egress set. Per D11, the outbound ledger and proxy state SHALL transfer to broker uid 1002 and group ta-brk during privileged startup, and reverse migration SHALL restore old-image access before an old image starts. Other classified paths SHALL gain service-group access and setgid inheritance without widening other-bits. The vault file and the materialized credential artifacts SHALL keep the owner uid as their only writer and SHALL become readable by the broker through the vault group, so that the owner's existing atomic sibling-temp-then-replace write keeps working with no privileged step. The vault's group SHALL be set explicitly on the temporary file before the atomic replace, not inherited from its directory, because that directory is the command-center root whose own group belongs to the work group; and because that assignment is a precondition of the write rather than a durability step, its failure SHALL propagate rather than commit a wrongly-grouped vault. Every mode and group these paths take SHALL come from one declaration read by both the migration and every runtime site that creates or re-modes them, so that a later provider launch cannot silently restore single-uid permissions. Child-writable workspaces SHALL be group-owned by the work group with setgid directories, and remaining platform state outside the broker egress set SHALL stay owned by the owner uid. The migration SHALL be idempotent, SHALL run under the exclusive data-layout lock before any role starts, and SHALL hold the capabilities required to re-mode and traverse paths it does not own.

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


### Requirement: The broker owns egress persistence and mediates daemon access

The broker SHALL own outbound.db, its SQLite sidecars and .outbound-proxy runtime
state as uid 1002, group ta-brk. Private files SHALL be 0600 and private directories
2700; authenticated daemon IPC SHALL retain D6's separate socket modes. The daemon
and every engine class SHALL NOT directly open the private egress set. Daemon
ledger queries/mutations, accounting reads and refresh triggers SHALL use the
broker's authenticated daemon IPC. Engines SHALL reach egress only through their
admitted cell's scoped proxy. Unroutable access SHALL fail loudly without local
file, raw-SQL RPC or legacy-worker fallback. The file:line inventory in
broker-access-inventory.md SHALL be reconciled with implementation, including raw
SQL, account deletion, injected ledger clients and backup consumers.

Forward and reverse ownership migration SHALL use D10's startup window, exclusive
layout lock, non-mutating dry-run, idempotence, crash recovery and no-follow/alias
protections. The physical ledger parent SHALL support broker SQLite journal
creation without granting write access to the whole data root. D12 fixes the ledger at /data/.broker/outbound.db and proxy runtime at
/data/.broker/.outbound-proxy, with uid 1002, gid 1101 and parent mode 2700.
Migration SHALL checkpoint WAL before same-filesystem rename, fsync the files
and both parent directories, refuse conflicting copies without overwriting,
and resume interrupted movement idempotently. Dry-run SHALL NOT checkpoint.
Reverse migration SHALL restore the original root paths before old-image startup.
No runtime implementation or probe completion is asserted by this requirement.

#### Scenario: The broker can create and transact in its private egress set
- **WHEN** the actual capability-free broker opens an existing ledger, creates a fresh ledger, upgrades its schema and creates per-grant proxy state
- **THEN** all operations succeed, including durable SQLite transactions and journal lifecycle

#### Scenario: Every other role is denied direct private egress access
- **WHEN** the daemon with its real groups and each actual engine class attempts direct access to the ledger, SQLite sidecars or private proxy directory
- **THEN** access is denied, including through procfs, inherited descriptors and aliases
- **AND** daemon accounting via authenticated IPC and legitimate per-cell egress still work

#### Scenario: Accounting and refresh retain their authority checks
- **WHEN** the daemon requests accounting, a ledger operation or refresh through IPC
- **THEN** the broker validates peer role, live fence and admitted principal/scope, and rejects forged or stale authority
- **AND** refresh preserves admission-before-spend and durable rotation without granting the broker vault write permission

#### Scenario: Reverse migration restores old-image ledger access
- **WHEN** startup reverse migration runs dry-run, apply, interrupted resume and repeat against a disposable migrated copy
- **THEN** dry-run changes nothing, resume completes, repeat is a no-op and the actual old uid-1001 image reads and writes the restored ledger with working journals
- **AND** retained ledger and proxy data is not deleted
