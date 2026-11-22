# session-store-pressure as built (2026-11-22)

Owed by `fact15-defaults-2.md` (#5792): every engine now keeps its sessions as long as the
owner's files do, so an engine's store can grow until the sandbox's disk (14.4 GB) is full, and
the only sign was the engine's own `ENOSPC` once no scratch was left to free. The owed line asks
for a loud runner event naming the store's size and the disk's, and a store copy let go that is
fetched again before a resume; and Codex's compressed second copy. It also carries what was owed
as `untangle/runner-protocol-5`: the start document's `scratch_retention_days`, written null.

## Research (Fact 14), read 2026-11-22 on main `afad840d6`

**What the sandbox's disk holds of the engines' sessions today** (`runner.ts`, `session.ts`,
`sync.ts`):

- **The store** (`/var/lib/ta/store/<engine>`, root's): every session a run of this sandbox made
  (`keepHome` writes the home's session files back) and every one sync-in brought.
- **The sync agent's `in/`** (`/home/ta-sync/in/<engine>/...`): sync-in fetches every session
  path the change feed names into it, and `importSessions` copies one into the store only where
  the store lacks it. Nothing ever removes it. The feed names this sandbox's own writes too
  (`files.ts` `changes` filters no writer, and `apply` fetches whatever the feed names), so one
  sync-in after each sync-out the whole of what was sent comes back: `in/` is a second copy of
  every session, and each transcript crosses the network twice.
- **Each run's home**: `makeHome` copies every session file of the engine's store into it, so a
  run holds a third copy while it runs (a fourth with two runs of one engine at once).
- **Staging out**: only the sessions the owner's files lack, removed once sent.

So sessions take about three times their size at a run, and a fresh sandbox (a reset of the
feed) fetches the owner's whole history before its first run: past the disk, the sync agent's
own write fails and no run starts. The owner's storage (25 GB free, 250 GB paid) does not bound
this disk.

**What a resume reads** (the pinned packages and their sources):

- **Claude Code 2.1.309**: `--resume <sid>` loads the transcript
  `projects/<project>/<sid>.jsonl` (missing: "No conversation found with session ID"); a
  session's subagents' transcripts and tool results sit in `projects/<project>/<sid>/`
  (`fact15-defaults-2.md`'s cleanup research).
- **Codex 0.166.0**: `exec resume <sid>` looks the thread up in its state database, which is
  outside `session_paths` and so never in a run's home, then by file name under `sessions/`:
  `YYYY/MM/DD/rollout-<timestamp>-<thread id>[_<rollout id>].jsonl[.zst]`, the newest of a
  thread's (`codex-rs/rollout/src/list.rs` `find_thread_path_by_id_str`).
- **Gemini CLI 0.66.0**: `--resume <sid>` lists every chat of the project
  (`tmp/<project>/chats/session-<time>-<first 8 of sid>.jsonl`, subagents'
  `chats/<sid>/...`) and picks the one whose content names the id (`SessionSelector.findSession`).
  Its file names hold only 8 characters of the id, so a resume reads the project's chats.
- **pi 1.4.0**: `--session <id>` opens `sessions/--<cwd>--/<timestamp>_<id>.jsonl`
  (docs/session-format.md, docs/cli.md).

**Codex's compressed second copy cannot arise in a sandbox.** Codex compresses a rollout whose
modification time is 7 days old (`compression.rs` `MIN_ROLLOUT_AGE`, `cold_file_state`). Every
file of a run's home is written fresh by `makeHome` (`safeWrite`, no time kept), and no run lives
7 days, so no rollout in a home is ever old enough. The same holds for every engine's own age
rule (Claude Code's cleanup reads the transcript's time too).

**What a resume needs to be fetched again.** The change feed moves a cursor, so a store file let
go never comes back by it. Only a fetch by path does (`sync/read`, `fetchInto`). To fetch the
right files the runner needs which store files a resume of `{sid}` reads: an engine difference,
so the record's (Direction 2), as `resume_argv` already is.

**The runner protocol.** `scratch_retention_days` is protocol 4's start-document field; no
runner reads it (`fact15-defaults-2.md`). Nothing else in this change crosses the Worker and the
runner: the sync agent's jobs and the events are the runner's own. So protocol 5 is protocol 4
less that field. The two-deploy rule (`protocol.ts`) keeps a run that lives across a deploy, or
its rollback, on a Worker that speaks its runner's protocol. No run can today: the routes are
not switched and no owner has a sandbox (`runner-protocol-contract.md`, the same day); the
deploy's pair check runs throwaway sandboxes. Either way a Worker meeting a runner it does not
speak adopts its own where nothing lives, and refuses a start by name where a run does
(`control.ts` `#speaking`). The forward list (`deploy/platform.json`) marks a change a version
before cannot follow; a rollback here meets only that loud refusal, so it takes no entry.

## Decided

1. **The store is a cache of the owner's sessions, fetched by name.** Each CLI record names the
   store files a resume of `{sid}` reads (`cli.resume_paths`, beside `session_paths`, each
   under one of them; `*` any run of characters within a segment, a trailing `/` a tree):
   - Claude Code `{home}/.claude/projects/*/{sid}.jsonl` and `{home}/.claude/projects/*/{sid}/`;
   - Codex `{home}/.codex/sessions/*/*/*/rollout-*-{sid}*`;
   - Gemini CLI `{home}/.gemini/tmp/*/chats/` (its resume lists the project's chats);
   - pi `{home}/.pi/agent/sessions/*/*_{sid}.jsonl`.
   One matcher (`engine.ts` `isKept`) reads every path list, the sid literal.
2. **A run's home holds what it resumes, never the whole store** (`makeHome`'s `session`): a new
   conversation's none. The route probe's second turn resumes from such a home, live, at every
   template pair check.
3. **Sync-in fetches no session.** A session path the feed names at a version the mirror does
   not hold is marked in the mirror as changed in the owner's files (`UNREAD`). A resume asks
   the sync agent for its files (`fetch`): every one the mirror knows under the resume's paths
   that the store lacks or that changed since, into `in/`, which `importSessions` empties into
   the store. A store copy whose owner's version changed is dropped at staging, never sent over
   it. **A change at the version the mirror holds is no change**, for every path: this sandbox's
   own writes are not fetched back.
4. **Disk pressure lets store copies go after scratch** (`scratch.ts` `letGoSessions`): while
   the disk is under the low water, the store's sessions the owner's files hold as they are go,
   least recently changed first, never one this run resumes. They come back by a resume's
   fetch. If the disk is still low, the run's events say so, loudly (`ta.disk_low`: the store's
   bytes, the disk's free and total), and the app in words; a full disk is the same on every
   tier, so never an upgrade.
5. **Protocol 5** is protocol 4 less `scratch_retention_days`, pinned. The runner speaks 5 and
   the Worker 5 alone, in one deploy, which `protocol.ts`'s rule now says when it may (no run
   can live across it). The start document no longer carries the field.

## Review (2026-11-22)

- The route probe's fake CLIs (`runner.test.ts` `honest`) kept their conversation at a
  `history.txt` no resume reads, so the large route-probe tests failed once a home held the
  resume's files alone (CI's runner sizes leg); each keeps it where its record's
  `resume_paths` name.
- The let-go (`runner.ts` `sessionsRoom`) read the live runs before its `known` call and deleted
  without the stores' locks: a run started meanwhile could resume from a home missing its own
  file, and a home kept meanwhile (a newer copy, unsent) could go. It deletes under every
  store's lock, the live runs read again, each copy held to the size and time it was hashed
  at; a resume reads which of its files the store holds under its store's lock.
- A fetch the sync agent did not answer stops the run by name (`storeResume`).

## Found and left

- **A staged session sent after a sync-in marked it changed**: sync-out's base is the mirror's
  version, the owner's newer one, so the staged copy lands over it. The same held before this
  change (a fetched row's version); it needs two writers of one transcript, as above.
- **A rebooted sandbox whose runner speaks 4** (a filesystem pause, which `#speaking` never
  adopts over) is refused by a Worker speaking 5 for as long as it lives. No owner sandbox
  exists before the cutover's routes, and the pair check's are killed.
- **An owner's edit of a transcript while its sandbox holds unsent changes to it**: a resume
  fetches the owner's version over the store's. Before, the store's was sent over the owner's
  as a conflict. Both need two writers of one transcript (an edit through the files door while
  a sync-out failed); the owner's files win, as they are the durable copy.
- **The sync agent's `in/` that an older runner filled** stays where it is: no owner sandbox
  exists to hold one (above), and a throwaway pair check's is gone with it.
- **The deploy guards read the live forward list** (`deploy/guards/deploy.test.ts`, through
  `facts.ts` `FORWARD`): its first entry, whichever change adds it, rewrites eleven of their
  expected tags and rollback lines. Found trying one here; this lane takes none, so it is that
  change's to meet.
- **The workspace itself** (`/workspace`, the owner's files) is on the same disk and is bounded
  by the owner's storage, not the disk: `ta.disk_low` names the disk's free space whatever
  fills it.

## Owed

- [ ] `live:` the route probe's resumed turn, at the next template pair check, from a home made
  with the resume's files alone (`resume_paths`), for every engine that resumes.
