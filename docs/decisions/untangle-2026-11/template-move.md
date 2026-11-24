# template-move as built (2026-11-24)

`untangle/template-move`, owed by #5854 (`untangle/pi-catalog`, its review): an owner's sandbox
keeps the template it was made from. `sandbox/src/control.ts` `#ensure` creates from
`E2B_TEMPLATE` only when the authority has no sandbox; a running or paused one is resumed, and a
renewal pauses and resumes that same sandbox. So a new pin (a new pi, a new CLI, an image
security fix) never reaches a sandbox that exists. After #5854 and its pin, the stage owner's
sandbox would still run pi 1.4.0, and `auto` pi runs on OpenRouter Messages-API connections
would keep failing. Killing that sandbox at E2B by hand, an irreversible act, is today's only
way out, and it loses the owner's sign-ins.

## Research first (Fact 14), read 2026-11-24 on main 11477c11e

### What an owner's sandbox holds, and where else it lives

From `runner/state-format.ts`, `runner/session.ts`, `runner/sync.ts`, `runner/scratch.ts` and the
template recipe (`template/image.py`):

- **The image**: Node, the engines (`/opt/ta/engines/<id>`, `npm ci` from each lock), the users,
  the loader. Made once by the template build and read-only to every run. This is what a move
  must replace.
- **The engines' stores** (`/var/lib/ta/store/<engine>`, root's alone) and their generations
  (`/var/lib/ta/generations`): each engine's `secret_paths` (a vendor sign-in, which only the VM
  holds: the owner never sees it, `login.ts`) and its `session_paths` (the store's copies of the
  conversations). **No other home.** The sessions are synced to the owner's files at each run's
  end (`stageSessions`), but a store copy the owner's files do not hold yet (a staging refused)
  lives only here, and the sign-ins live only here by design.
- **The workspace** (`/workspace`) and its mirror (`/var/lib/ta-sync/mirror.db`): a copy of the
  owner's files and, per path, the base version it was read at. Rebuilt by any sandbox's first
  sync-in (what a new sandbox does today). **Except the unsent changes**: a file whose bytes are
  not the mirror's (a refusal kept it: `storage_full`, `secret_bytes`, an upload given up), a
  removal not yet sent, each against its base. Those live only here. A sandbox's mirror rows of
  the sessions (`/workspace/.sessions/...`) say what the owner's files hold of each store copy,
  so they go with the store: a store copy with no row is sent with no base, a conflict copy.
- **Paths the sync-ignore keeps out** (custom lines, defaults such as `node_modules`): the
  working copy's own files, never synced. Unbounded (they are what the list is for).
- **The agents' scratch** (`/scratch/<agent>`) and its stamps: "a cache" by design
  (`scratch.ts`), let go under disk pressure, the owner told.
- **Caches**: `sessions-seen.json` (discarded at any format change), the tools' caches (in
  scratch).
- **Each job's state** (`/var/lib/ta/runs`, `/signin`): read only while the job lives (`liveJobs`,
  `recover`). Nothing a later job reads once none lives.
- **Size**: the disk is 14.4 GB (`limits-audit.md`), less than a free tier's 25 GB of storage, so
  "carry the whole disk" is no shape: a workspace near the disk cannot even be archived on it.

### What the records already say about a version moving

- **The runner by digest** (#5428, `control.ts` header): the Worker ships the runner; a sandbox
  whose row names another runner adopts this Worker's at a start or resume that finds nothing
  living in it (`#speaking`, `adopt`), never under a live run or sign-in. That is exactly the
  rule a template move needs: a new version reaches every sandbox at its next idle start, and
  nothing is cut. The template is the one version that cannot be adopted in place: the engines
  are the image's.
- **The format rule** (`state-format.ts`): every runner reads the format before its own; the
  disk is "mostly durable, never thrown away". A move made by this Worker's runner on both sides
  carries its own format, so no cross-version read is needed.
- **The protocol rule** (`protocol.ts`, `runner-protocol-contract.md`): a command added is a new
  protocol, recorded and frozen. The Worker speaks its runner's protocol and at most the ones
  either side.
- **`runner-answers.md`**: a protocol's record holds what its runner reads of the owner's
  answers (`relayed`), and the deploy kit holds the owner to every protocol spoken. A new
  protocol that changes no read carries protocol 5's `relayed` unchanged.

### What E2B allows (docs.e2b.dev, read 2026-11-24 through search; the docs host is outside this session's network)

- A sandbox is made from one template and keeps it: no call moves a sandbox, running or paused,
  to another template. Pause keeps its filesystem and memory; a paused sandbox has no TTL and
  is removed only by a kill.
- Snapshots (`POST /sandboxes/{id}/snapshots`) capture a sandbox to start others from, its
  filesystem and memory as they were: the old image with them, so no help for a move.
- envd's file API reads a file (`GET /files`) as it writes one (`POST /files`), by the
  sandbox's envd token, as root.

### What the major hosted agents do

- **Codex cloud**: each task runs in a container from the environment's image and setup script;
  a container is cached for a time, and editing the setup script, variables or secrets drops
  the cache. The durable state is the repository, not the container.
- **Claude Code on the web**: each session gets a fresh VM from its cloud environment; the setup
  script's result is cached; the repository is cloned in.
- **Cursor cloud agents**: an environment's build or snapshot is what every new agent starts
  from; a new build reaches new agents, and an agent's disk is not carried.
- **Devin**: sessions boot from the latest machine snapshot; an update reaches the next session
  (an enterprise snapshot is re-inherited by choice).
- **Replit**: the durable data is outside the container (persistent storage, object storage);
  the container itself restarts on deploys and sleeps, and its filesystem is not the record.

None rebases a live environment onto a new image. Each starts **new work** from the newest
image, with the durable state elsewhere (the repository, a storage service). The secrets each
needs are the platform's (injected at start), never the old container's disk.

### The direction

The zero-hosts rule (Direction 8): the move must run with no host, inside the sandbox Worker,
at a moment a call already brings. Fact 15: no new limit. Fact 8: fail loudly, never fall back
to something that looks right. Fact 4: a move is not an outward act or spending beyond a run's
own (one more sandbox for seconds, then one fewer).

## Design, from the research (decided before building)

**A sandbox on another template than the pin is moved at the next start that opens a lease
on it while nothing lives in it: a new sandbox is made from the pin, what lives only in the old
one is carried across, then the old one is killed. Nothing living is ever moved, and a move
that cannot finish leaves the old sandbox serving the run, untouched.**

1. **Which template a sandbox is on** is E2B's own record of it: the create's metadata names
   the pin (`ta_template`), and `info` reads it back. No row column, no copy. A sandbox made
   before this change names none, so it is on another template, and moves once.
2. **The moment** is a start or sign-in that opens the lease (it brings the network a create
   needs) and finds the sandbox, running or resumed, on this Worker's runner (adopted when
   nothing lives, as today) with nothing living (`carry-out` refuses while anything does). A
   joining run never moves a sandbox (something lives). The look never moves one: a paused,
   idle sandbox costs nothing, so it moves when it is next wanted, and no move runs a sandbox
   no start asked for.
3. **What is carried is the runner's to say** (it owns the disk's format): the engines' stores
   and generations whole, and of the workspace only what lives nowhere else, the unsent changes
   (each file whose bytes differ from its base, with its mirror row; each removal not sent, its
   row alone) and every session row. The new mirror holds those rows alone, its cursor 0, so
   the first sync-in rebuilds the rest from the owner's files, exactly as a new sandbox does,
   and the unsent changes go out against their bases at the next sync-out, as they would have.
   Not carried: the sync-ignore's kept-out paths and the agents' scratch (the working copy's
   own and a cache, unbounded), the caches, the finished jobs' state.
4. **The protocol**: two Worker commands, `carry-out` (the old sandbox's runner packs the carry
   into the root-only state directory and answers its size and SHA-256, or refuses while
   anything lives) and `carry-in <sha256>` (the new sandbox's runner checks the archive is
   those bytes, refuses a sandbox that already holds a store or a mirror, and puts each part
   in place by its owner's rules). They are protocol 6: protocol 5 plus the two commands, no
   document, answer of 5 or read of the owner's changed. The Worker speaks 5 and 6.
5. **The Worker's steps** (`#move`, on the serial chain, inside the start): `carry-out`; make
   the new sandbox from the pin with the start's network and route, its runner installed and
   prepared; copy the archive from the old sandbox to the new one through envd, streamed, its
   length known (no copy in the Worker's memory or storage); `carry-in`; kill the old sandbox;
   then the row names the new one (the meter logs the old one's kill and the new one's start).
   Any fault or refusal before the kill (E2B with no room, the carry's checks, a full disk)
   kills the new sandbox if one was made, logs `sandbox_move_failed` loudly, and the start goes
   on in the old sandbox: it is asked again at the next idle start. A move never makes a run
   wait that the old sandbox could serve, so the room it needs is E2B's count and refusal
   alone, not a turn in the line.
6. **No credential lost or exposed**: the sign-ins move VM to VM as bytes through envd, by the
   key that already reaches every sandbox's envd as root (the Worker's one secret), so no new
   holder. The archive stays in each sandbox's root-only state directory and is deleted once
   taken (and at `prepare`); nothing is logged or stored on the way.

Checked against the intent: a new pin reaches every owner's sandbox at its next start with
nothing living (for the stage owner, its next `auto` pi run), the run that brings it runs on the
new image, no run is cut, and the owner's sign-ins, conversations and unsent changes survive.
Zero hosts: the Worker alone moves it.

## Problems found (untangle step 2), with the fix above each

- **A sandbox names the template it was made from, and a start that finds it on another, with
  nothing living, moves it.**
  - `#ensure` read `E2B_TEMPLATE` only at a create: a sandbox outlived every pin after its own,
    and nothing recorded which pin it was made from.
  - The only way to move one was a hand kill at E2B, which drops the sign-ins, the unsent
    changes and the store's unsent sessions with it.
- **What a sandbox holds that nothing else does is the runner's to name, and to carry.**
  - `state-format.ts` named what is durable, but nothing could take it out of a sandbox: the
    disk was the record, so a sandbox could never be replaced on purpose.

## Left, with reasons

- **The rule's two deploys.** Protocol 6 adds two commands to 5 and changes nothing a Worker
  of 5 sends or reads, so this Worker speaks 5 and 6 and its runner 6, in one deploy. A rollback
  of this one deploy meets a sandbox it adopted onto runner 6: one nothing lives in adopts the
  rolled-back Worker's own runner at its next start, as today; one a run lives in is refused new
  starts (`sandbox_runner_protocol`) until that run ends, and is killed only if the run is still
  live at its window's end (`foreign-runner-reap.md`). Holding the move one more deploy would
  keep every sandbox on its old engines, the stage's broken pi runs among them, for that long.
- **Kept-out paths and scratch start empty after a move**: unbounded working files and caches;
  a run rebuilds them, as after disk pressure. What the owner sees is below.
- **Whether envd takes a streamed upload of known length**, as a body copied from its own
  download: the transport's upload was probed with whole bytes (`transport.ts` header). The
  first live move shows it; until then a move that fails there leaves the old sandbox serving.

## What an owner sees

- The run that brings the move starts a few seconds later: one sandbox made, the carry copied,
  the old one killed, before the run's own start.
- Signed-in engines stay signed in; the conversations resume; unsent files and removals are sent
  at that run's end against their bases, as they would have been.
- The run's first sync-in reads every file again (a new sandbox's own), so a large workspace's
  first run takes longer.
- The working files the sync-ignore keeps out (`node_modules`, build outputs, a custom line's
  paths) and the agents' scratch (clones, installs, caches) are gone; the agent rebuilds them.
- Nothing is refused: a move that cannot finish leaves the run on the old sandbox, and the move
  is tried at the next idle start.

## As built

(written with the build)
