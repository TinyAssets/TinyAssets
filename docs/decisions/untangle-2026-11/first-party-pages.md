# Bounded pages in the first-party account app

The owner spec requires every content read to fit one answer and the rest to remain
reachable. On main `f934871ad` and combined `b4ebe1371`, the generic tool route in
`platform/doors/src/app.ts` serves the owner's answer. Three first-party views discard
information the answer already supplies.

| View in `platform/doors/app/app.js` | Owner result | What the current view shows |
| --- | --- | --- |
| Rooms, mail | `inboxDigest` in `platform/owner/src/ledger.ts` gives mail half the answer budget. Its first message can hold a nonempty prefix with `truncated`, `message_id` and `body_next_offset`. | The prefix appears as ordinary text. The “too long” fallback only applies to null. There is no action to read the rest. |
| Rooms, board and room collections | `changed` in `platform/owner/src/collections.ts` returns ordered changes and a cursor. `heads` in `platform/owner/src/pages.ts` fits the returned heads to the byte budget. | Both digests always request `after: 0, limit: 100`. Revisiting repeats the first page. A reset is possible only after a nonzero cursor, so it cannot explain a currently empty first page. |
| Your data and an agent's Files | `fileList` in `platform/owner/src/files.ts` returns entries and `next`, the name from which the request's `after` continues. Either the item count or the encoded size can end a page. | Both views request 200 entries and consume only `entries`. The owner cannot reach the next entry through the listing. |

## Acceptance in the ordinary account UI

- Read a message whose first page ends partway through its text. Show that the rest exists
  and let the owner read it through the existing `mailbox_read` tool, passing the returned
  byte offset and message ID. Preserve the door's `message_changed` result if a concurrent
  acknowledgement changes the first message. Verify that the displayed pieces reconstruct
  the original text, including multibyte characters.
- Check acknowledgement separately. Rendering sends no acknowledgement. The owner can
  click **Mark all read**, which sends `mailbox_ack` through the last displayed sequence.
  The ledger advances that reader's cursor. The tool description asks a reader to finish
  a cut message before acknowledging it. Whether the owner may deliberately skip unread
  text is a product choice. The demonstrable bug is that the UI cannot show the remainder
  even when the owner wants to read it.
- Exercise board and room changes beyond the first request, using each result's own cursor.
  Check a reset encountered while paging against the digest contract's instructions to
  reread the collection. Exercise both file views with more than 200 short entries and
  with a page shortened by the encoded size. Keep each subsequent request bounded.

## Evidence from this review

At both revisions, an offline caller fixture evaluated the checked-in `rooms`, `heads`
and `data` functions with normal synthetic door results. It recorded UI actions and
requests. A cut message at sequence 7 showed only its prefix. The explicit click sent
`through: 7` with no `mailbox_read` request. Two renders requested board changes after
zero both times. A 200-file page with a non-null next name issued one list request and
exposed no next-page control. The second file listing in the agent view was inspected
in source.

At `f934871ad`, the actual owner helpers `inlineWindow` and `bodied` with `INBOX_PART`
split 46,005 ASCII bytes into 24,841 and 21,164. The second read, started at the
returned offset, rebuilt the original with the first. The helper sources match
`b4ebe1371`. These checks use synthetic data and local rendering functions. A browser
test against the real owner doors remains part of the implementation.
