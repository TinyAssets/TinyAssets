# Late mail acknowledgements and unread archive research

Step 20 names two later changes under ADR-036. The shipped archive accepts mail
after every recipient releases it. A recipient can read or reply and still hold
its message unacknowledged. A post with the same sender and client message ID
then returns the original ID. Changing its room, body or parent is a conflict.
After release and an archive move the same key can start a new message. The
owner-object spec states both ends of this resend window and keeps no
tombstone per moved ID.

## Sparse late acknowledgements

The first acknowledged messages can move around a lower unread sequence. When
that sequence is later acknowledged, `Ledger.archivePick` picks the oldest
such sequence and collects eligible messages through the next fence's last key.
`Archive` reads the existing segment and rewrites the merged records. Picked
and scheduled are separate conditions. At or below half the hot ceiling the
lane can leave a small eligible batch hot. Above half, a single late
acknowledgement with a destination fence is enough to schedule a pass.

On both main `f934871ad` and the joined `ce85dfe5e`, an eight-message SQLite
fixture committed the four acknowledged sequences 2, 4, 6 and 8 as one small
fence. The other reader's cumulative acknowledgements through 1, 5 and 7
selected one, three and four messages respectively, each for that same
fence. The production selector supplied the batches. The fixture called the
commit directly to keep the segment small. It does not measure cost at a
production seal. Before choosing a batched or tiered merge, measure the work
when acknowledgements arrive singly across many large fences and when they
accumulate behind one fence. Check admission progress at the hot ceiling,
disjoint ranges, the read during a rewrite, and export at its cut.

## Unread mail

The later unread design needs to preserve the resend window before the first
move of an unacknowledged message. The original ID, changed-body/room/parent
conflict, and surviving reader's acknowledged cursor all have public callers.
The present sender/key lookup, mailbox, reader counts, owner-wide unread
ceiling and native delivery queue use hot rows. A reply updates its hot parent recipient; the
archived-parent path assumes the parent has already been acknowledged. The
mailbox's byte continuation names its message. The account's explicit
acknowledgement and the room collection's change cursor have separate owners.

On both trees, the original sender/key returned the same message before and
after a read, and a different body or room was refused. The move refused an
unread, a read, and a replied but still unacknowledged message. After an
acknowledgement its export record retained the replied state and the move
accepted it. The old acknowledgement still succeeded after the move, and a
repeat of the original post got a new ID. In a separate rolled-back projection
we removed the unacknowledged message's hot rows. The mailbox then missed it,
the old sequence could no longer be acknowledged, and an identical resend got
a new ID. This projection identifies dependencies; the shipped mover rejects it.

A second fixture ran the v3 export column definitions with the restore's actual
row loader and comparison. The original four hot mail tables read back
exactly. Omitting the remaining unread recipient and its message while
preserving the reader's exported row rebuilt its unread count as zero instead
of one; the restore comparison rejected the row. The v3 logical segment reader
and the owner's physical restore must each be checked. Moving a body alone
does not settle who owns changing recipient state, delivery, or duplicate
identity while its message stays unread.

The real inbox digest returned mail sequence 1 and collection cursor 3.
Following cursor 3 retained the mail. Acknowledging sequence 1 retained the
room rows. Staged `2ec78fcc` follows the byte continuation and `8b890623`
refreshes the mailbox after explicit acknowledgement. Neither changes archive
eligibility. The staged `46b64a90` also holds future shared streams on
restore and accounting. The fixtures above ran on Node 22 and 24 on both
trees. All four fixture groups also passed on `6df564f73` and both runtimes,
including the newer restore row loader. The original full owner Worker export test passed on `f934871ad` for
a real archive move during generation and v3 replay at its cut. Sparse
performance, unread archive design and the cross-version restore of any new
representation remain open.
