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

- **(a) Carry only what no sync carries**: the engines' sign-ins (which only the VM holds) and
  generations, and the mirror's session rows, which say what the owner's files hold of each
  store copy; not the workspace (what the stores' session copies are is the next section's).
  The new sandbox gets the workspace by its first sync-in. What the workspace holds unsent (a refused or
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
the workspace (`carry-out`'s walk) has its own share (`CARRY_OUT_MS`), a walk past it a counted
try. The new sandbox takes no `prepare` in the move: nothing runs in it but root's `carry-in`, as
a start's create installs before it prepares, and its next resume prepares it, as every resume
does.

## What the carry holds, and what waits on a move (research again, Fact 14: the review at 8006f717a)

Read on the head (`runner/session.ts` `makeHome`, `keepHome`; `sync.ts` `stageSessions`,
`fetchSessions`, `syncOut`; `fsafe.ts` `walkTree`; the four engine records):

- **The stores are not kilobytes.** Each engine's store holds its `secret_paths` (the sign-ins)
  and its `session_paths`, the full transcripts (`{home}/.claude/projects/`, `.codex/sessions/`,
  `.gemini/tmp/*/chats/`, `.pi/agent/sessions/`), kept after they are synced (`keepHome`; a store
  copy goes only when the owner's files changed it, `stageSessions`, or under a low disk,
  `sessionsRoom`). An owner with real history passes 16 MiB.
- **A resume already fetches a session the store lacks** (`fetchSessions`: a resume's files the
  mirror holds a row of, where the store holds no copy or a stale one), which is how the store
  recovers after a low disk lets copies go. So a copy the owner's files hold as it is
  (`synced`: the mirror's row its hash, the rule `stageSessions` uses to send none) need not be
  carried: its row is, and its resume fetches it. Only the copies the owner's files lack
  (refused, cut short, holding a secret's bytes) are carried.
- **What sync cannot hold, the walk meets and the old shape left behind**: links (`walkTree`
  passes them, sync-out sends regular files alone), names that are not UTF-8 (`unnamed`) and
  empty directories. Before a move the old sandbox kept them; a move must too, or say it. All
  three are tiny and `tar` holds each as it is (a link as a link, a name as its bytes), so they
  are carried as one archive the sync agent makes of them (`workspace.tar`), and put back by it.
  A socket or a device is passed over (`tar`), as no run's state.
- **The unsent check was a copy of sync-out's rule** for a row's removal, and lacked its client
  read-only clause. One function now says what sync-out does with a row (`rowOut`), for both.
- **The old sandbox's `prepare` is not needed in a move**: nothing of the owner's runs in it,
  only root's carry and the sync agent's walk; its row is marked unprepared before its resume
  is sent, and whatever next resumes it prepares it. Leaving it out (`#wake`, the resume's part
  before `prepare`) takes 7 s and a call off the bound.
- **What waits on a move**: every call on the owner's chain, any authority's. A start's lease
  holds the bound and its own steps (twelve calls, not nine: the settle's list, the slot given
  back and the line left among them). With the old side's `prepare` gone, the bound (26.2 s) is
  less than two resuming starts' own steps (27.3 s), and two resuming starts queued behind a
  move start within 53.5 s, inside a lease. A queue of three or more resuming starts overruns a
  lease whether or not a move is in it: that is the chain's, before this change. A cancel, a
  release or a re-apply of another authority's run waits at most the bound: a re-apply's first
  send then misses the lane's call budget (15 s, `LANE_CALL_MS`), and its retry lands.
- **The carry's deadline was on the guest clock**, which lags after a resume (`look-resume-loop.md`),
  and nothing stopped a carry-out the Worker gave up on. A carry command now takes a budget in
  ms, counted on the runner's own monotonic clock (`performance.now()`, timers), a request's
  transit less than the Worker's own cut; and a carry-out under way writes its pid with this
  boot's id (the lock's form), so `carry-drop` and `prepare` stop it, its sync agent with it,
  even one a pause froze.

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
   and generations, less each session copy the owner's files hold as it is (`synced`, by the
   mirror's rows, the rule `stageSessions` sends by); the mirror's session rows, its cursor 0;
   and what of the workspace no sync can hold, its links, empty directories and entries whose
   names are not UTF-8, as one archive (`sync.ts` `carryOut`). Refused while a job lives or the
   workspace holds what sync-out would send or remove (`differs`, `rowOut`: sync-out's own). The
   first sync-in in the new sandbox reads the workspace's files from the owner's files, and each
   session's resume fetches it. Not carried: the workspace's files, the sync-ignore's kept-out
   paths and the agents' scratch (the owner's files', the working copy's own, a cache), sockets
   and devices, the caches, the finished jobs' state.
4. **The protocol**: three Worker commands, sent to this Worker's own runner alone (`#own`):
   `carry-out <authority> <ms>`, `carry-in <sha256> <ms>` and `carry-drop`, protocol 6's
   (#5860, which the stage serves), its record naming the commands, the carry's path and
   `carry-out`'s answer, all as before (it records no argument); this change moves the runner to
   6. Each carry command stops within its budget, on the runner's own clock, and leaves no
   archive past it; `carry-drop` and `prepare` stop a carry-out under way (`CARRY_PID`). **No rollback of the sandbox Worker past this
   deploy**: migration 8 moves `SandboxControl`'s lock to 8, and the kit refuses a rollback to a
   lower `-m` (`deploy/deploy.ts` `forwardOnly`).
5. **The Worker's steps** (`#moveIdle`, on the chain, one move a look, every E2B and envd call
   cut at `#until`: `MOVE_MS` less the finish's share, `MOVE_FINISH_MS`, for the steps, and the
   finish's after, so the whole move ends by `MOVE_MS`):
   - the try charged (`move_tries`, `moveBackoff`: a lease's TTL, doubling, at most a day)
     before anything is sent, and never given back;
   - the old one woken (`#wake`: unprepared before `connect` is sent; its runner made this
     Worker's where nothing lives; not prepared, nothing of the owner's running in it);
   - `carry-out`, cut at its own share (`CARRY_OUT_MS`) here and a request's transit sooner in
     the sandbox, read whole, the archive dropped there (`carry-drop`), the old one paused
     again;
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
6. **The bound** (`MOVE_MS`, 26,222.5 ms): the steps at their measured worst (`MOVE_STEPS_MS`:
   the old one's resume 895 ms, two pauses at 705 ms, the create 1,078 ms, `carry-out`'s 3 s
   share, and 20 other calls at the slowest full request, 480 ms: info, the line's turn and
   E2B's count; the resume's network, `state`, an adoption's upload, install and `adopt`, and
   the slot given back;
   the carry's read and `carry-drop`, the line rung; the new one's upload and install, the
   carry's write, `carry-in`, info, the line rung; the kill, the line rung: 15,983 ms), and the
   finish (`MOVE_FINISH_MS`: three pauses and six calls, 4,995 ms), with a fifth of the bound to
   spare. `carry-out`'s budget in the sandbox (2,520 ms) holds twice the measured walk of
   100,000 files (1,050 ms) and the pack of a carry at its cap (16 MiB packed and hashed in 50 ms,
   this session's Linux host). A resuming start's own steps are a resume, a `prepare` and twelve
   calls (13,655 ms): one behind a move starts within 39.9 s, two within 53.5 s, inside a lease's
   60 s.
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

## Review findings (#5857, at 20c662fbc), with the fix above each

- **What the carry puts back takes sync-in's own modes** (`syncModes`, after the extract: a
  folder `02775`, a file `0664` or `0775`, a link as it is), so a run user writes into a carried
  link's folder and a carried empty folder as before: the first carry test does, as a run user
  of group `ta`. Dropping `syncModes` fails it.
  - `carryIn`'s `tar` ran as `ta-sync` under root's umask (022): folders `02755`, files `0644`,
    which no run user writes, and sync-in's `safeDirs` keeps a folder's mode.
- **What the sync agent may not read is passed over, as sync-out passes it**: the walk tells a
  folder it may not list (`Walk.unlisted`), so it is never taken as empty; a named file or an
  unnamed entry it may not read is not counted unsent nor packed; and `tar` passes over what it
  may not read under an unnamed tree (`--ignore-failed-read`). "passes over what the sync agent
  may not read" moves a run user's 0700 folder, its 0600 file, an unnamed 0600 file and an
  unnamed folder holding one, carrying the rest. Dropping the readable check, or `tar`'s pass,
  fails it.
  - A run user's `0700` folder (`mkdir -m 700`, `mkdtemp`, a gpg home) was taken as empty, and
    the carry's `tar` failed on it at every try.
- **The slot a move's wake owes is given back in the move** (Important 3, the red CI): below.
- Nits: `stopCarry` kills the carry-out's own `tar` before it, and only a carry-out whose start
  is the one its file names ("never stops another process that took a stale carry-out's pid");
  `carry-out` keeps what it hashed (`keepSeen`), so a try cut short hashes no copy twice, and a
  cold cache (a deploy that changes the state format) costs one try's budget a store's hashing;
  the low-disk let-go uses `synced` too (`sessionsRoom`); "Left" names the unreadable files.

## CI at 20c662fbc, with the fix above it

- **The move gives back the slot its wake owed** (`#giveBack` after `#wake`, as `#resume` does),
  so the look after it arms the token's window alone: "leaves no sandbox unnamed when the
  create's reply never comes" now also holds that nothing is owed.
  - Splitting `#wake` out of `#resume` left its owed slot ungiven, so the look after every move
    armed again at once (a probe of `#arm`: "armed in 0 ms, owed 1", then, fixed, "armed in
    81214 ms, owed 0"). That extra look gave the slot back and armed the window, but the test's
    read of the alarm could land while it ran (null on CI; 2 of 12 runs locally under load, 0 of
    24 once fixed).

## Review findings (#5857, at 8006f717a), with the fix above each

- **The carry leaves out each session copy the owner's files hold** (design 3): "leaves out each
  session copy the owner's files hold, however large, for its resume to fetch, and carries the
  one they lack" moves a store past 16 MiB, its carry under 1 MiB, and resumes the session from
  the owner's files. Carrying every copy fails it.
  - `CARRIED` was the stores whole, every transcript with them: an owner with history passed the
    cap or the share at every try, forever.
- **One rule for a row sync-out would remove** (`rowOut`, used by `syncOut` and `carryOut`):
  "carries a client's sandbox whose read-only tree's file is gone, which no sync-out removes".
  Dropping the client clause fails it.
  - `carryOut` copied the rule and lacked `clientReadOnly`, so a client agent that moved its
    read-only tree away left its sandbox refused forever.
- **What no sync holds is carried** (design 3): the first carry test moves a link (as a link,
  its target kept), an empty directory and a name that is not UTF-8. Dropping the empty
  directories fails it.
  - Links, unnamed entries and empty directories were dropped without a word.
- Nits:
  - a start's own steps recounted at twelve calls, and two resuming starts behind a move bounded
    (53.5 s), the old side's `prepare` left out to fit (`#wake`); the bound test holds two;
  - a carry-out under way is stopped by `carry-drop` and `prepare` (its pid with this boot's id),
    and every carry budget runs on the runner's own clock ("stops a carry-out under way at
    carry-drop");
  - another authority's cancel, release and re-apply wait at most the bound, said (header,
    research);
  - the share and its test agree: the budget holds twice the measured walk and the pack.

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
  `carry.pid` was gone with the cut (back, with the boot's id, at 8006f717a).

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
- **What the sync agent may not read is not carried**: a run user's own `0700` folder (a gpg
  home, a `mkdtemp`) and its `0600` files, named or not, which no sync-out sends either (it skips
  them, `gone`). They are that run user's alone, and go with the old sandbox, as at its loss.
- **A workspace whose walk outlasts `carry-out`'s budget** (some 235,000 files at the measured
  rate) never moves, each try counted, named stuck from the third. Its time on E2B is owed to
  the `live:` wait below.
- **Session copies the owner's files lack past the cap** (16 MiB of turns refused, or cut short,
  at every run's end) hold the move, each try counted, named stuck from the third, until a run's
  end sends them.
- **A cold sessions cache** (a deploy that changes the state format drops `sessions-seen.json`)
  makes `carry-out` hash every store copy once; a try cut short there keeps what it hashed
  (`keepSeen`), so the next hashes on from it.
- **Sockets and devices in the workspace** are not carried: `tar` passes over a socket, and
  neither is a run's state.
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
  move at them runs about 17 s, past the workerd tests' budget, with no large leg of the
  sandbox's workerd tests. The bound test holds the bound against the steps as the measurements
  state them, and `carry-out`'s share is held past by a test (3 s).
- **Three or more resuming starts queued on one owner's chain** overrun a lease, with a move in
  the queue or without: a move holds the chain less than two resuming starts do. The chain is
  one per owner, before this change.
- **The calls to the line** (`SandboxSlots`: `turn`, `freed`) are Durable Object calls, not cut
  by the deadline fetch; each is counted in the bound at a full request's time.
- **The 16 MiB cap and the isolate's 128 MB.** Durable Objects of one class may share an
  isolate, so two owners' objects can move at once in the same 128 MB. Each owner's moves run
  one at a time on its own chain, and a carry (the sign-ins, the unsent session copies, the
  workspace's links, empty folders and unnamed entries) is held only between its read and its
  write, at most an eighth of the isolate.

## What an owner sees

- A run on a sandbox of an older template runs there. The move happens afterwards, at the look,
  once the sandbox has been paused with nothing in it for half an hour, so a later run runs on
  the new image, from a paused sandbox resumed as any other. A start that comes during a move
  waits for it (at most about 26 s) and runs in the new sandbox.
- Signed-in engines stay signed in; the conversations resume, each fetched from the owner's
  files at its first resume.
- The workspace's links, empty folders and files whose names are not UTF-8 come across, with
  the modes sync-in gives (a run user writes into them as before).
- A folder or file of a run user's own that the sync agent may not read (`0700`, `0600`: a gpg
  home, a temporary folder) does not come across, as no sync sends it.
- The run's first sync-in reads every file again (a new sandbox's own), so a large workspace's
  first run takes longer.
- The working files the sync-ignore keeps out (`node_modules`, build outputs, a custom line's
  paths) and the agents' scratch (clones, installs, caches) are gone; the agent rebuilds them. A
  file's mode changed alone is the owner's again.
- A move refuses nothing and loses nothing but what the sync-ignore keeps out, the agents'
  scratch, a mode alone, sockets and what the sync agent may not read (above): one that cannot finish leaves the old sandbox
  paused with everything it had, and is tried again later; one whose workspace holds what no
  sync has sent waits for a run's end to send it.

## As built

- `sandbox/src/control.ts`: `#create` (the one create) and `#made` (its row, `template` among
  it); `#moveDue`, `#movesDue` and `#moveIdle`, after a look that failed at nothing in `alarm`,
  which `#arm` arms for a row due; `#needAt`, shared by `#arm` and the move; `#pausedAt`, the
  quiet window from each pause; `#wake` (a resume's part before `prepare`, clearing `prepared`
  before `connect`) and `#resume` (`#wake`, then `prepare`); `#pauseIdle`; `budget`;
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
  no sync has sent; each synced session copy left out), `carry-in` and `carry-drop` (`CARRIED`,
  `CARRY_PID`, `stopCarry` (`startOf`, `childrenOf`), also at `prepare`; `ownTree`, `fileSha`,
  `withinOf`, `tarUntil`),
  each carry stopped within its budget on the runner's own clock and leaving no archive past
  it; `syncAgent` taking the lapse it stops on; `session.ts` `GENERATIONS`; `pid-lock.ts`
  `liveHolder`; `sync.ts` `carryOut` (sync-out's own rules for what is unsent, `differs` and
  `rowOut`, and what it may read; the session rows; the workspace's links, empty folders and
  unnamed entries), `carryIn` (`syncModes`); `fsafe.ts` `Walk.unlisted`;
  `synced` (`stageSessions`' rule) and `heldOf`.
- Tests: `test/sandbox.test.ts` "a template move" (a start runs at once in a sandbox of another
  template, and the look moves it once it has been paused with nothing in it for its quiet
  window, the old one never prepared in it, the new one given the carry unprepared and prepared
  before its first launch; the bound against the measured steps and two resuming starts in a
  lease; one that named no template moved once; a start
  that comes mid-move held on the chain and run in the new one, prepared first; a resume whose
  answer is lost marked unprepared and prepared by the next start, and paused again mid-move;
  five faults, each leaving the old sandbox paused and whole, the new one killed and the move
  backed off; every try counted and named stuck from its third; a carry-out past its share a
  counted try; a create whose reply never comes, nothing left unnamed; the old one's kill reply
  lost; one a move made that no row names, killed; no move without the line's turn and room, the
  try counted; a leased row arming no look; no move while another sandbox needs the look within
  the bound; none after a failed look); `runner.test.ts` "a template move's carry" (the real
  runner, as root: the sign-in, generation and session row carried, a link, an empty folder and
  a name that is not UTF-8 carried, no workspace file, every mode the new runner's; a store past
  16 MiB of synced sessions carried under 1 MiB, the one unsent copy carried, and the resume
  fetching the other from the owner's files; refused while the workspace holds a changed file, a
  file never sent or a removal not sent, kept-out paths and unread rows aside; a client's
  read-only tree gone carried, the owner's refused; cut within its budget mid-carry; a carry-out
  under way stopped at `carry-drop`, its sync agent with it; refused while a job lives, with no
  budget, of another carry's bytes, and into a sandbox with a store; the archive dropped).

- [ ] live: the stage owner's sandbox moves to the pin at the look half an hour after its next
  use once this deploys (envd takes the file read and write; the move's time and `carry-out`'s,
  against the workspace's file count, the store's size and the carry's bytes, recorded here),
  and one `auto` pi run on an OpenRouter
  connection sealed with the Messages API then answers on pi 1.6.0.
