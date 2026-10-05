## Context

The app's unconfirmed-send recovery observed saved history and the active turn
but had no way to correlate an observation with a specific send. Saved
founder-turn timestamps record when a turn finishes, not when it was sent
(`record_exchange_turns` takes no ts). Any text-and-time rule therefore matches
an earlier identical prompt.

## Decisions

- **D1 Client-minted, server-echoed identity.** `crypto.randomUUID()`-style id
  per send. The server validates it at the converse boundary and stores it
  verbatim. Equal ids can exist in different threads. Because readers echo ids
  only within the caller's own owner/home/agent thread, that cannot correlate
  or disclose another user's or agent's turn.
- **D2 Additive storage.** `TEXT NOT NULL DEFAULT ''` columns. Old rows read as
  empty and are treated as legacy (no delivery claim).
  - The conversation store needs no read migration (it selects an empty
    expression when the column is absent), and if its write migration fails it
    saves the turn without the id.
  - Agent steering requires its `ALTER` to succeed (non-duplicate errors are
    re-raised in `_connect`). Its callers open it best-effort.
- **D3 No authority.** The id is not used to look anything up, deduplicate,
  Stop or replay. Owner, home and agent resolution is unchanged. Repeating an
  id creates separate rows.
- **D4 Matching order.** Running turn first, then saved history. Only
  nonempty equal ids confirm. Text and timestamps prove nothing.
- **D5 Legacy display.** A watched running turn without an id draws only the
  replies after the newest matching founder row. It confirms and forgets
  nothing.

## Risks

- **The gap between a turn ending and its reply being saved**
  (`universe_server.py` ~3212 to ~3287): an id-bearing turn can briefly offer
  "Send it again", and an id-less turn can briefly draw an older reply. Both are
  display-only.
  - For the id-bearing unconfirmed notice, it advises checking first, and a
    focus re-check can reconcile it.
  - An id-less display misattribution made after `finishActiveTurn` has
    cleared `watchedActive` is not repaired by that re-check, and remains a
    documented limitation.
  - Both were recorded as non-blocking in the final review.
- **Rollback:**
  - Proven: the additive columns are compatible with an older binary, which
    never reads them.
  - Not proven: whether an older server accepts an unknown `client_send_id`
    sent by an already-open newer page. A server rollback must account for
    newer pages still sending the argument (for example, by reloading clients
    or tolerating the field) before it is called safe.
