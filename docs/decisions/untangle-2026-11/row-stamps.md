# row-stamps (2026-11-23): no answer shows a count of changes

`untangle/row-stamps`, owed by `board-cursor.md` ("Left", "Stamps"). #5820 made every cursor
count only rows its reader may see, but each row still carried the counters' values, so a
visiting owner in a shared room could write a private row of its own and read the owner-wide
clock back from its `change` (`board_digest`, `collection_history`), or the room's seq from its
`seq` (`collection_write`). The coordinator's rule: nothing a reader gets may reveal the count
or timing of changes it can't see; it covers cursors and the stamps on rows.

## Research (Fact 14), from the code and the spec

### Who sees what

- A visitor (`owner/doors.json` caller `visitors`) reaches only the collections: `boardDigest`,
  `collectionGet`, `collectionPull`, `collectionHistory`, `collectionWrite` (delete and clear are
  the owner's, `owner_rows`). Hidden from it: the host's private rows and every other visitor's.
- The owner's agents, outside clients and runs read as the owner's caller (`owner-do.ts`
  `#account`), so what is hidden from them is a visitor's private rows. They also reach
  `inboxDigest` and get wakes.
- Mail is the owner's alone: no visitor reaches a mailbox, `room_post` or a wake. A message's
  id already carries its seq opaquely (`orderedId`); the mailbox's own `seq` and ack cursor
  count one owner's mail to its own agents. Out of scope.

### Every value a counter reaches an outside caller by

| Value | Where | Counter |
|---|---|---|
| `seq` | every row: pull, digest heads, get, write answer, a conflict's `current` | the collection's seqs, private writes included; a new collection's first past `seq_floor`, owner-wide |
| `etag` | every row | `sha256(seq:body)`: whoever knows the body (its writer, any reader) finds the seq in a few thousand hashes |
| `revision` | every row, history entries | a new row's first is `revision_floor + 1`, the newest revision any erase gave owner-wide, a hidden row's among them: a visitor making rows reads it, and so when the host deletes a private row and how many writes it had |
| `change` | digest rows and deletions, history entries | the owner-wide clock |
| history `before` | `collection_history` | the clock |
| digest `cursor` | `board_digest`, `inbox_digest` rooms, a reset | the clock (the reader's last item since #5820; a reset `erasure_floor`) |
| pull `cursor` | `collection_list` | the collection's seqs |
| wake `after` | each doorbell's body and text (`WakeContent`), owner's agents only | the clock (`seen`) |

Not counters: a history entry's `at` and a row's writer are of writes the reader sees.

### Who compares stamps across rows or collections

Nothing outside. The app (`doors/app/app.js`) reads both digests from 0 and shows heads; the
skills (`rooms-and-board`, `shared-brain`) pass a cursor back and claim by etag; MCP Events
hands `after` back to a digest; code mode calls the same tools. The doors Worker passes every
field through. Inside, the wake folds take the least `after` (`Ledger` `#open`) and `touched`
reads past it, both on the raw clock; `heads` and the pull door take the last kept row's
position. Nothing anywhere needs one row's stamp against another's.

### The spec's promises

`owner-object/spec.md`: a write "stamped with the collection's next seq"; a pull's cursor "the
seq of the last row it answered"; digests show "its etag, seq, writer and body length", "the
next cursor the page's last change"; `collection_write` answers "its etag, revision, seq and
`body_bytes`"; a history `before` "the change of the page's oldest entry"; "a key made again
SHALL go on past the newest revision any delete or clear gave, so no revision repeats"; a wake
carries "the newest change its agent could see". The last promise but one is the
`revision_floor` leak above: it and the rule cannot both hold.

### The cost #5820 left, at the limits

`maxRowsCharge` (256 MiB laid out, `limits-audit.md`) holds up to about a million small rows.
A digest reads `collection_changes` (one index on `change`, owner-wide) from its cursor and
looks up each entry's row. With a reader's cursor its own last item, a quiet board's reader
read again, on every call, every room row changed since its last board row, up to the whole
store: about a second of the object's time a poll at that fill. `seen` read again a visitor's
private rows past the owner's newest one on each owner write and post.

## The shapes weighed

- **(A) A counter per visibility.** Public numbering and each writer's own, two-part cursors
  (public, own). Meets the rule; a cursor may move with its own counters, so the rescans go. A
  migration renumbering every row and history key, every counter, the history's paging, the
  contract and the wakes: 1,000 to 2,000 lines.
- **(B) Opaque cursors, hidden stamps.** No answer shows `seq` or `change`; cursors are
  tokens the object reads back. Meets the rule if the etag and the revision stop deriving from
  shared counters too. Estimated to keep the rescans.

Chosen: **(B)**, and it does not keep the rescans. A cursor its reader cannot read may carry
any place, so it carries the clock (or the collection's seqs) again when the page stopped at
the end, as before #5820: no row in between is one the reader would ever be shown (a row's
change and seq only rise, a deletion never moves), so it is shown the same rows, and no longer
reads the hidden ones again. The wake's cursor is the clock before the change again, so `seen`
goes. Nothing outside needs stamp arithmetic, so (A)'s two-part cursor buys nothing.

How (B) holds the rule:

- **A cursor** is eight random bytes, the place and the object's incarnation masked under an
  HMAC of them, and a tag over both and the order it is in (the change feed, one collection's
  seqs, or one row's history), under the object's id key (`ledger_ids`, which mail ids already
  use and the export carries). Two cursors at one place differ; one made up, altered or from
  another order is refused `invalid`. A Feistel round's HMAC input starts with a byte under 4
  and the cursor's with a letter, so the one key serves both. 64 hex.
- **The incarnation** (review, #5822) is 8 random bytes in `collection_space` (migration 30,
  not exported): made the first time a cursor is, emptied by `restored()` so the next cursor
  makes a new one, and absent in a wiped or new object. A cursor of another incarnation is
  the object before a restore, whose place may be past the restored clock or top and would
  skip the restored object's next changes. A digest answers it `reset: true` with a cursor
  after the clock: the path the spec already gives a restore (`restored` puts the floor at the
  clock, so every reader behind it is reset). A pull and a history have no reset in the spec,
  so they refuse it `invalid`, "from before this object was restored": read again from the
  start.
- **An etag** is 32 random bytes as hex: it names a version and nothing else.
- **A revision** counts the row's writes since it was made, which whoever sees the row sees all
  of (a row keeps its privacy). A key made again starts at 1 under a new etag. `revision_floor`
  goes (migration 30): the coordinator's rule settles the conflict with the spec's "no revision
  repeats", which existed for nothing inside (history is keyed by change, an erase drops the
  key's entries, and stage finding 8 already told two rows at one revision apart by etag).
- **History** pages by change, as before, under a cursor in that key's own order
  (`history\n<collection>\n<key>`), its place the oldest returned entry's change (review,
  #5822: paged by revision, which a key made again repeats, an old `before` read the new row's
  entries as older ones, and with `body_offset` could splice two entries' bodies, Fact 9). A
  key made again has only entries past every old cursor's place, so an old `before` reads none
  of them.

Trade-offs taken:

- A cursor is 64 characters, not a small integer, and the contract's `after` and `before` are
  strings, left out to read from the start (a history's newest page): a type change on four
  doors and the wake.
- A reader can no longer tell from two cursors which is later. Nothing did.
- The expand step: the doors' release before validates `after` and `before` as integers, and
  the owner deploys first, so for that window a number reads from the start, and a history's
  newest page (a reread, as a reset). The contract step takes it out (Owed).
- The deploy window the other way (review, #5822): until the doors deploy, a string cursor
  the new owner answered and a client sends back through the old doors is refused by their
  integer check, briefly; the client reads again from the start once the doors serve strings.

## Problems found (untangle step 2), with the fix above each

- **One opaque cursor primitive under the object's id key (`owner/src/ordered.ts`), beside the
  mail ids it is kin to.**
  - `segments.ts` held `idKey`, `orderedId`, `idOrder` (the archive's file, not theirs), and
    `ledger.ts` read and made `ledger_ids` twice over (`#idKey`, `seqOf`).
  - Each counter's raw value was the cursor (`changed`, `pull`, a reset, a wake).
- **A cursor reads on past what its reader can't see.**
  - `changed` answered its last item, or the cursor given, so a quiet reader read every hidden
    change past it again on each call; `pull` the same over a collection's private rows.
  - `seen` existed to give a wake a number that counted no hidden change, at a scan.
- **No row shows a counter.**
  - `Row.seq`, `Changed.change`, `HistoryEntry.change` were shown.
  - `etag = sha256(seq:body)` was a seq in disguise.
  - A new row's revision was `revision_floor + 1`.
  - History's `before` was a change.
- **A cursor is of the object it was given by** (review). A cursor's raw place outlived a
  restore, which loads an older clock and top, so one minted past them read past the restored
  object's next changes.
- **One limit check.** `pull` and `changed` each wrote out the same `limit` check (`pageLimit`).
- **Comments and spec.** The module's head, `pull`, `changed`, `heads`, `ring`, the ledger's
  wake note and `WakeContent` named counters as cursors; the spec's primitive, bounds, digest
  and wake requirements, and their scenarios.
- **Tests.** Tests held cursors equal to rows' changes or seqs, compared cursors with `>`, read
  `seq` off a row, held `seen`, and held a remade key's revision past the floor.

## Left, with reasons

- **Key existence.** A visitor writing a key that is another writer's private row is refused
  "a private row is its writer's own", so it can probe a key it can guess and see when that row
  is made or deleted. Not a stamp or a cursor; private rows share one key space per
  collection, and keys of their own per writer are a schema change of their own. Reported to
  the coordinator, not owed here; done by `untangle/private-keys` (`private-keys.md`).
- **A reset tells of a prune.** A reader reset learns the history overflowed past its cursor,
  which hidden changes help fill. Accepted in `board-cursor.md`; it is rarer now, since a
  cursor that read on past hidden deletions is not reset when they go.
- **Bounds.** A write refused at the owner's rows' bound counts everyone's rows; the bounds
  are the owner's object's, by design.
- **A history's first page read on.** `body_offset` with no `before` reads the row's newest
  entry, which a write between two reads moves; a `before` from the answer before holds the
  entry. As on main; the read-on with a `before` is held to Fact 9 here.

## As built

- `owner/src/ordered.ts`: the id key (`heldIdKey`, `readIdKey`), `orderedId`/`idOrder` (moved
  from `segments.ts`), and `cursorAt`/`cursorPlace`.
- `owner/src/collections.ts`: `Row` without `seq`, `Changed` without `change`, history entries
  without `change`; random etags; revisions from 1; `pull`, `changed` and `history` take and
  give cursors in their orders (`cursorsOf`, of the incarnation), each item carrying the
  cursor past it (`ReadsOn`, `next`) for a door that keeps fewer; a reset answers the clock,
  as does a digest cursor of another incarnation; `seen` is `clock()`; `changeCursor` for the
  wake. `revision_floor` gone from `#erase` and `restored`, which empties the incarnation.
- `owner/src/pages.ts` `onFrom`: the cursor after a door's kept rows, used by `heads` and the
  pull door.
- `owner/src/ledger.ts`: the claim gives the wake `after_cursor`; `wakes.ts` sends it.
- `owner/src/schema.ts` migration 30 drops `collection_space.revision_floor` and adds its
  `incarnation` (not exported), pinned in `owner/migrations.json`.
- `contract/doors.json`: `after` optional and a string on `inbox_digest`, `board_digest` and
  `collection_list`; `before` a string cursor; generated files. `WakeContent.after` and the MCP
  Events payload a string. The app reads both digests with no `after`.
- The spec: the primitive, a new requirement "No answer shows a count of changes" with two
  scenarios, the bounds, the history, the digests, the wake.

## Owed

- [ ] untangle/cursor-numbers: once the stage's doors hand only string cursors, the owner
  refuses a number for `after` and `before` (`collections.ts` `cursorsOf`) and the test reading
  one from the start goes.
- [ ] live: after the next owner deploy, the stage's `board_digest` from the start answers a
  64-hex cursor and rows with no `seq` or `change`, and with no board row, no rows from that
  cursor, whatever the rooms hold.
