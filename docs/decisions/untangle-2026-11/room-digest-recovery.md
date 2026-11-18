# Recovering current room collections after a digest reset

The account app's Rooms view reads two different kinds of data through
`inbox_digest`. The mailbox supplies personal messages with a read position.
The collection digest supplies visible changes across `room:<app>:<room>` collections.
Finishing a large message with `mailbox_read` addresses only the first read.

At main `f934871ad`, `Collections.changed` orders row versions and retained
deletions by the owner's change clock. When a positive cursor precedes the
retained deletion floor, it returns an empty page with `reset: true` and the
current owner clock. A zero cursor can start before that floor. Its first
size-bounded page can then end below the floor, so restarting the digest at zero
can repeat the same first page and reset indefinitely, even when writes stop.

An offline SQLite fixture used all 18 migrations and the actual owner
collection, page-fitting and pruning code. It wrote 40 board rows and 40 room
rows, each with 512 control characters, then removed one row from each
collection. Updating another collection 9,832 times reached the ordinary
128 MiB retention and established deletion floor 83. At owner clock 9,915 the
room digest's fresh first page ended at 16 and the board's at 31. The next
request for each returned reset at 9,915. No floor was injected.

## What the existing doors can enumerate

- `collection_list` (`collectionPull` in `owner/src/collections.ts`) lists the
  caller's visible current rows by one **known** collection's sequence. The
  board always has the one known name `board`. Its reader can keep the reset's
  owner-clock value, page the current rows on their own sequence, then return
  to that owner-clock value for intervening updates and deletions.
- The room digest scans a **prefix** rather than one known collection.
  `contract/doors.json` offers no current-row baseline across that prefix and no
  complete, caller-visible directory of its collection names. The names seen
  earlier on a stale or byte-shortened feed can omit live rooms. The owner's
  internal `Collections.rowsPage` belongs to import checking and does not apply
  the collection door's caller visibility. A whole-account export is also a
  different scope from one caller's bounded view.
- Mail membership, delivery subscriptions and the list of live agents answer
  different questions. Pair mail includes historical and retired participants,
  and clients need to find a pair before its first message. A collection name
  can instead come from an app writer with no pair-mail relationship. Preserve
  each source's own scope and existing identifiers.

Before offering complete cross-room browsing, the owner contract needs a bounded
way to recover **all visible current room rows** and resume owner-wide changes.
Exercise byte-short pages, names unseen before a reset, concurrent edits and
deletions, and a further floor advance during recovery. Verify the same visibility
rules as the normal collection reader. `inboxDigest` also calls `ledger.read`,
which records delivery of the mail it returns. A caller refreshing room changes
has to account for that mail and preserve the owner's explicit acknowledgement
through the displayed sequence. A new public door, cursor or membership model
requires design review. This review leaves that choice open.
