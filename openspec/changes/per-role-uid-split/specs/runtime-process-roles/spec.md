## ADDED Requirements

### Requirement: Distinct kernel identities per role and per owner
The production container SHALL run the daemon as uid 1001 with no capabilities and the broker
as uid 1002. Every owner-scoped child process SHALL run as its owner's dedicated UID and GID,
inside that owner's cell. Each principal SHALL receive a permanent UID/GID from 300001..399999,
reserved append-only in the broker's `owner-identities.db`, and never reused. Only the bounded
mapper SHALL switch to an owner identity, inside a user namespace mapped `0 300000 100000`.
There SHALL be no configuration under which these roles share a uid.

#### Scenario: Roles are distinct after startup
- **WHEN** the service has started and served one owner turn
- **THEN** `ps` shows the daemon at 1001, the broker at 1002 and the owner's child at its
  dedicated UID
- **AND** every one of those processes reads back an empty capability set

#### Scenario: No switch restores a shared uid
- **WHEN** any environment variable or configuration that formerly selected the legacy path
  is set
- **THEN** startup and every spawn site behave identically to it being unset

### Requirement: Startup is a capability-retiring bootstrap that never migrates
PID1 SHALL fork the broker and the bounded mapper, then retire to the daemon identity before
serving. It SHALL hold only the capabilities that bootstrap needs, and `ta_op.c` SHALL assert
set equality with that set. Startup SHALL refuse to serve unless `/data/.layout.json` records
the `owner-split` layout. It SHALL NOT migrate, reconcile a journal or check a principal set.
PID1 SHALL reap adopted orphan zombies after a grace period, never the broker or the mapper.

#### Scenario: Fresh volume initializes directly
- **WHEN** the image starts on an empty data directory with the image's label (1001:1001,
  0755, no ACLs), with no entries at all
- **THEN** retired daemon and broker children create the platform layout and empty broker
  identity ledger under their own identities, then publish the owner-split marker last
- **AND** startup continues through the normal isolation and runtime-admission checks without
  migration authority; an interrupted initialization is ambiguous and refuses on restart

#### Scenario: Unmigrated volume refuses
- **WHEN** the cutover image starts on a nonempty volume without the `owner-split` marker,
  including any center, `.agent-sessions`, broker ledger, layout marker or unknown entry
- **THEN** startup exits 78 with an actionable `run ta-migrate` reason and serves nothing

### Requirement: No owner-writable path lies on a privileged chain
Every file that root executes or imports during bootstrap SHALL be root-owned and outside
`/app` and `/data`. Root SHALL run Python with `-I -S`. `/app` SHALL be root-owned and
read-only, and the owner's HOME SHALL be `/home/tinyassets`. A child's environment SHALL be
built from a per-kind allowlist, with the platform secret denylist applied on top.

#### Scenario: Chain check runs on every image build
- **WHEN** the image is built for a non-draft PR
- **THEN** `scripts/check_privileged_chain.py` passes or the build fails

### Requirement: Every owner-scoped child runs in an owner cell
Every spawn site that runs owner-scoped work SHALL go through the bounded mapper's fixed
classes: decoder, git, git_bridge, preview, node, tool, video, provider-discovery,
provider-exec, packages, owner-delete and center-root. A cell SHALL see only its owner's tree,
its sealed launch snapshot and its exact relay sockets, under a named seccomp profile. A spawn
site with no cell class SHALL refuse; there SHALL be no unconfined fallback. Provider
admission SHALL NOT branch on provider identity.

#### Scenario: A cell cannot reach another owner
- **WHEN** owner A's cell of any class tries to read or write owner B's tree, vault,
  materialized credentials or relay socket
- **THEN** the kernel refuses and no byte of B's data is read or changed
- **AND** the same cell completes its legitimate operation on A's data

#### Scenario: A provider turn runs in provider-exec
- **WHEN** an owner runs a turn on a subprocess provider
- **THEN** the provider runs in a provider-exec cell under that owner's identity with its
  sealed snapshot and its pinned egress relay

#### Scenario: A production-built command uses a bare executable name
- **WHEN** the server builds a provider command using a bare CLI name
- **THEN** the cell resolves it only in the immutable image wrapper directory and applies
  the same shipped-executable validation before exec

### Requirement: Cell refusals remain observable without disclosing owner data
Launcher, mapper, decoder and relay failures SHALL produce bounded diagnostics containing
only fixed operation identifiers, immutable code locations, exception classes and errno or
exit/signal numbers. The mapper SHALL drain cell stderr without blocking on the daemon,
and carry the diagnostic on its authenticated completion channel. Provider stream failure
SHALL collect that completion and raise a ProviderError with the reason, rather than a bare
socket reset. No exception message, source line, argv, environment or owner bytes SHALL enter
the diagnostic.
The completion authenticates which cell ended, not the truth of cell-written stderr;
owner code can forge a bounded diagnostic for its own cell. Relay teardown notices SHALL
NOT override native provider error classification. Undelivered stderr SHALL be bounded
and explicitly reported as truncated.

#### Scenario: A tool cell refuses or exits before returning a complete frame
- **WHEN** tool maintenance or execution closes, resets or truncates its stream, or refuses launch
- **THEN** the tool error carries the bounded mapper refusal or authenticated completion reason
- **AND** immutable deployed script names map to source locations without exposing exception text

#### Scenario: Platform entry count does not prevent an owner's tools from starting
- **WHEN** a migrated center contains many hidden platform entries
- **THEN** tool mounts select only owner content before applying the mount-count bound
- **AND** owner labels, exclusive regular-file inodes and the read-only virtual center remain required

#### Scenario: A decoder rejects a provider launch after its cell proof
- **WHEN** validation or exec fails before the CLI consumes its stdin
- **THEN** the daemon logs the bounded reason and the ProviderError carries it even if the
  data stream resets with unread input

### Requirement: Cutover acceptance runs production-data chat
The image acceptance SHALL restore and migrate a production backup and run real HTTP
converse turns for Claude Code and Codex in compose posture. The real server SHALL build
the persona, command, environment and MCP configuration. Only provider transport credentials
and endpoint SHALL be substituted with a local streaming service through the real egress
proxy, on a network unable to contact external APIs. Synthetic launch fixtures SHALL NOT
substitute for this acceptance.

#### Scenario: Both provider turns complete on the migrated production copy
- **WHEN** the cutover acceptance runs for the selected command center
- **THEN** both real CLIs receive streaming responses through egress and converse returns
  the expected reply with the corresponding provider identity
- **AND** each turn invokes real write, read, edit and bash tools through its engine route;
  successful process results and round-tripped unpredictable file content prove the tool cell ran

### Requirement: Daemon readers enforce owner labels
Daemon reads and writes inside an owner's tree SHALL use pinned no-follow descriptors. They
SHALL refuse an entry whose UID/GID label belongs to a different owner than the request's.

#### Scenario: A planted link cannot steer the daemon
- **WHEN** owner A's tree holds a symlink to owner B's file and the daemon reads it for A
- **THEN** the read refuses without opening B's file

### Requirement: The broker owns egress state and the owner channel needs a memory-only factor
The outbound ledger, its sidecars and the proxy runtime SHALL live under `/data/.broker/`,
owned by uid 1002. The daemon SHALL reach them only over authenticated broker IPC. The owner
channel SHALL require the peer uid plus a factor held only in daemon memory, never on disk.
The daemon and the broker SHALL mark themselves non-dumpable after exec.

#### Scenario: A same-uid process cannot act as the owner
- **WHEN** a process at uid 1001 that is not the daemon connects to the owner channel
- **THEN** the broker refuses it, because it lacks the in-memory factor

### Requirement: Centers are admitted at runtime from the broker log
The broker SHALL record each center admission and retirement as a generation-numbered `admit`
or `retire` row in an append-only log. The machine identity SHALL come from the principal's
reservation, never from a caller. A retired center SHALL never be admitted again. The mapper
SHALL bind a center only after reading its row over an inherited read-only broker channel and
checking the root's canonical label. A new root SHALL be labelled `1001:<owner gid>` through a
capability-free setgid hand-off from the center-root cell. Deletion SHALL append `retire` and
drop the binding.

#### Scenario: A new user's first center runs without a restart
- **WHEN** a user signs up after startup and their first center is created
- **THEN** the broker appends one `admit` row, the mapper binds it, and the user's first cell
  runs under their dedicated identity
- **AND** a cell request before the bind refuses

#### Scenario: A forged admission refuses
- **WHEN** the daemon names an absent generation, a retired center, or a root whose group is
  not the row's machine
- **THEN** the mapper refuses and its table is unchanged

### Requirement: Owner deletion is two-pass without capabilities
Deleting an owner tree SHALL run in two passes. In the first, the owner-delete cell runs as
the owner and removes owner entries without following links. In the second, the daemon removes
its own entries and the empty structure. An owner-wide admission fence SHALL hold until the
daemon finishes the exact deletion token. An entry that cannot be removed SHALL fail loudly
with its path, never as a silent partial success. The same applies to subtree removal (pool
removal and scoped reset).
Both subtree passes SHALL retain the same parent and target descriptors. Ancestors SHALL
be opened without following links, and the original device/inode SHALL be checked before
removal. Subtree deletion SHALL use the same quiescent owner-wide fence as account deletion.

#### Scenario: An ancestor is replaced between deletion passes
- **WHEN** an owner replaces a writable ancestor with a symlink into another owner's tree
- **THEN** the daemon uses its held descriptors and never traverses the replacement
- **AND** another owner's daemon-owned credentials remain intact

#### Scenario: Account deletion removes engine-created restrictive files
- **WHEN** an owner's engine created 0700 directories and 0600 files and the account is deleted
- **THEN** both passes complete with zero capabilities and the tree is gone
- **AND** no other owner's entries are read or changed

### Requirement: One forward-only migration, re-runnable after a crash
The cutover SHALL convert the production volume once, with every writer stopped, after a
verified full snapshot. It SHALL run in a one-shot container that holds `CHOWN`, `FOWNER`
and `DAC_OVERRIDE`, which no service process holds. The migration SHALL refuse before any
write if the volume is in use, if the filesystem is overlay or lacks ACL support, if no
snapshot id is given, or if any inode's names span more than one owner or platform class.
Each step SHALL converge to a target computed from the path and `owner-identities.db`, and
SHALL change only fields that differ. The layout marker SHALL be written last. Rollback SHALL
be restoring the snapshot and the previous image together with its pinned runtime bundle;
there SHALL be no reverse
migration.

#### Scenario: A crash is fixed by running it again
- **WHEN** the migration is killed at any step and the same command is run again
- **THEN** the final volume manifest (path, uid, gid, mode, ACL, content hash) is identical to
  an uninterrupted run
- **AND** a further run changes nothing and `--check` reports zero diffs

#### Scenario: A cross-owner inode stops the migration before any write
- **WHEN** the preflight finds an inode named in two owner trees
- **THEN** the migration exits non-zero, naming both paths, and the volume is unchanged
