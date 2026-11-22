# sync-stage-race as built (2026-11-22)

Found in the #5794 review (`session-store-pressure.md`, its first "left" entry): a session
staged for sync-out, then marked changed in the owner's files (`UNREAD`) by a sync-in before
that sync-out, was sent with the owner's newer version as its base, so it landed over the
owner's edit, silently (Fact 8). It needs two writers of one transcript, and it predates #5794.

## Research (Fact 14), read 2026-11-22 on main `99d42771a`

**How sync-out picks its base** (`sync.ts` `syncOut` `offer`): the mirror's row of the path,
`base: known?.version ?? null`, read when the file is offered. Every row is written with the
version of the bytes it describes: a fetch's (`apply`, `fetchSessions`), a commit's answer, a
reset's listing as fetched. `state-format.ts` calls the mirror "each path's base version". One
writer broke it: `unread`, which put the owner's new version on a session's row while this
sandbox held, and had read, none of that version's bytes. The reset's own `vanished` marked a
session `UNREAD` at the version the row already held, so the two marks disagreed.

**The race, as the runner runs it** (`runner.ts`): at a run's end the root runner asks the sync
agent what the mirror holds (`known`, a job under the sync agent's lock), stages each session
the owner's files lack as it is (`stageSessions`), then runs sync-out (`out`, another job under
the lock). Between the two jobs another run's sync-in (`in`) can take the lock. A staged copy
whose row the mark rewrote went out with the owner's version as its base.

**What the owner's commit does with a stale base** (`owner/src/files.ts` `#syncOp`): a write
against a version that moved on, or against no base where the path exists, is a conflict. The
owner's version stays, the sandbox's content is saved beside it under a new conflict name
(`<name>.ta-conflict-<epoch>[-n]`), and the outcome is `conflict`, which the run reports
(`ta.sync_conflicts`). Nothing is dropped and nothing is silent: the rule is already built, for
every path. The owner could not see this race because the base it was sent was its own.

**Ordinary workspace files:** sync-out reads the live file (no staging), and its base is the
row that sync-in wrote with the bytes it put in place, so a workspace file's base is always the
version its bytes came from. The stale base cannot arise. The reverse can (below, left).

**The shapes weighed:** the owner refusing a stale base is what it does already; it was never
sent one. A conflict copy is the owner's existing outcome. The base being the version the copy
came from is the one missing piece, and it is the rule every other row already keeps. Dropping a
staged copy whose row became `UNREAD` would be a session-only branch in sync-out, and would lose
the run's turn silently.

## Decided

1. **A mirror row's version is the version this sandbox last read or wrote, for every path,**
   the base its next write is offered against. Marking a session changed in the owner's files
   (`unread`) blanks its hash (`UNREAD`) and keeps the row's version. A copy staged before the
   mark is a conflict at the owner's files, kept beside their newer edit and reported, never
   over it. A path with no row takes the version the feed names: this sandbox holds no older
   copy of it (its sessions are named by its own runs' ids).
2. **The sync tests' stand-in owner checks bases as the owner's object does** (`feedOf`'s
   commit: a moved base is a conflict copy), so the race test reads the owner's edit and the
   conflict copy, not the base sent. The test runs the sync agent's three jobs in the race's
   order (a sync-out, the owner's edit, the staged turn, another run's sync-in, this run's
   sync-out). The real interleaving is across two jobs of one lock, and the decision is the
   row's version alone, so no gate or hook is needed.

## Found and left

- **A store copy already `UNREAD` at staging is dropped** (`stageSessions`, #5794's decision:
  the owner's files win), so the same two writers end in a drop or a conflict copy depending on
  whether the sync-in came before the staging or after it. Sending that copy as a conflict
  needs to know whether it holds unsent changes, and the mark blanks the hash it was read at.
  The mirror cannot hold both the read hash and the mark without a state-format change
  (`state-format.ts`, two deploys).
- **A sync-in replaces a workspace file holding unsent changes** (`apply` fetches over it
  without comparing it to the mirror's identity): a file refused at a full storage and kept in
  the sandbox, or one a second run edits, is lost there silently when the owner edits it
  meanwhile. Keeping it and letting its sync-out conflict needs the mirror to fetch the owner's
  version after the conflict, or every later sync-out makes another copy. That is a new shape
  for workspace sync, so it gets its own research.

## Owed

- [ ] `untangle/sync-in-unsent-edit`: a sync-in never replaces a workspace file holding
  unsent changes silently (research first: keep it and conflict, then fetch the owner's).
