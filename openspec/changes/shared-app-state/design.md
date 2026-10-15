## Context

Owner boxes (S8) keep an owner's files. They are the wrong place for state
that several accounts read and write at once. Platform state stays outside
boxes (S10). Seats and storage remain the two account limits (S26). The
actor is the authenticated account (S14). An app is still one command center
(N11); this document is that center's shared data, not a second app store.

## Decisions

1. The hot path is an ordered log a client pulls with `sync`. It does not
   start a run, take a seat, or wake a box. There is no push into the
   sandboxed screen.
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
   app owner's. The host enqueues it only when no queued, waiting, or running
   row of that name exists.

## Non-goals

- Executing arbitrary rules supplied as code.
- Calling a model from the schedule. Foreground model admission is unchanged.
- Moving a record row into a second app.
- A physics engine: collisions, forces, and constraint solvers are not a step.
- A websocket or an eighth MCP handle. The screen allowlist does not call
  `sync` yet.
