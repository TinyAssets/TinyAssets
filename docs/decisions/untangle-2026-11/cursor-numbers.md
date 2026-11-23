# cursor-numbers (2026-11-23): a cursor that is no answer's is refused by name

`untangle/cursor-numbers`, owed by `row-stamps.md` ("Owed"). #5822 made every collection and
board cursor, and a history's `before`, an opaque 64-hex string, but the owner still read a
number as "from the start" (a history's newest page) for one deploy, since the doors' release
before held `after` and `before` to integers. This is the contract step: a number is refused,
and every cursor no answer gave is refused by a code of its own.

## Research (Fact 14), from the code and the decision file

### Where a number still reached a read

- `owner/src/collections.ts` `cursorsOf().from`: `given === undefined || typeof given ===
  "number"` read as the start. Its callers: `pull` (`collection_list`), `changed` (`board_digest`,
  the rooms of `inbox_digest`) and `history` (`collection_history`).
- `history` read `before ?? undefined`, and its door handed `r["before"] ?? null`: a `before`
  of null went through two conversions and read the newest page, while a pull's `after: null`
  was refused. Nothing hands null: the doors' MCP schema holds `before` to a string, and an
  answer's `before: null` means no older page.
- Every other refusal of a cursor was `invalid`, the quiet malformed-request family, with two
  messages: one made up, altered, of another order or not a string; and, for a pull or a
  history, one from before a restore (`RESTORED`, written out at both callers). A client
  could not tell either from a wrong field, though its way out is the same for both: read
  again with the cursor left out.

### How the stage's callers hand a cursor

- The doors hand the owner each tool's arguments as they came (`doors/src/tools.ts`
  `callDoor`); only `/mcp` holds them to the tool's schema (the MCP SDK, `doors/src/mcp.ts`).
  Since #5822 (351ba2fd6) that schema has `after` and `before` as optional strings, and the
  app (`doors/app/app.js`) reads both digests with no `after`.
- `/api`, the app's `tools/<name>` route and code mode (the owner's `invoke`) check no type: a
  script or key holder sending `after: 0` reached the number path whatever the doors' version.
  That is the caller the contract step refuses, by name.
- The starter (`starter/workspace/.agents/skills/rooms-and-board`, `schedule`, their copies in
  `plugins/` and `starter.json`) says "with a cursor" and hands none of its own; code mode's
  tools are the door table's; `sources/` reads no collection; the canary (`deploy/canary.ts`,
  `contract/canary.json`) calls no cursor door. None hands a number.
- Left as they are: other feeds' cursors are numbers of their own contracts, outside
  #5822's rule (the files sync feed's `fileChanges`, a mail chain's `after`, `agents_list`'s
  `next`, a meter's `after`, the wake's mailbox `cursor`).

### Expand, then contract, across the deploy order

The deploy order is codemode, files, gateway, sandbox, owner, doors (`deploy/facts.ts`
`WORKERS`): the owner deploys before the doors, and a rollback takes the doors back first.

- **What serves.** Run 45191373564 (main 9bb0b1b32) deployed all six Workers at 100%, each read
  back; its canary failed after the doors' 100% on the owner's files check (a 403 for a
  connection with no registered agent), not on a cursor. Its rollout record holds every Worker
  before it at b65adb647, and the owner as forward-only (it pins `-m30`, its version before
  `-m29`): a rollback of that run takes the doors, and the four before the owner, back to
  b65adb647 and leaves the owner at 9bb0b1b32.
- **The stage now (review, 2026-11-23).** Run 45200348628 (main b8424315e, which carries
  #5824's migration 31) has since deployed all six Workers at 100%, each read back; its canary
  failed after the doors' 100% on the full run's file read, not on a cursor. Its record holds
  the doors before at 9bb0b1b32 and the owner as forward-only (`-m31` over `-m30`), so a
  rollback of it stops at the owner and takes the doors back to 9bb0b1b32, which hand strings.
  Run 45191373564's record can no longer be rolled back at all: its owner serves neither its
  version before nor its commit, so `deploy.ts` `rollbackAsk` stops it whole ("deployed since
  run 45191373564, so nothing is rolled back"), and the kit never takes the owner back past
  `-m31`.
- **This change's deploy.** Its plan reads the doors at 9bb0b1b32 or b8424315e: both hand a
  string or nothing. It adds no migration, so its owner is not forward-only over b8424315e's: a
  rollback of its run takes every Worker back to b8424315e, whose owner still reads a number as
  the start and whose doors hand strings. Every pairing a rollback through the kit can reach
  hands strings.
- **The pairing that would break, and why no rollback reaches it.** b65adb647's doors beside
  this owner: their MCP schema requires `after` as an integer on `board_digest`, `inbox_digest`
  and `collection_list`, and their app sends `after: 0`, which this owner refuses, so every
  digest and list from `/mcp` and the app would be refused until the doors went forward. The
  only record whose doors' version before is b65adb647 is run 45191373564's, which the kit
  refuses (above). A rollback by hand (`checklist.md`, `wrangler versions deploy`) is outside
  the kit, as for every pairing.
- **What the kit's hold does not read.** The plan's hold (`deploy.ts` `servedHold`,
  `deploy/handed.ts`) reads the door table's field names, not their types: b65adb647's required
  `after` is taken (optional) here, so had run 45191373564 been rolled back before run
  45200348628, a plan of this change over its doors would have passed, the hold line naming
  "tinyassets-doors at b65adb647aea" but stopping nothing. The stage's state has closed that
  order, so this change needs no check of its own; the gap stays for the next type change.
  Owed: the hold reads each field's type too, so a type change takes an expand step the kit
  holds as it holds a field's.

## The shape

The number path and the null path go, and both cursor refusals take one code of the contract's,
`cursor_invalid` (400, not quiet, so every surface shows it), each message starting with it, as
#5818's file faults and `row_changed` do. The two ways out are one: read again from the start,
the cursor left out. A digest still answers a cursor of the object before a restore `reset:
true`, as the spec gives it; a pull and a history refuse it, now in one place (`placed`).

## Problems found (untangle step 2), with the fix above each

- **A cursor is a string an answer gave, or left out; anything else is `cursor_invalid`.**
  - `cursorsOf().from` read a number as the start: the expand step's compatibility branch.
  - `history` read `before ?? undefined` and its door handed `r["before"] ?? null`: null read
    as the start in a history, refused in a pull.
  - A malformed cursor and a pre-restore one were `invalid`, which a client cannot tell from a
    wrong field.
- **The pre-restore refusal lands in one place.** `pull` and `history` each wrote out `if (from
  === null) throw new Refusal("invalid", RESTORED)`; `Cursors.placed` holds it.
- **Comments and spec.** `Cursors.from`'s comment named the expand step and this branch; the
  spec's primitive said a number reads from the start, its scenarios said `invalid`.
- **Tests.** One test held a number read from the start (pull and history); the refused list
  mixed cursors and limits under `invalid`; in-object calls handed `0` and `null` as "left out".

## Left, with reasons

- **An app page open from before 9bb0b1b32's doors** (b65adb647's `app.js`) sends `after: 0`
  and is refused `cursor_invalid` until it is reloaded. The doors have served the new app since
  run 45191373564's apply; the refusal is loud and a reload ends it.
- **Other feeds' numeric cursors** (above): their own contracts, not this one's.
- **The tool texts** (`contract/doors.json`) already say "the last answer's cursor (left out at
  first)": no change.

## As built

- `contract/src/refusal.ts`: `cursor_invalid`, 400.
- `owner/src/collections.ts`: `cursorsOf().from` refuses anything but a string an answer of
  that order gave, or `undefined`; `placed` refuses a cursor of the object before a restore, for
  `pull` and `history`; `history` takes `before` as handed, its door no longer converting it.
- `owner-object/spec.md`: the primitive refuses by name, a number among the made-up cursors,
  with no number read from the start.
- Tests: `owner/test/collections.test.ts` (a number, null, a boolean, an empty, a made-up and an
  altered cursor refused `cursor_invalid` by a pull and a history; limits still `invalid`; a
  pre-restore cursor's code and words), `owner/test/digests.test.ts` (a number refused by both
  digests, by name).

## Owed

- [ ] untangle/handed-types: the deploy kit's hold (`deploy/handed.ts`) reads each door
  table field's type as well as its name, so a plan stops before an owner that refuses a type
  its serving doors hand (as this change would have over b65adb647's doors, had run
  45191373564 been rolled back before run 45200348628), and a type change takes an expand step
  held as a field's is. Research first (Fact 14): how the table says a field taken in two types.
- [ ] live: after the next owner deploy, the stage's `board_digest` with `after` a number or a
  made-up cursor (via `/api` or code mode, which hand it through) answers `cursor_invalid`.
