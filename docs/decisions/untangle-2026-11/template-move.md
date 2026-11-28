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
  removal not yet sent, each against its base. Those live only here, so a move waits for a run's
  end to send them (below). A sandbox's mirror rows of
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

## Where a move runs (research again, Fact 14: the reviews at 313a71b6e and ef62940c9)

Two shapes were built and broke:

- **Inside the start that opens a lease** (to 313a71b6e): a start has its lease's 60 s, renewed
  only by the runner's heartbeats (`owner/src/files.ts`, `runs.ts` `lost`, `launch.ts`), and a
  move hashes and copies what the workspace holds, so a large one cut the run that brought it,
  at every retry.
- **At the look, off the serial chain, giving way to a start** (to ef62940c9): a live sandbox
  moved while runs can arrive kept sprouting faults. A run launched mid-move waited on the
  carry's sync lock; the look armed at alarm speed for a leased row; the move's deadline missed
  paused rows' retries and ran after a failed look; a row replaced or erased mid-move left the
  new sandbox unnamed, with the sign-ins. Each is a sign of the shape: two sandboxes of the
  owner's, and a run, at once.

What the research says (`Research first` above): no provider moves a live environment to a new
image while work can arrive in it. An update reaches a user's environment at a moment nothing
runs in it.

Weighed then:

- **(i) The owner's store carries what lives only in the sandbox**: at the idle pause, the
  sign-ins written to the owner's files, the old sandbox killed, the next start creating from
  the new pin and restoring. It breaks what the platform holds of a vendor sign-in: only the VM
  holds it (`runner/login.ts`: the owner sees a URL or a code, never a token), a sync refuses a
  file holding a secret's bytes (`sync.ts`, `guards.ts`), and the owner's files are every one of
  the owner's agents' to read and every export's to carry. An encrypted copy needs a key the
  sandbox Worker would keep, and it keeps none. **Refused.**
- **(ii) A paused sandbox, moved on the chain, one sandbox running at a time.** Chosen, and still
  the shape below.

## What a move carries, and when it may run (research again, Fact 14: the review at 4bf8fe835)

Six rounds of Important findings came from two parts of (ii), not from its frame:

- **The carry took the workspace**: every file sync-out would send, found by hashing every
  synced file, and moved through the isolate at up to 16 MiB. Its time grew with the workspace
  and nothing measured it (4bf8fe835's third finding), and its size needed a cap (9ee259851).
- **A move had to give way to starts**, so a start never waited on that unbounded carry: first by
  a tail split of the bound (2b86b2768), then by a cut at any call (4bf8fe835). A cut after the
  resume gave back a try that had cost one, so a busy owner was resumed and paused every minute
  forever, unnamed (its second finding), and a cut inside the resume let a start launch in an
  unprepared sandbox (its first).

Two facts changed under it (the coordinator, 2026-11-28):

- **The founder made the platform's sandbox the last-resort placement**: vendor cloud agents on
  the user's subscription first, then the user's device, then this sandbox. Moves are rare, so
  the simplest correct shape beats a fast one.
- **The workspace is synced to the owner's files at each run's end** (`runner.ts`, the run's
  sync-out), and a new sandbox's first sync-in rebuilds it (what every new sandbox does).

Weighed against patching the cut and the carry:

- **(a) Carry only what no sync carries**: the engines' stores and generations (the sign-ins,
  which only the VM holds, and the store's session copies) and the mirror's session rows, which
  say what the owner's files hold of each store copy. Kilobytes, not the workspace. The new
  sandbox gets the workspace by its first sync-in. What the workspace holds unsent (a refused or
  failed sync-out) is not carried: the carry is refused while there is any, found by sync-out's
  own rule (`differs`: a stat against each row, a file read only where size or time do not
  settle it), so nothing the old sandbox holds is lost, and the next run's end sends it.
  Measured here: that walk over 100,000 files, a stat and a row read each, 1.05 s (warm, this
  session's Linux host, 2026-11-28, a scratch script against `fsafe.ts` `walkFiles` and the
  mirror). Taken.
- **(b) Move only a sandbox paused with nothing in it for a while** (`MOVE_QUIET_MS`, 30
  minutes from its last pause): a start then rarely meets a move, and one that does waits for
  it on the chain (no cut, no give-back), the move's bound and the start's own steps inside the
  start's lease. No data of owners' gaps between runs is kept, so it is a fixed figure: the
  move's safety rests on nothing in it, only how often a start waits; half an hour is past a
  conversation's pace, and costs that much more time on the older image. Taken.
- **(c) The row marked unprepared before any resume is sent** (`#resume`), so a resume whose
  answer never comes (a fault, a deadline; a start's own on main too) is prepared by whatever
  next finds the sandbox running. Taken.
- **(d) Every try that ends short of the move backs off and counts**, and from its third in a
  row it is named stuck (`sandbox_move_stuck`), the line the canary's owed alerts name. Taken.

With (a), the steps are fixed and measured (`MOVE_STEPS_MS`), and the one step that grows with
the workspace (`carry-out`'s walk) has its own share, cut there in the sandbox and in the
Worker alike (`CARRY_OUT_MS`, 3 s, about three times the measured walk), a walk past it a
counted try. The new sandbox takes no `prepare` in the move: nothing runs in it but root's
`carry-in`, as a start's create installs before it prepares, and its next resume prepares it,
as every resume does; that saves 7 s and a step. The old one is resumed as any sandbox is
(`#resume`, its `prepare` with it), one rule for every resume.

## Design, from the research (decided before building)

**A sandbox paused with nothing in it for `MOVE_QUIET_MS`, on another template than the pin, is
moved at the look, on the owner's serial chain, bound whole by `MOVE_MS`: it is resumed, what no
sync carries packed while its workspace holds nothing unsent, and it is paused again; a new
sandbox is made from the pin, the carry put in, it is paused, and the old one killed. A start
never moves a sandbox and never meets one being moved; one that comes meanwhile waits for the
move, inside its lease. A try that ends short of the move leaves the old sandbox paused and
whole, backs off and counts.**

1. **Which template a sandbox is on** is its row's (`template`, set when this Worker makes it,
   from the pin it makes it from). A row kept from before names none, so it moves once, costing
   no run; that includes a sandbox main's Worker made from the new pin before this deploy.
   E2B's `info` names a template id, not the tag, so the row is the record.
2. **The moment** is the look (the alarm) after one that failed at nothing, for each row due a
   move (`#moveDue`: another template, paused, no lease, renewal, release, kill or move in it;
   past `move_after`, which every pause pushes to `MOVE_QUIET_MS` on, `#pausedAt`, and every try
   to its back-off), only while no other row of the owner's needs the look within `MOVE_MS`
   (`#needAt`, the alarm's own rule, which `#arm` uses too). A leased row is never due, so a run
   on an older template arms nothing new. The move takes its turn and room in the platform's
   line (`slots.turn`, `roomForOne`, a renewal's held slot counted) for its one running sandbox.
3. **What is carried is the runner's to say** (it owns the disk's format): the engines' stores
   and generations whole, and the mirror's session rows, its cursor 0 (`sync.ts` `carryOut`),
   refused while a job lives or the workspace holds what sync-out would send (a file with no row
   or another identity, a removal not sent). The first sync-in in the new sandbox reads the
   workspace from the owner's files. Not carried: the workspace, the sync-ignore's kept-out
   paths and the agents' scratch (the owner's files', the working copy's own, a cache), the
   caches, the finished jobs' state.
4. **The protocol**: three Worker commands, sent to this Worker's own runner alone (`#own`):
   `carry-out <authority> <until>`, `carry-in <sha256> <until>` and `carry-drop`, protocol 6's
   (#5860, which the stage serves), its record naming the commands, the carry's path and
   `carry-out`'s answer, all as before; this change moves the runner to 6. Each carry command
   stops at `until` and leaves no archive past it. **No rollback of the sandbox Worker past this
   deploy**: migration 8 moves `SandboxControl`'s lock to 8, and the kit refuses a rollback to a
   lower `-m` (`deploy/deploy.ts` `forwardOnly`).
5. **The Worker's steps** (`#moveIdle`, on the chain, one move a look, every E2B and envd call
   cut at `#until`: `MOVE_MS` less the finish's share, `MOVE_FINISH_MS`, for the steps, and the
   finish's after, so the whole move ends by `MOVE_MS`):
   - the try charged (`move_tries`, `moveBackoff`: a lease's TTL, doubling, at most a day)
     before anything is sent, and never given back;
   - the old one resumed (`#resume`: unprepared before `connect` is sent; its runner made this
     Worker's where nothing lives; prepared);
   - `carry-out`, cut at its own share (`CARRY_OUT_MS`), read whole, the archive dropped there,
     the old one paused again;
   - the new sandbox named before its create is sent by a token of the move's (`move_tag`, on
     its metadata `ta_move`) and, once the create answers, by its id (`move_to`); the token kept
     until a list past its create's window (`move_tag_until`) finds nothing by it;
   - its runner installed; the carry written and put in place (`carry-in`);
   - in one transaction the row names the new one (`#made`, unprepared, so its next resume
     prepares it) and the old one is marked to be killed (`retiring`); the new one paused, idle;
     the old one killed (`#settle`), a kill that fails or whose reply is lost left to the look.
   A try that ends short of the move, at any step, is logged `sandbox_move_failed` with its try
   (`#moveFailed`) and, from its third in a row, `sandbox_move_stuck`. Its finish asks E2B for
   the old one and, where it runs (resumed, whether or not the resume's answer came), drops its
   archive and pauses it (`#moveBack`); what the move made is killed by its id or token. The
   meter logs what ran: the old one's resume and pause, the new one's start and pause.
6. **The bound** (`MOVE_MS`, 36,172.5 ms): the steps at their measured worst (`MOVE_STEPS_MS`:
   the old one's resume 895 ms, its `prepare` 7 s, two pauses at 705 ms, the create 1,078 ms,
   `carry-out`'s 3 s share, and 22 other calls at the slowest full request, 480 ms: info, the
   line's turn and E2B's count; the resume's network, `state`, an adoption's upload, install and
   `adopt`, `recover`, info and the slot given back; the carry's read and `carry-drop`, the line
   rung; the new one's upload and install, the carry's write, `carry-in`, info, the line rung;
   the kill, the line rung: 23,943 ms), and the finish (`MOVE_FINISH_MS`: three pauses and six
   calls, 4,995 ms), with a fifth of the bound to spare. A start that waits for all of it, then
   takes its own steps (a resume, a `prepare`, nine calls: 12,215 ms), starts 48.4 s after it
   came, inside its lease's 60 s.
7. **No credential lost or exposed**: the sign-ins move VM to VM as bytes through envd and this
   isolate's memory, by the key that already reaches every sandbox's envd as root (the Worker's
   one secret), so no new holder; nothing is stored or logged on the way. The archive is dropped
   in the old sandbox as soon as it is read, and deleted in the new one once taken (and at
   `prepare`).

Checked against the intent: a new pin reaches every owner's sandbox at the first look after it
has been paused with nothing in it for half an hour, and nothing unsent in its workspace (for the
stage owner, the run after its next `auto` pi run, half an hour on, runs on the new image); no
run is lost, and a start that comes during a move waits for it inside its lease; nothing is
billed twice; nothing is orphaned; every try that does not move counts, and a move that cannot
is named. Zero hosts: the Worker alone moves it.

## Review findings (#5857, at 4bf8fe835), with the fix above each

- **A sandbox is marked unprepared before its resume is sent** (`#resume`): "marks a sandbox
  unprepared before its resume is sent", and "pauses the old sandbox again when its resume's
  answer is lost mid-move" (`#moveBack`). Dropping the mark fails both.
  - A cut, or any fault, after E2B resumed a sandbox and before its answer came left the row
    `prepared = 1`, so the start that found it running launched with no `prepare`: after a
    reboot resume, on E2B's boot state.
- **No cut, and no try given back** (shape (b), (d)): a start that comes mid-move waits for it
  ("holds a start that comes mid-move on the chain, then runs it in the sandbox the move made,
  prepared first"); every try counts and is named stuck from its third ("counts every try that
  ends short of the move ... and names it stuck from its third"; never naming it fails it).
  - A cut after the resume gave the try back, so a busy owner was resumed, prepared and paused
    every minute, forever, never named.
- **The bound is honest** (shape (a), design 6): no workspace is carried, `carry-out`'s walk has
  its own share and is cut there ("counts a carry-out past its own share as a failed try"), and
  the create is counted at its measured 1,078 ms. "hands its steps a bound that holds their
  measured worst, and that bound and a start's own steps after it inside the start's lease"
  fails a 30 s bound and a 50 s one.
  - `carry-out` hashed every synced file as one 480 ms call, and carried up to 16 MiB each way.
- Nits: the stale lines are rewritten; a cut inside the resume is gone with the cut, and the
  finish pauses whatever E2B says runs; a start waits for the move, the case the bound sizes;
  `carry.pid` is gone with the cut (`carry-drop` drops the archive alone).

## Review findings before (#5857, 313a71b6e to 2b86b2768), where they stand

- **313a71b6e**: a move never runs in a start; each carry command stops at its deadline and
  leaves no archive past it; the second deploy's owed line ticked; no rollback past this deploy.
  Standing.
- **ef62940c9**: a leased row arms no look; no run meets a carry (the chain); no move after a
  failed look or while another row needs the look within `MOVE_MS`; nothing replaced or erased
  mid-move, and `move_to` kills what an ended object left. Standing, each tested.
- **01eadda3e**: a move is never a start's refusal; its state is kept before each step, the new
  sandbox never killed once it holds the carry and the old one only after the row names the
  new one; one room rule (`roomForOne`); protocol 6 in its own deploy first (#5860); the carry
  sets every mode (`ownTree`); the create path names its sandbox before reading its window; the
  carry tests run no `prepare`. Standing. Its "a carry hashes every file with a row" is gone: no
  workspace file is carried, and the unsent check is sync-out's own rule.
- **9ee259851**: a try keeps its back-off (now every try's); the new sandbox named by a token
  before its create is sent. Standing. Its cap (16 MiB) stands for the stores.
- **2b86b2768**: the bound sized from the steps, standing (design 6); its cut at a chain call is
  gone (above).

## Problems found (untangle step 2), with the fix above each

- **A sandbox names the template it was made from, and the look moves one on another, with
  nothing living in it.**
  - `#ensure` read `E2B_TEMPLATE` only at a create: a sandbox outlived every pin after its own,
    and nothing recorded which pin it was made from.
  - The only way to move one was a hand kill at E2B, which drops the sign-ins, the unsent
    changes and the store's unsent sessions with it.
- **What a sandbox holds that nothing else does is the runner's to name, and to carry.**
  - `state-format.ts` named what is durable, but nothing could take it out of a sandbox: the
    disk was the record, so a sandbox could never be replaced on purpose.
- **A resume's `prepared` was cleared only once its answer came** (main's too): a start whose own
  resume's answer was lost met the same unprepared sandbox. Fixed here, for every resume.

## Left, with reasons

- **A workspace that always holds something unsent never moves**: a file the sync refuses every
  time (`secret_bytes`, past the owner's storage) holds the move, each try counted, named stuck
  from the third. The owner is told of the refusal at each run's end already
  (`ta.sync_refused`); once it is sent or removed, the next try moves.
- **A workspace whose walk outlasts `carry-out`'s share** (some 250,000 files at the measured
  rate) never moves, each try counted, named stuck from the third. Its time on E2B is owed to
  the `live:` wait below.
- **A mode alone changed is not sent or carried**: the mirror keeps no mode, so neither a sync
  nor the unsent check can tell one, and the new sandbox's first sync-in takes the owner's.
  Holding it needs a mode in the mirror, a format change of its own in two deploys.
  - [ ] untangle/sync-mode: the mirror keeps each file's exec bit, so sync-out sends a mode
    alone changed.
- **A start's create whose reply is lost** (E2B made it, the Worker never heard its id) leaves an
  unnamed sandbox, fresh and holding nothing of the owner's, until erasure. A move's own create
  is named by its token; a start's is not yet.
  - [ ] untangle/create-reply: a start's create names its sandbox by a token of the Worker's own
    before it is sent, and a start or the look finds one whose reply was lost by it.
- **The quiet window is a fixed figure** (30 minutes): no data of owners' gaps between runs is
  kept. A start that comes mid-move waits for it; the window only makes that rare.
- **Kept-out paths and scratch start empty after a move**: unbounded working files and caches;
  a run rebuilds them, as after disk pressure.
- **A move is no clean room**: what the old sandbox's store holds (an engine's config, its hooks
  and MCP config among a CLI's kept paths) comes across as it was, so a pin that fixes the image
  fixes nothing an attacker with root in the old sandbox planted in the store. That attacker
  already holds the same sign-ins; a store kept is no worse than the sandbox kept.
- **A move's time on the stage is not measured here** (no live act from this change): its bound
  is the measured steps' (design 6). Measured at the first live move.
- **No test holds a whole move at the measured latencies**: the fake E2B answers at once, and a
  move at them runs about 24 s, past the workerd tests' budget, with no large leg of the
  sandbox's workerd tests. The bound test holds the bound against the steps as the measurements
  state them, and `carry-out`'s share is held past by a test (3 s).
- **The calls to the line** (`SandboxSlots`: `turn`, `freed`) are Durable Object calls, not cut
  by the deadline fetch; each is counted in the bound at a full request's time.
- **The 16 MiB cap and the isolate's 128 MB.** Durable Objects of one class may share an
  isolate, so two owners' objects can move at once in the same 128 MB. Each owner's moves run
  one at a time on its own chain, and a carry (the stores, kilobytes) is held only between its
  read and its write, at most an eighth of the isolate.

## What an owner sees

- A run on a sandbox of an older template runs there. The move happens afterwards, at the look,
  once the sandbox has been paused with nothing in it for half an hour, so a later run runs on
  the new image, from a paused sandbox resumed as any other. A start that comes during a move
  waits for it (at most about 36 s) and runs in the new sandbox.
- Signed-in engines stay signed in; the conversations resume.
- The run's first sync-in reads every file again (a new sandbox's own), so a large workspace's
  first run takes longer.
- The working files the sync-ignore keeps out (`node_modules`, build outputs, a custom line's
  paths) and the agents' scratch (clones, installs, caches) are gone; the agent rebuilds them. A
  file's mode changed alone is the owner's again.
- A move refuses nothing and loses nothing: one that cannot finish leaves the old sandbox paused
  with everything it had, and is tried again later; one whose workspace holds what no sync has
  sent waits for a run's end to send it.

## As built

- `sandbox/src/control.ts`: `#create` (the one create) and `#made` (its row, `template` among
  it); `#moveDue`, `#movesDue` and `#moveIdle`, after a look that failed at nothing in `alarm`,
  which `#arm` arms for a row due; `#needAt`, shared by `#arm` and the move; `#pausedAt`, the
  quiet window from each pause; `#resume` clearing `prepared` before `connect`; `#pauseIdle`;
  `#moveFailed`; `#moveBack`; `#settle`, run on the chain by `#ensure`, the look and the move;
  `#until`, the deadline every E2B and envd call is cut at during a move; `#own`, which sends
  `OWN_COMMANDS` to this Worker's runner alone; `roomForOne`; `moveBackoff`, `CREATE_MS`,
  `CARRY_OUT_MS`, `MOVE_STEPS_MS`, `MOVE_FINISH_MS`, `MOVE_MS`, `MOVE_QUIET_MS`,
  `MOVE_STUCK_TRIES`, `CARRY_MAX_BYTES`; `#create`'s token (`MOVE_TAG_METADATA`). `#ensure`
  moves nothing. The header says it. `src/schema.ts` migration 8 (`template`, `move_to`,
  `move_tag`, `move_tag_until`, `move_tries`, `move_after`, `retiring`), pinned.
- `sandbox/src/e2b.ts` `runtimeE2b(key, until)` and `sandbox/e2b/transport.ts` `fetchUntil` and
  `download` (a file read whole, its length checked).
- `sandbox/runner/protocol.ts` (`OWN_COMMANDS`, `CarryAnswer`), on #5860's record;
  `runner-speaks.json` 6.
- `sandbox/runner/runner.ts`: `carry-out` (refused while a job lives or the workspace holds what
  no sync has sent), `carry-in` and `carry-drop` (`CARRIED`, `ownTree`, `fileSha`, `untilOf`,
  `tarUntil`), each carry stopped at its deadline and leaving no archive past it; `syncAgent`
  taking the lapse it stops on; `session.ts` `GENERATIONS`; `sync.ts` `carryOut` (sync-out's
  own rule for what is unsent; the session rows), `carryIn`.
- Tests: `test/sandbox.test.ts` "a template move" (a start runs at once in a sandbox of another
  template, and the look moves it once it has been paused with nothing in it for its quiet
  window, the new one given the carry unprepared and prepared before its first launch; the bound
  against the measured steps and a start's lease; one that named no template moved once; a start
  that comes mid-move held on the chain and run in the new one, prepared first; a resume whose
  answer is lost marked unprepared and prepared by the next start, and paused again mid-move;
  five faults, each leaving the old sandbox paused and whole, the new one killed and the move
  backed off; every try counted and named stuck from its third; a carry-out past its share a
  counted try; a create whose reply never comes, nothing left unnamed; the old one's kill reply
  lost; one a move made that no row names, killed; no move without the line's turn and room, the
  try counted; a leased row arming no look; no move while another sandbox needs the look within
  the bound; none after a failed look); `runner.test.ts` "a template move's carry" (the real
  runner, as root: the sign-in, generation and session row carried, no workspace file, every
  mode the new runner's; refused while the workspace holds a changed file, a file never sent or
  a removal not sent, kept-out paths and unread rows aside; cut at its deadline mid-carry;
  refused while a job lives, past its deadline, of another carry's bytes, and into a sandbox
  with a store; the archive dropped).

- [ ] live: the stage owner's sandbox moves to the pin at the look half an hour after its next
  use once this deploys (envd takes the file read and write; the move's time and `carry-out`'s,
  against the workspace's file count, recorded here), and one `auto` pi run on an OpenRouter
  connection sealed with the Messages API then answers on pi 1.6.0.
