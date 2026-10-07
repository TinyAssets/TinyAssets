## ADDED Requirements

### Requirement: The universe agent has four platform-run tools over its own folder
A served founder turn that has engine tools SHALL be given exactly four tools
over its own universe folder: `read`, `write`, `edit` and `bash`. The
platform SHALL execute them; no vendor CLI built-in file or shell tool SHALL
be enabled for any turn. The four SHALL be served on the per-universe engine
route, so every provider adapter sees the same definitions, and SHALL NOT be
added to the public connector's top-level handles. No tool parameter SHALL
name a universe: the folder is the engine's pinned universe.

#### Scenario: every adapter sees the same four tools
- **WHEN** the served tool inventory is read for the claude, codex and HTTP
  adapters
- **THEN** each contains `read`, `write`, `edit` and `bash`, the CLIs' own
  `Read`/`Write`/`Edit`/`Bash` remain denied, and the public connector's
  handle set is unchanged

#### Scenario: another user cannot drive the tools
- **WHEN** an engine bound to one principal is pinned at a universe that
  principal does not currently own
- **THEN** every one of the four tools refuses before any process is started

### Requirement: The tools run in a tool jail that holds only the universe
Every tool call SHALL run as a process inside an OS jail in which the owning
universe is mounted read-write at `/u` and no other universe, no data root,
no platform source and no credential snapshot is reachable. The universe root
SHALL be read-only in the jail, with only the agent-owned brain files and
harness directories writable, and every hidden root entry (the credential vault,
`.runtime/`, the consent and usage databases) SHALL be masked so it is neither
readable nor writable and cannot be created. The jail SHALL have no network
namespace shared with the host, SHALL start from an empty environment, and
SHALL refuse creating symbolic links and special files, including through
io_uring. A host with no jail SHALL refuse the call; there SHALL be no
unjailed fallback.

#### Scenario: another universe is unreachable
- **WHEN** the agent reads another universe by its host path, by `..`, or
  through a link it tries to plant
- **THEN** the read fails, the link is never created, and no foreign content
  is returned

#### Scenario: io_uring cannot create a link
- **WHEN** a jailed process sets up an io_uring ring (to run a link op the
  syscall filter would not see)
- **THEN** the ring setup is refused, so no ring operation runs

#### Scenario: bash has no network
- **WHEN** a jailed command connects to a listener on the host loopback that
  the host itself can reach
- **THEN** the connection fails and the jail has only a loopback interface

#### Scenario: the jail starts while the daemon holds a database open
- **WHEN** a root entry that existed when the jail's view was built (a SQLite
  `-shm`/`-wal` sidecar) is gone by the time the jail launches
- **THEN** the call runs, the agent reads and writes its own files, and no
  hidden root entry is visible

#### Scenario: the platform-owned dir is masked
- **WHEN** the agent reads `.runtime/` or writes into it
- **THEN** it sees no credential or route bearer, and nothing it wrote exists
  on disk after the call

#### Scenario: the owner's credentials and authority state are out of reach
- **WHEN** the agent reads the credential vault, or writes the vault, a consent
  or usage database, `soul.md` or `config.yaml`
- **THEN** the read returns no secret, every write fails, and a database that
  did not exist is not created

### Requirement: The daemon treats every universe file as untrusted
The daemon SHALL read every file in a universe folder from outside the jail —
persona grounding, soul, self-model, voice, the skill index, and any other
such read — through one shared reader that opens every path component without
following a link, requires a regular file, bounds the read size, and parses
YAML only after refusing anchors and aliases. A file that is or sits behind a
link, is not a regular file, is over the bound or is hostile YAML SHALL become
a fail-closed default with a logged note, never an error that breaks the turn.

#### Scenario: an oversized or alias-bomb config is never parsed
- **WHEN** `config.yaml` is megabytes long or carries YAML anchors/aliases
- **THEN** the next turn's config load returns defaults promptly with bounded
  memory, and a strict writer refuses rather than erasing the file

#### Scenario: a pre-existing link is not followed into the prompt
- **WHEN** a universe grounding file is a symlink pointing at another user's
  file (planted from outside, or by a mechanism the jail filter cannot see)
- **THEN** the daemon's persona read returns nothing for it and no foreign
  content reaches the prompt

### Requirement: A CLI's own project settings are never a loading mechanism
Every provider launch SHALL mask every hidden directory at the universe root
other than `.runtime/`, by a rule that names no vendor, and SHALL refuse the
launch when a hidden root entry is a symbolic link.

#### Scenario: a settings dir the agent wrote is invisible to the next launch
- **WHEN** the agent writes `.claude/settings.json` (or any hidden dir) into
  its folder
- **THEN** the file is kept on disk, and a provider launched for the universe
  sees an empty directory there

### Requirement: Tool jails run under per-universe resource limits that fail closed
Every tool call SHALL run under limits on address space, process count, cpu
time, file size, open files, core size, wall-clock time, output size, summed
resident memory of its process tree, and the free space and free inodes it
leaves on the shared data volume, with bounded concurrency per universe and
per host. Jail processes SHALL be de-prioritised for CPU and SHALL be the
kernel's first choice under memory pressure, ahead of the daemon. A call whose
limits cannot be applied SHALL be refused with nothing run.

#### Scenario: a runaway is killed
- **WHEN** a jailed command allocates without bound, forks without bound,
  spins the cpu, floods output, sleeps past its wall clock, or fills the disk
  towards the floor
- **THEN** it is stopped, the result says which limit stopped it, nothing from
  the jail survives, and the universe's next call works

#### Scenario: limits that cannot be applied refuse the call
- **WHEN** the host has no bubblewrap or no `prlimit`, or the jail exits
  before proving its limits were applied
- **THEN** the call is refused and no command ran

### Requirement: The skill index is in the prompt and a written skill changes the next turn
A served founder turn that has the tools SHALL receive, in its system prompt,
the name and one-line description of each `skills/<name>/SKILL.md` in its
universe (progressive disclosure: not the body), read fresh on every turn and
never through a link. A turn without the tools SHALL receive no folder or
skill section.

#### Scenario: a skill the agent writes is followed on its next turn
- **WHEN** the agent writes `skills/standup/SKILL.md` in one turn
- **THEN** the next turn's prompt lists `standup` with its description, the
  agent reads the skill and follows it, and after the file is deleted a later
  turn no longer lists it

#### Scenario: visitors and dark deploys are not shown the folder
- **WHEN** the turn is not a founder turn, or engine tools are off
- **THEN** the system prompt has no folder or skill section
