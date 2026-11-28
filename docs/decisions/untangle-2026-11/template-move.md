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

## Where a move runs (research again, Fact 14: the review at 313a71b6e)

The first build moved a sandbox inside the start that opened its lease. The review measured
the window that start runs in, and the shape broke:

- **The window.** A start's lease is opened with `lease_expires = now + LEASE_TTL_S`, 60 s
  (`owner/src/files.ts`), renewed only by the runner's heartbeats; the owner settles a run whose
  runner it has not heard from in that time `unknown` (`owner/src/runs.ts` `lost`), and stops a
  start that answers after it (`owner/src/launch.ts`). So all of a start, its sandbox's resume,
  `prepare` (about 7 s, `PREPARE_MS`), its network and its launch, has 60 s.
- **What a move takes.** `carry-out` hashes every file with a row (`sync.ts` `carryOut`, so it
  grows with the synced workspace, up to the 14.4 GB disk) and packs and hashes the archive; a
  create, install and `prepare`; the copy, as long as the archive; `carry-in`, which hashes and
  unpacks it again. Nothing bounds that by a start's window, and for a large workspace no bound
  could fit it: such a move would cut the run that brought it, at every retry.
- **Elsewhere.** No provider moves a live sandbox to a new image in a request's path. E2B keeps a
  sandbox on its template and its lifecycle events (created, paused, resumed, killed) are
  notices, not moments to act in; Modal and Daytona start new sandboxes from a new image or
  snapshot and restore an archived one asynchronously; Codex cloud, Claude Code on the web,
  Cursor and Devin start each session from the newest image. An update reaches a user's
  environment when nothing waits on it.
- **What our lifecycle gives.** The look (this object's alarm) already runs at moments no run
  waits on: it pauses an idle sandbox, a released one's pause lands, and it is armed by every
  start and release. Its handler has 15 minutes of wall clock (`ALARM_WALL_MS`); a sandbox's
  live network (its egress transform among it) and its sealed route are read back from E2B, so a
  move needs nothing a start brings. A paused, idle sandbox costs nothing while it waits.
- **Not chosen:** one deadline for the move inside the start (what is left of 60 s after the
  start's own steps): a move that cannot fit never happens, and one that nearly fits still
  delays the run it rides on.

So the move runs at the look, after it, on a sandbox nothing lives in, its long steps off the
serial chain: a start that comes meanwhile is never kept waiting, and the move gives way to it.

## Design, from the research (decided before building)

**A sandbox on another template than the pin is moved at the look, once nothing lives in it: a
new sandbox is made from the pin, what lives only in the old one is carried across, the new one
is paused, idle, and the old one killed. A start never moves a sandbox, so no run waits on a
move or is cut by one; a move that cannot finish leaves the old sandbox as it was.**

1. **Which template a sandbox is on** is its row's (`template`, set when this Worker makes it,
   from the pin it makes it from). A row kept from before names none, so it moves once, in the
   background, costing no run; that includes a sandbox main's Worker made from the new pin
   before this deploy. Telling those apart would need E2B to say a sandbox's template tag: its
   `info` names a template id, not the tag, so the row is the record.
2. **The moment** is the look (the alarm), after its own work, for each row due a move
   (`#moveDue`: another template, no lease, renewal, release, kill or move in it, past its
   back-off). The alarm is armed for it (`#arm`). A paused sandbox is resumed for the move when
   there is room for it and the new one, by the line's rule (`roomForOne`, renewals' held slots
   counted), and its runner made this Worker's where nothing lives, as at any resume.
3. **What is carried is the runner's to say** (it owns the disk's format): the engines' stores
   and generations whole, and of the workspace only what lives nowhere else, the unsent changes
   (each file whose bytes differ from its base, with its mirror row; each removal not sent, its
   row alone) and every session row. The new mirror holds those rows alone, its cursor 0, so
   the first sync-in rebuilds the rest from the owner's files, exactly as a new sandbox does,
   and the unsent changes go out against their bases at the next sync-out, as they would have.
   Not carried: the sync-ignore's kept-out paths and the agents' scratch (the working copy's
   own and a cache, unbounded), the caches, the finished jobs' state.
4. **The protocol**: three Worker commands, sent to this Worker's own runner alone (`#own`):
   `carry-out <authority> <until>` (the old sandbox's runner packs the carry into the
   root-only state directory and answers its size and SHA-256, or refuses while anything
   lives), `carry-in <sha256> <until>` (the new sandbox's runner checks the archive is those
   bytes, refuses a sandbox that already holds a store or a mirror, and puts each part in place
   by its owner's rules, every owner and mode set by it), and `carry-drop` (the old sandbox's
   archive dropped where the move went no further). Each carry command stops at `until`, the
   Worker's deadline, its sync agent and its `tar` with it, and leaves no archive past it. They
   are protocol 6: protocol 5 plus the three, no document, answer of 5 or read of the owner's
   changed, recorded and spoken first by #5860 (`untangle/template-move-protocol`), which the
   stage serves; this change moves the runner to 6 (`runner-speaks.json`). #5860's deploy hold
   refused this tree until then. **No rollback of the sandbox Worker past this deploy**:
   migration 8 moves `SandboxControl`'s lock to 8, and the kit refuses a rollback to a version
   of a lower `-m` (`deploy/deploy.ts` `forwardOnly`); this deploy is fixed forward. #5860's
   runner still drops an archive a runner of 6 left, should a sandbox ever meet it.
5. **The Worker's steps** (`#moveIdle`), each one's state kept before the next:
   - on the chain: the try charged (`move_tries`, `moveBackoff`: a lease's TTL, doubling, at
     most a day) before anything is made, so a look cut short by its wall clock backs off too;
     the old sandbox running on this Worker's runner; its live network and sealed route; the
     jobs launched in it so far (`launches`);
   - off the chain, each step bound by `until` (the alarm's wall clock less a renewal's whole,
     and the next renewal window of any other sandbox of the owner's, so no look's renewal waits
     on a move): `carry-out`; room for one more; the new sandbox made from the pin with the old
     one's network and route, named on the row (`move_to`) before anything else awaits, so while
     it holds nothing of the owner's; its runner installed and prepared; the archive copied
     through envd, streamed, its length known, cut at `until` (`copy`); `carry-in`;
   - on the chain: only if the row still names the old sandbox, no job launched in it since
     (`launches`), no lease runs in it and its runner says nothing lives, the row names the new
     one (`#made`, the create's own) and the old one is marked to be killed (`retiring`), in one
     transaction; the new one, idle, is paused as the reaper would; the old one is killed
     (`#settle`), its end logged on the meter as E2B answers the kill, and a kill that fails or
     whose reply is lost leaves it marked, for the look, never the new one.
   A move put off (something lives, a job launched meanwhile, no room, no time left in the
   look) is tried again a lease's TTL on; one that fails at any step is logged
   `sandbox_move_failed`, what it made killed by its id, the old sandbox's archive dropped, and
   tried again past its back-off. An object that ends at any step leaves `move_to` or
   `retiring` set, which the next call on the chain or the look (armed at once while either is
   set) settles (`#settle`, never a move under way, `#moving`), and an erasure kills too. Time
   that only the platform's sandbox ran (a move's new one before it is the owner's) is no
   owner's on the meter.
6. **No credential lost or exposed**: the sign-ins move VM to VM as bytes through envd, by the
   key that already reaches every sandbox's envd as root (the Worker's one secret), so no new
   holder. The archive stays in each sandbox's root-only state directory and is deleted once
   taken, dropped in the old sandbox where the move went no further (and at `prepare`); nothing
   is logged or stored on the way. The new sandbox holds the sign-ins only from the copy on,
   after the row names it.

Checked against the intent: a new pin reaches every owner's sandbox at the look after its next
use with nothing living (for the stage owner, the run after its next `auto` pi run runs on the
new image), no run waits on a move or is cut by one, whatever the workspace's size, and the
owner's sign-ins, conversations and unsent changes survive. Zero hosts: the Worker alone moves
it.

## Problems found (untangle step 2), with the fix above each

- **A sandbox names the template it was made from, and the look moves one on another, with
  nothing living, moves it.**
  - `#ensure` read `E2B_TEMPLATE` only at a create: a sandbox outlived every pin after its own,
    and nothing recorded which pin it was made from.
  - The only way to move one was a hand kill at E2B, which drops the sign-ins, the unsent
    changes and the store's unsent sessions with it.
- **What a sandbox holds that nothing else does is the runner's to name, and to carry.**
  - `state-format.ts` named what is durable, but nothing could take it out of a sandbox: the
    disk was the record, so a sandbox could never be replaced on purpose.

## Review findings (#5857, at 01eadda3e), with the fix above each

- **A move is never a start's refusal.**
  - `carry-out` and E2B's count were read outside the move's fallback: a fault there (or an old
    disk that cannot hold the archive, every try) refused the start.
- **A move keeps its state before each step; the new sandbox is never killed once it holds the
  carry, and the old one only after the row names the new one.**
  - A kill of the old sandbox whose reply was lost had the fallback kill the new one too: both
    gone, the sign-ins with them.
  - A Worker ending between the create and the row left the new sandbox unnamed, a copy of the
    sign-ins kept paused until erasure.
- **One room rule for one more sandbox** (`roomForOne`).
  - The move's own check ignored renewals' held slots.
- **The rule's two deploys** (#5860 first).
  - Protocol 6 shipped with its runner in one deploy while the stage owner has a sandbox.
- **A failing move backs off**, and its archive is dropped in the old sandbox.
- **The carry sets every mode** (`ownTree`), and says GNU tar's defaults it keeps.
- **A carry hashes every file with a row**: an edit of the same size inside the change time's
  tick, which no sync finds, is carried.
- **The create path names its sandbox before reading its window**, as the move does: a fault
  reading it after made a second sandbox at the next start (found in an outside rehearsal on
  the PR's body, folded here).
- **The carry tests' CI time**: they ran the real `prepare`, five times in two tests. Its
  lockdown walks the whole disk, over a GitHub host's `/usr` 47.7 s a call (its own tests are
  tagged `large`), so one case ran 54 to 59 s against the medium budget of 47 s, the other 11 to
  20 s. The carry needs no `prepare`; those calls are gone (CI: 0.6 s and 1.6 s), and the large
  prepare test holds that it drops an archive a failed move left.

## Review findings (#5857, at 313a71b6e), with the fix above each

- **A move runs at the look, never in a start** (research above).
  - The whole move ran inside the start that opened the lease, with nothing to keep it under
    that lease's 60 s, and its steps' own bounds added up to twice that; a large workspace cut
    the run that brought the move, at every retry.
- **Each carry command stops at the Worker's deadline, and leaves no archive past it.**
  - A `carry-out` the Worker gave up on went on, and wrote its archive after `carry-drop`.
- **The meter logs the old sandbox's end as E2B answers its kill**, not at the move's commit.
- **The second deploy's owed line, ticked** (`template-move-protocol.md`).
- **No rollback past this deploy** (design 4), said.

## Left, with reasons

- **A mode alone changed is not carried**: the mirror keeps no mode, so neither a sync nor the
  carry can tell one, and the move's first sync-in takes the owner's. Holding it needs a mode
  in the mirror, a format change of its own in two deploys.
  - [ ] untangle/sync-mode: the mirror keeps each file's exec bit, so sync-out sends a mode
    alone changed and a carry keeps it.
- **A create whose reply is lost** (E2B made it, the Worker never heard its id) leaves an
  unnamed sandbox, fresh and holding nothing of the owner's, until erasure; the move's create is
  the same. Naming one before its reply needs an id of the Worker's own on its metadata, read
  back by E2B's list.
  - [ ] untangle/create-reply: a create names its sandbox by a token of the Worker's own before
    it is sent, and a start or the look finds one whose reply was lost by it.
- **Kept-out paths and scratch start empty after a move**: unbounded working files and caches;
  a run rebuilds them, as after disk pressure. What the owner sees is below.
- **A move is no clean room**: what the old sandbox's store holds (an engine's config, its hooks
  and MCP config among a CLI's kept paths) comes across as it was, so a pin that fixes the image
  fixes nothing an attacker with root in the old sandbox planted in the store. That attacker
  already holds the same sign-ins; a store kept is no worse than the sandbox kept.
- **Whether envd takes a streamed upload of known length**, as a body copied from its own
  download: the transport's upload was probed with whole bytes (`transport.ts` header). The
  first live move shows it; until then a move that fails there leaves the old sandbox serving,
  tried again a day at most later.
- **A move's time on the stage is not measured here** (no live act from this change): the
  look bounds it, and a start never waits on it, so its time is a cost of the platform's alone.
  Measured at the first live move.

## What an owner sees

- Nothing at a start: a run on a sandbox of an older template runs there, at once. The move
  happens afterwards, at the look once nothing lives, so the next run after it runs on the new
  image, from a paused sandbox resumed as any other.
- Signed-in engines stay signed in; the conversations resume; unsent files and removals are sent
  at that run's end against their bases, as they would have been.
- The run's first sync-in reads every file again (a new sandbox's own), so a large workspace's
  first run takes longer.
- The working files the sync-ignore keeps out (`node_modules`, build outputs, a custom line's
  paths) and the agents' scratch (clones, installs, caches) are gone; the agent rebuilds them. A
  file's mode changed alone, with its bytes as synced, is the owner's again.
- A move refuses and delays nothing: one that cannot finish, or that a start overtakes, leaves the
  old sandbox with everything it had, and is tried again later.

## As built

- `sandbox/src/control.ts`: `#create` (the one create) and `#made` (its row, `template` among
  it, a move's too); `#moveDue`, `#movesDue` and `#moveIdle`, after the look in `alarm`, which
  `#arm` arms for a row due; `#settle`, run on the chain by `#ensure` and the look and by a
  move's commit, sparing a move under way (`#moving`), logging a retired sandbox's end as it is
  killed; `#own`, which sends `OWN_COMMANDS` to this Worker's runner alone; `roomForOne`;
  `moveBackoff`, `MOVE_LEAST_MS`; `#forget` and `#unrow`. `#ensure` moves nothing. The header
  says it. `src/schema.ts` migration 8 (`template`, `launches`, `move_to`, `move_tries`,
  `move_after`, `retiring` with its authority), pinned.
- `sandbox/src/e2b.ts` `copy` and `sandbox/e2b/transport.ts` `download` and a streamed `upload`:
  the carry through envd, its length fixed (`FixedLengthStream`), never whole in the isolate,
  cut at the move's deadline.
- `sandbox/runner/protocol.ts` (`OWN_COMMANDS`, `CarryAnswer`), on #5860's record;
  `runner-speaks.json` 6.
- `sandbox/runner/runner.ts`: `carry-out`, `carry-in` and `carry-drop` (`CARRIED`, `ownTree`,
  `fileSha`, `untilOf`, `tarUntil`), each carry stopped at its deadline and leaving no archive
  past it; `syncAgent` taking the lapse it stops on (a run's heartbeat, or a carry's deadline);
  `session.ts` `GENERATIONS`; `sync.ts` `carryOut` (every file with a row hashed), `carryIn`.
- Tests: `test/sandbox.test.ts` "a template move" (a start runs at once in a sandbox of another
  template, and the look moves it once nothing lives, its carry in the new one, which is paused,
  the row naming it, the meter's start, pause and then kill; one that named no template moved
  once; a move held as long as a workspace too big for any start's window, a start meanwhile
  running at once in the old one, the move giving way and happening at a later look; five
  faults, each keeping the old sandbox, the new one killed, the archive dropped and the move
  backed off; the old one's kill reply lost, the new one kept and the old one killed by a later
  look; one a move made that no row names, killed; no move without room for two, a renewal's
  slot held; none while a lease runs), the create naming its sandbox before reading its window;
  `runner.test.ts` "a template move's carry" (the real runner, as root: the sign-in,
  generation, session row, unsent change, unsent new file, same-size edit and unsent removal
  carried, every mode the new runner's, and nothing synced, kept out or scratch; refused while a
  job lives, past its deadline (no archive), of another carry's bytes, and into a sandbox with a
  store; the archive dropped).

- [ ] live: the stage owner's sandbox moves to the pin at the look after its next use once this
  deploys (envd takes the streamed copy; the move's time recorded here), and one `auto` pi run
  on an OpenRouter connection sealed with the Messages API then answers on pi 1.6.0.
