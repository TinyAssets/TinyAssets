## Context

The app's unconfirmed-send recovery observed saved history and the active turn
but had no way to correlate an observation with a specific send. Saved
founder-turn timestamps record when a turn finishes, not when it was sent
(`record_exchange_turns` takes no ts). Any text-and-time rule therefore matches
an earlier identical prompt.

## Decisions

- **D1 Client-minted, server-echoed identity.** `crypto.randomUUID()`-style id
  per send. The server validates it at the converse boundary and stores it
  verbatim. A client can only choose ids that are echoed back inside its own
  thread, so a client-chosen value cannot collide with or reveal another
  user's or agent's turn.
- **D2 Additive storage, no read migration.** `TEXT NOT NULL DEFAULT ''`
  columns, added by a race-tolerant `ALTER TABLE`. Old rows read as empty and
  are treated as legacy (no delivery claim). If migration is impossible, the
  store logs a warning and saves the turn without the id rather than failing
  the turn.
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
  display-only; the notice advises checking first and a focus re-check
  resolves it. Recorded as non-blocking in the final review.
- **Rollback:** an older binary ignores the columns and the argument (the
  argument is optional), so rollback is safe.
