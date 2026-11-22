# fact15-defaults-2 as built (2026-11-22)

Owed by `fact15-defaults.md` now that `control-last-good.md` has landed: the control record's
`scratch_retention_days` leaves the record, and Claude Code's pinned `cleanupPeriodDays: 30`
is researched, with every other engine record's own session retention. Fact 15: the only
limits are the tier's runs and storage, the owner's own settings (off by default), vendors'
limits and recorded technical ceilings, each with its reason. Call 11: a retention is an owner
setting, off keeping for ever, unless a ceiling is recorded with its derivation.

## Research (Fact 14), read 2026-11-22 on main `205aafae2`

**Scratch's age.** `scratch_retention_days` was read by `contract/src/control.ts`
`parseControl` (an optional key), handed by `sandbox/src/control.ts` `#control` into each start
document (`startDocument`), and read by the runner's `scratchFor` into `pruneScratch`, whose
`expired` agents the app's `scratchWords` said ("Some working files had not been used for a
long time"). `known_retention` was already dropped (`control-last-good.md`, migration 4). The
dark record (`deploy/control.json`) holds no such key, and no doc or host action records the
founder setting one. Scratch is a cache on the sandbox's own disk, never synced, never counted
in the owner's storage, and already let go under disk pressure (`LOW_WATER_SHARE`). An age bounds
nothing the disk does not, refuses nothing, and is neither the tier's nor the owner's: none of
Fact 15's kinds.

**The runner protocol.** The start document is protocol 4's (`protocol.json`, frozen), which
names `scratch_retention_days: number|null`. A protocol 4 runner in a sandbox that outlived a
deploy reads it, `?? null`. Dropping the field is a protocol change (two deploys and a contract,
`runner-protocol-4.md`).

**Each engine's own session retention**, read in the pinned packages (npm tarballs of the
records' versions) and their vendors' pages:

- **Claude Code 2.1.309.** `cleanupPeriodDays` defaults to 30; 0 fails validation, the minimum
  is 1, and its own hint says "set a large number (e.g. 3650 for ~10 years)" (the bundled
  settings schema: `int().positive()`). At each start a background sweep deletes every
  transcript under `projects/` (its subagents' transcripts and tool results with it) older
  than the period: the cutoff is `new Date(Date.now() - days * 24 * 60 * 60 * 1000)`
  (code.claude.com/docs/en/settings-reference, `cleanupPeriodDays`, and claude-directory,
  "Cleaned up automatically"). The sweep is skipped only when the user layer is off and no
  other layer names the key, or a settings file fails to parse. The pin
  (`cli.decide.pins`, `{home}/.claude/settings.json`) was written only on a hooked run, whose
  point was that layer's ownership (`r5-hooks.md`): an unhooked run had no user layer and
  deleted at 30 days all the same. What a held call resumed late needs is its transcript in
  the engine's store: staging out sends only files the store holds (`stageSessions`) and never
  a deletion, so the owner's files keep the synced copy, but the store a sandbox resumes from
  had lost it.
- **Gemini CLI 0.66.0.** `general.sessionRetention` defaults to enabled, `maxAge` "30d"; at
  each start, interactive or not, `cleanupExpiredSessions` deletes chats (and
  `cleanupToolOutputFiles` tool outputs) older than that (`bundle/gemini-*.js`;
  docs/cli/session-management.md). `enabled: false` turns both off.
- **Codex 0.166.0.** No session retention: its rollout crate has no cleanup; its maintenance
  worker compresses a rollout older than 7 days in place and deletes the plain copy
  (`codex-rs/rollout/src/compression.rs`), so the session stays, under a new file name.
- **pi 1.4.0.** No session retention: a session file is deleted only from the interactive
  session picker (`dist/bundle/chunks`, `deleteSession`); compaction keeps the entries
  (docs/sessions.md).
- **Kimi** has no engine record (`sources/records/`), so nothing to read.

**The held card's life.** `fact15-caps.md` deleted `cardTtlS`, which had rested on the 30 days:
a held call waits until its answer or its authority ends. A late yes's resume now rests on the
run's receipt and conversation (`RUNS_KEPT`, `CONVERSATIONS_KEPT`, `Runs.resume` answering null
when either is gone) and on the engine's session, which this change keeps.

## Decided

1. **Scratch has no age.** `scratch_retention_days` leaves the control record (`Control`,
   `parseControl`'s keys), the sandbox Worker's control read (`#control` answers the numbers
   alone; `SandboxPart` goes), the runner's pruning (`pruneScratch`'s retention, `Pruned` and
   `ScratchCleared`'s `expired`) and the app's words (one reason left, the disk). No owner
   setting replaces it: a setting nobody asked for that empties a cache would be a feature with
   no reader (`fact15-defaults.md` decision 2).
   - **The start document keeps `scratch_retention_days: null`**, protocol 4's own field, which
     a protocol 4 runner reads as no age; this runner reads it not at all. It leaves with the
     next runner protocol, rather than three PRs of expand and contract for a field no runner
     acts on.
   - **A live record still holding the key is refused, never read past.** Every reader reads it
     malformed and closes every gate (Fact 8), the kit's `control` mode names it and takes only
     `--replace`, and now a dark or routes run stops on any held record no reader takes, naming
     it (`deploy.ts` `control`); its plan job reads the record too, so it stops before any
     change. Before, the dark run checked only that one existed, so a record every gate read
     closed passed it silently. No migration: the record is KV, and the founder edits it.
   - **The live record holds no such key** (review, 2026-11-22, from the code and CI's own
     logs, KV unread): its only writers are the dark run's `deploy/control.json`, which never
     held the key, and the `control` mode, which no `platform-deploy.yml` run has used. The
     record was written once, by the dark run of 2026-11-06 (`f531a4fe3`), as the
     `control.json` of today, which every reader takes; each dark run since left it.
2. **Every engine's own session retention is off, on every run.**
   - **Claude Code:** `{home}/.claude/settings.json` moves from the hooked run's `decide.pins`
     into the record's `files`, written root's on every run (`configOwned`), holding
     `cleanupPeriodDays: 100000000`. That is the largest whole number of days the CLI's
     arithmetic holds as a date at every time since the epoch: ECMAScript's time values span
     8.64e15 ms, 100000000 days, either side of the epoch, so `now - days` stays a valid date
     before any file, and 8.64e15 ms stays under 2^53. A smaller round number (3650, the CLI's
     own hint) would be a chosen age. A hooked run's user layer is the same root file as before,
     so `--setting-sources user` still loads no layer the agent writes (the runner test that
     holds it is unchanged). On an unhooked run a project or local layer in the owner's files
     outranks the user layer and may name a shorter period: that is the owner's own setting
     (Fact 15), and nothing it guards, since the run's user can delete its own transcripts.
   - **`decide.pins` goes** (schema, check, engine): its one use is now a file of every run.
   - **Gemini CLI:** `general.sessionRetention.enabled: false` in its system settings, the
     record's `mcp.template` that every run writes (beside `disableAutoUpdate`), the highest
     layer, which a hooked run's decide file merges into.
   - **Codex and pi:** nothing to pin.
3. **`limits-audit.md`:** the "Not yet in one of the kinds" list goes; the retentions section
   says each engine's setting and its derivation, and that scratch has no age.
4. **The spec** (`doors`): scratch is never deleted for its age alone; a CLI record turns its
   CLI's own session cleanup off on every run, with a scenario for a late resume.

## Found and left

- **The sandbox's engine store holds every session synced in, on the sandbox's disk.** With no
  engine pruning (as Codex and pi already were), a heavy owner's transcripts can outgrow the
  sandbox's 14.4 GB, which the owner's storage (25 GB free, 250 GB paid) does not bound. Letting
  a store copy go needs the sync to fetch it again before a resume (sync-in is by change feed,
  so a deleted store file never returns): its own research
  (`untangle/session-store-pressure`).
- **Codex's compression leaves two copies in the owner's files.** The plain rollout stays
  synced and the compressed one is synced beside it, since a deletion never syncs out. The
  owner's storage counts both. Same branch, since both are the store's mirror of the owner's
  files.
- **`r5-hooks.md` and `r5-decide.md` name the pin** as it was; decision logs are history.
  `subscription-engines.md` and `engines-replan.md` record Claude Code's 30-day default, still
  the vendor's fact.

## Owed

- [x] The start document's `scratch_retention_days`, written null, leaves with the next runner
  protocol (`untangle/runner-protocol-5`). Done: protocol 5, in `untangle/session-store-pressure`
  (`session-store-pressure.md` decision 5).
- [x] The engine store's size on the sandbox disk, and Codex's compressed second copy
  (`untangle/session-store-pressure`): until it lands, a disk the store fills shows only as
  the engine's own `ENOSPC` once no scratch is left to free, so it owes a loud runner event
  naming the store's size and the disk's, and a store copy let go that the next sync-in
  fetches again. Done (`session-store-pressure.md`): `ta.disk_low`, the store's copies let go
  under pressure and fetched by a resume; Codex's second copy cannot arise in a sandbox.
- [ ] `tasks.md` Step 5's call-11 line stays for `untangle/limits-guard`, which lands after this.
