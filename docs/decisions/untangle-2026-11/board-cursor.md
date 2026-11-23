# board-cursor (2026-11-23): a cursor counts only what its reader can see

`untangle/board-cursor`. Stage finding 9 (`stage-findings-2.md`): `board_digest`'s cursor
jumped from 0 to 6 on the live stage with no board rows, because it is the owner-wide
collection clock. A caller, a visiting owner in a shared room among them, learns from it how
many changes it cannot see happened, and when. The coordinator's call (engineering, a
cross-owner metadata leak): a cursor reveals nothing its reader can't see; the cursor a reader
gets, and how it moves, depend only on rows that reader may see; a spurious reset after a
prune is accepted, since a reset is a reread.

## Research (Fact 14), from the code and the spec

### The counters

- `collection_clock` (`owner/src/collections.ts` `#tick`): one owner-wide change, taken by every
  write and by each row a delete or clear erases, private ones included. A row holds the change
  of its latest write (`change`), a history entry and a deletion (`collection_erasures`) the
  change they took.
- `collection_seqs` / `#top`: each collection's high-water seq, taken by every write in it,
  private ones included; a collection left empty gives its seq to `seq_floor`, owner-wide, and
  the next collection made goes on past it.
- `revision_floor`: the newest revision any erase gave, owner-wide; a key made again goes on
  past it.
- `erasure_floor`: the change of the newest deletion the history's prune let go (`#let`), and
  the clock at a restore (`restored`). A feed reader whose cursor is under it (and not 0) is
  answered `reset: true`: a deletion it may hold the row of is no longer kept.

### Who sees what

- A private row, and its deletion, is its writer's (`caller`) alone. An outside client and a run
  act as the owner (`owner-do.ts` `#account`: `caller` is the owner unless a visitor's), so what
  is hidden from the owner's agents is a visiting owner's private rows; what is hidden from a
  visitor is the host's and every other visitor's.
- A visitor (`owner/doors.json` caller `visitors`) reaches `boardDigest`, `collectionPull`
  (`collection_list`), `collectionGet`, `collectionHistory` and `collectionWrite`;
  `inboxDigest` is the owner's agents' (and a run's, for its board digest).

### Every surface that hands out a counter's current value

1. `changed` (`board_digest`, and `inbox_digest`'s rooms): a page that is not full answers
   `max(after, clock)`; a reset answers the clock.
2. `pull` (`collection_list`): a page that holds everything answers the collection's `#top`.
3. A wake's `after` (`contract/src/wake-kinds.ts` `WakeContent`, sent to the agent's
   callback): a board or room ring's is the clock before the write (`collectionsDoors`
   `ring`'s `before`); a mail wake's the clock at the post (`Ledger` `#changes`). Folds take the
   least (`#open`).

Not a current value, but a counter all the same: each row's `seq` and `change`, a history
entry's `change` and `before`, a deletion's `change`, a row made again's `revision`. They are
stamps on the item, read by everyone who sees it; see "Left".

### The spec's wording

`owner-object/spec.md`, "A main agent's digests are held to its grant": "the next cursor the
page's last change when it is full, else the owner's latest, never behind the one given"; a
reader under `erasure_floor` "answered `reset: true` and the latest cursor". "Each vendor's
wake is a record": a wake carries "the owner-wide change cursor it is after". The pull's cursor
rule is the paging rule ("its cursor SHALL name where the next page starts after the last one
answered") with no word on an empty or whole page.

## The shapes weighed

- **A. The reader's own position.** A cursor is the position of the last item the page
  answered (its change for a digest, its seq for a pull), or the one given when it answered
  none. It moves only past items the reader was shown. A reset answers the floor (the least
  cursor that is not reset again). A wake's `after` is the newest change its agent may see.
  - It reveals nothing a stamp of a shown item does not: test 1 and 2 of the brief follow.
  - Cost: a reader re-reads, each call, the index entries of changes it can't see past its
    cursor. A digest reads `collection_changes` (one index on `change`, owner-wide) from the
    cursor, so a quiet board's reader reads again every room row changed since its last board
    row; before, it read each once. Each row is in that range at most once (by its latest
    change) and the deletions are bounded by the history's charge, so one call reads no more
    than it could before; only the repeat is new.
  - Cost: a reader whose cursor sits behind a deletion it could not see is reset when that
    deletion is pruned (the accepted cost).
- **B. A clock scoped per room or collection.** Each collection's own counter.
  - A private write in a shared room still moves that room's counter, so a visitor's cursor
    still moves on the host's private row: it fails the rule unless private writes take no
    tick, which leaves them no position in the feed.
  - `inbox_digest` reads every room with one cursor; a counter per room needs a cursor per
    room, a new shape on every client.
  - It needs a migration (a counter per collection, and the change index rebuilt per
    collection).

Chosen: **A**. It is the smaller and the only one that holds the rule. One method,
`Collections.seen(caller)` (the newest change a caller may see, never under the floor),
replaces `Collections.clock()`, whose only readers were the wakes.

## Problems found (untangle step 2), with the fix above each

- **A cursor is the reader's own position: the last item answered, or the one given.**
  - `collections.ts` `changed` answers `max(after, clock)` on a page that is not full.
  - `collections.ts` `pull` answers `#top` on a page that holds everything, and bounds its
    read by `seq <= top`, which every row already is.
  - `pages.ts` `heads` and `collectionsDoors.collectionPull` each pick between the store's
    cursor and the last kept row by comparing lengths: with the store's cursor the last row's
    own, it is the last kept row's, or the store's when none is kept.
- **A reset answers the floor it must pass.**
  - `changed`'s reset answers the clock.
- **A wake is after the newest change its agent may see (`seen`).**
  - `ring`'s `before` is `Collections.clock()`.
  - `Ledger`'s `#changes` is `() => collections.clock()` (`owner-do.ts`).
- **The spec says the rule.** Its digest and wake requirements say "the owner's latest" and
  "the owner-wide change cursor".
- **Tests.** `digests.test.ts`'s pruned-deletion test held the reset's cursor to the clock;
  `collections.test.ts` held a pull of a room with only another writer's private row to cursor
  1.

## Left, with reasons

- **Stamps.** A shown row's `seq` and `change`, an entry's `change`, a row made again's
  `revision`, and a new collection's first `seq` (past `seq_floor`) are owner-wide or
  collection-wide counters, so the distance between two shown items still counts the changes
  between them. Numbering what each reader sees apart would need a counter per writer and
  cursors of two parts (public, own), a change to every stored row, the history's keys and
  every client; the cursor is fixed here because it moved with nothing shown, which no stamp
  does. Review: the stamps are also an oracle a visitor can call. It writes a private row of
  its own (`collection_write` answers its `seq`; `board_digest` and `collection_history` show
  its `change`) and so reads the owner-wide clock, or a room's seq, when it likes. This change
  closes the passive leak only; the active one is `untangle/row-stamps` (Owed).
- **A reset tells the floor.** A reader under `erasure_floor` must be handed a cursor at or
  past it, or it is reset again forever; so a reset tells it the change of the newest pruned
  deletion (or the clock, after a restore). That is one number, told once per prune past it.
- **Rescans.** Shape A's repeated read of changes past the cursor that its reader can't see,
  above. A per-collection change index would bound a board reader's to the board's, at a
  migration and the rows' charge (every index entry is charged); nothing measured asks for it.
- **`seen`'s read.** `clock()` read one row. `seen` reads the change index from its top down
  to the first row its reader may see, and the deletions only past that row's change. Review
  bounded the deletions so: read whole, a clear of a visitor's private rows was read again by
  every later owner write and mail post (about 7 ms per 200,000 deletions in SQLite). A
  visitor's private rows newer than the newest row the owner sees are still read again by each
  owner write, delete, clear and mail post, bounded by that visitor's share of the rows. An
  O(1) answer keeps the owner-visible change in `collection_space` at each write, a
  migration; it goes with `untangle/row-stamps`, which renumbers these counters anyway.

## As built

- `Collections.changed` and `pull` answer the last item's change or seq, or `after`; a
  reset answers `erasure_floor`. `heads` and `collectionPull` take the last kept row's, or
  the store's when none is kept.
- `Collections.seen(caller)` replaces `clock()`: `ring`'s `before` is
  `seen(account.owner)`, the ledger's mail wake `seen(namedOwner(id))` (an outside agent's
  calls are the owner's caller). Its deletions are read only past its newest visible row.
- The spec: the digests' cursor and reset, the wake's `after`, the pull's cursor, and two
  scenarios (a visiting owner's cursor, an empty board).
- Tests: a visitor's board and room cursors stay put while the host writes private rows and
  move to the row it can see; an empty board's digest stays at 0 across room writes and
  another writer's private rows, and the rooms' cursor stops at the owner's last room row; a
  wake's cursor counts no visitor's private row (`doorbell.test.ts`); `seen` holds
  visibility, deletions and the floor; a reader behind a pruned deletion, or behind only one it
  could not see, is reset to the floor and goes on from it (`large`).

## Owed

- [ ] live: after the next owner deploy, the stage's `board_digest` from 0 with no board row
  answers cursor 0, whatever the rooms hold.
- [ ] untangle/row-stamps: the stamps' oracle ("Left"), researched first (Fact 14); with it,
  `seen`'s and the digests' rereads of what their reader can't see.
