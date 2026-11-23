# owner-control-signins as built (2026-11-23)

`untangle/owner-control-signins` takes the item `control-row.md` owed: the sign-in switches'
half of the fold. #5805 made the binding carry the control record (`Binding.control`) and the
owner keep it in `control_kept`, its tiers read from there. Here the binding's `signins` and the
owner's `platform_signins` go, and the owner reads its switches from the same kept record.

## Research first (Fact 14), read on main cf007a867

- **What each switch protects.** A source's vendor sign-in inside a sandbox (`cli.login`) is
  hosted use of the vendor's subscription; each record cites its verdict on the vendor's terms
  (`hosted_use.verdict`), and the record's `signins` lets the founder switch one on or off with
  no deploy. The owner holds three acts to it: a sign-in's start (`signinStart`, mode `in`), a
  pasted code (`signinCode`), and a run on the vendor's login (`runs.ts` `choose`, named or by
  `auto`). A sign-out sits behind none, so a login is always removable.
- **Who wrote the switches, and how.** `signinsRead` (`sources/src/signin.ts`) turned a read
  into a list: a well-formed record its switches (else each record's verdict), a missing one
  every verdict, a malformed one none, a kept or unreadable one no list. The doors handed it as
  `Binding.signins` (`withAccount`, threading their catalog through `mcpDoor`, `outsideMcp`,
  `outsideAccount`, `serveApi` and `serveInbound` for it alone). The owner wrote it to
  `platform_signins` at `#account` and at the two sign-in doors (`switchedOn(storage, handed,
  now)`, a read with a write in it), and at the lane's pass from its own read
  (`ControlGate.signins`, `#laneSignins`). `runs.ts` read it with `switchedOn(storage,
  undefined, 0)`. A second derived copy beside `control_kept`, written by three paths.
- **What a missing or malformed record does elsewhere.** The doors' outside gate is closed on
  either (`outsideOpen`); the owner's own surfaces stay open on a missing one, so a fresh
  deployment's owner reaches its account (`ownerOpen`); the hooks gate is off. The tiers fail
  closed: a missing or malformed record drops the kept copy, and every run and file byte is
  refused `tier_unread` (`control-row.md`, review). The sandbox starts nothing, a sign-in
  included, in or out (`sandbox/src/control.ts` `signin`, `sandbox_unconfigured`).
- **What one copy changes.** `freshRecord` hands null for a missing record and for a malformed
  one alike, and every keeper drops its copy on both (`readControl`). With one copy, a missing
  record leaves no copy, so "every verdict on a missing record" has nothing to read: keeping it
  would need a second state beside the copy (a marker for "read, and missing"), which is the
  copy this change removes.
- **The doors' sources page** (`app.ts` `sourceList`) showed `enabled` from `signinsRead ?? []`:
  every verdict on a missing record, none on a kept one.
- **The catalog.** The doors serve `bundledSources()` (`doors/src/index.ts`), as the owner reads
  it; the `sources` parameter exists for tests. The D5 test (#5476) held the binding's switch to
  the doors' injected catalog; with no switch in the binding, the owner's catalog decides, as
  the lane's own read always did (`control-gate.ts`, `bundledSources()`).
- **Restore and forward.** `platform_signins` is not exported, no trigger of it writes an
  exported table, and the restore's load table never names it: `FORWARD` null. The export's
  shape does not change; its pin moves its schema number alone.

## The rule, with its reason

**No record kept, no sign-in on**, refused `signin_off` with its own words (`SIGNIN_UNREAD`:
"no sign-in is switched on while the platform's control record is not known here"). A record
kept that names no switch for a source still takes that source's verdict, as before.

- Fact 8: a switch is never taken as on unless a record says so. A missing record is a fresh
  deployment before its first deploy writes the dark record, or a deleted one: no record says
  anything, so nothing is switched on. The refusal says why, as `TIER_UNREAD` does, rather
  than the "switched off until its vendor's terms are verified" a real switch gives.
- Fact 15: the switches are the platform's limits on hosted use (vendors' terms); with no
  record there is no limit read, so the act waits for one, as runs and file bytes do.
- It costs nothing that worked: on a missing record the sandbox starts no sign-in either
  (`sandbox_unconfigured`), and every run is refused `tier_unread` before any switch is read.
- The doors' sources page follows the same rule, so it never offers a sign-in the owner
  refuses: on a missing record, as on a kept one, every sign-in shows off.

## The problems, with their fixes

- **The owner reads its switches from the one kept record** (`signins.ts` `switchedOn(storage)`:
  `control_kept`, through `signinsOn`, null with none kept; `requireOn` refuses with
  `SIGNIN_UNREAD` or `SIGNIN_OFF`). Migration 28 drops `platform_signins` (pinned, `FORWARD`
  null).
  - `Binding.signins`, a value derived from the record beside the record itself.
  - `platform_signins`, a second partial copy, written at `#account`, at the sign-in doors and
    at the lane's pass.
  - `switchedOn(storage, handed, now)`: a read with a hidden write, `now` for the write alone,
    and `runs.ts` calling it with `(undefined, 0)`.
  - `signinStart`'s fresh check read `binding.signins === undefined`, a second spelling of
    `binding.control === undefined`.
- **One rule turns a record into its switches** (`signinsOn(sources, control | null)`), the
  doors' list and the owner both: `signinsRead`, `signinOn` (one caller) and the missing-record
  branch go.
  - `signinsRead`'s four-way answer, of which only the owner's lane and the binder used the
    list, and the doors' page used `?? []`.
  - `signinOn`, exported for its one caller.
- **The lane reads the record, which keeps the copy** (`ControlGate.read`, public):
  `ControlGate.signins` and `#laneSignins` go.
- **The doors stop threading their catalog for the binding**: `withAccount`, `outsideAccount`,
  `serveApi`, `serveInbound`, `mcpDoor` and `outsideMcp` lose `sources`.
- **Tests:** the rigs' default binding hands a record with every sign-in on
  (`EVERY_SIGNIN_RECORD`, `signinRecord(on)`), in place of `EVERY_SIGNIN` beside `OPEN_RECORD`;
  a test that switched sources by the list hands the record instead. `ownerRun` keeps the
  binding's record (it overrode it with `OPEN_RECORD`, dropping its switches). The memory
  probe's `SIGNINS` goes: the probe signs nothing in. The doors' `signinsRead` unit test becomes
  a behaviour test of the sources page. The D5 test keeps its list half: the binding half
  tested a field that is gone. The code-door outage test reads the gate's read kind (`kept`,
  `unreadable`) where it read the switch list.

## Behaviour changed on purpose

- A missing record switches no sign-in on (before: each record's verdict, Claude Code's
  `allowed` among them). A sign-in start or a pasted code is refused `SIGNIN_UNREAD`; the doors'
  sources page shows every sign-in off.
- The switches follow every read the object keeps, as the tiers do since #5805: a code-mode
  script's tool calls and the doorbell's gate move them too, not only the binder and the lane.
- An object that keeps no copy yet (none since migration 20) starts no sign-in until its next
  read of the record: its next owner door hands one.

## Left, each with its reason

- The frozen-bounds guard still names `platform_signins`' spelling of `SIGNIN_LIST_CHARS`: the
  baseline statement that made it is pinned and still spells it.

## Review (2026-11-23)

- **The doors' version before binds this owner for one deploy.** The stage is deployed
  (`stage.tinyassets.io`), and the kit deploys the owner before the doors (`facts.ts`
  `WORKERS`: each Worker after the ones it binds). From the owner's 100% until the doors' (their
  0% soak and smoke, or a run stopped there, as run 45065970031 stopped, `deploy-readback.md`),
  every account and inbound call comes through the doors' version before, which hands
  `signins` (and, from before #5805, `tiers`); a parser refusing both answered every one of
  those calls `invalid`, and a rollback could not mend it (migration 28 holds the owner, and the
  doors' version before is the one refused). The 0% smoke passed regardless: it reaches only
  the new doors. The account and inbound parsers now take and drop the two
  (`binding.ts` `HANDED_BEFORE`): expand, then contract, the order the kit's `forward` rule
  names. The import's binding is left strict: an import runs only in its window, never beside
  a deploy.
- The missing-record rule matches the tiers' (fail closed, `TIER_UNREAD`) and the doors'
  outside gate; the sandbox starts no sign-in on a missing record either.

## Owed

- [x] `HANDED_BEFORE` goes, its two fields refused `invalid` again, once the doors the stage
  serves are of this PR's commit or later (live: the stage's next deploy past this PR, then
  `untangle/binding-handed-before`).

Taken by `process/binding-compat` (`binding-compat.md`), the stage's doors serving eab88202b
at 100% (stage routes run 45103565688), with the guard that holds what the doors hand the
owner to the base's doors.
