## Context

Owner boxes (S8) keep an owner's files. They are the wrong place for state
that several accounts read and write at once. Platform state stays outside
boxes (S10). Seats and storage remain the two account limits (S26). The
actor is the authenticated account (S14). An app is still one command center
(N11); this document is that center's shared data, not a second app store.

## Decisions

1. The hot path is an ordered log. `sync` reads it and does not start a
   run, take a seat, or wake a box. A client may name an interest (kinds
   and keys); the cursor moves past every row examined. A burst of writes
   wakes waiters once per short tick. Watchers of the same public interest
   share one page. The signed-in shell holds `GET /app/api/live/stream` and pushes
   those deltas into the screen. The screen has no socket. This is not an
   eighth MCP handle.
2. The document is data. Kinds, secrecy, transitions, transfers, groups,
   timers, motion, and scheduled runs are fields. The module has no nouns
   for any app.
3. `save_app` inserts one row. `revise_app` replaces that row at its etag.
   Invitees cannot revise it.
4. Creator-owned keys are stored as `{account}/{local}`. A client cannot
   create another account's creator-owned record. App-owned keys are the
   local key and are billed to the app owner.
5. Secret fields are removed from the public projection at any depth. A long
   secret string echoed into the public JSON is refused. A short string is
   not scanned.
6. A transfer names each record once. A move may name another app. The actor
   must be a member of that app, and that app must declare the same transfer
   name, kind, field, and conserve flag. Bytes stay with each record's owner.
7. Motion adds `velocity * tick_s * steps` to `position`, at most 8 steps of
   catch-up. It does not run code.
8. A scheduled run's actor is `app`, so the seat and the provider are the
   app owner's. That is intended: the owner stored the document. The host
   enqueues it only when no queued, waiting, or running row of that name
   exists, and only while the owner's in-flight runs are under the owner's
   background seats. `scheduled_runs` tells the owner who is billed. A
   member cannot read it.
9. A durable live write records owed bytes in the same transaction. The
   quota ledger is charged when an account owes 65536 bytes, or by the
   stream process about every 2 seconds. The charge happens outside the
   writer lock. A same-sized position update does not call the ledger.
10. A record kind declares `durability` `transient` or `durable` (the
    default). Transient updates of an existing record are authoritative in
    memory for that one app, fanned out on the stream, and checkpointed on
    the document's `checkpoint_s` or before a transition, transfer, or
    document replace. A checkpoint is one transaction: the latest body and
    one log row per dirty record, billed as stored bytes, not as one row
    per tick. Creation of a transient record is a durable insert. Durable
    writes, transitions, and transfers still commit with an etag. A crash
    between checkpoints drops uncheckpointed transient ticks. Group commit
    of every tick stays off the hot path: the serial cost is the apply, and
    a commit per burst is still linear in N. A writer per app does not
    split the players of one app; the per-app memory lock is what keeps
    unrelated apps off that path.

## Non-goals

- Executing arbitrary rules supplied as code.
- Calling a model from the schedule. Foreground model admission is unchanged.
- Moving a record row into a second app.
- A physics engine: collisions, forces, and constraint solvers are not a step.
- A socket inside the sandboxed screen, or an eighth MCP handle. The shell
  holds the stream. The frame's connect-src stays `data:` and `blob:`.
