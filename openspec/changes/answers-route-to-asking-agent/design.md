## Context

The request store already records an agent but most callers force main. App replies bypass the request handler. Protected action and connection requests already have continuation receipts.

## Goals / Non-Goals

Route every answer surface by server-captured provenance; preserve existing consent and credential boundaries. No prompt-head changes or new disk store.

## Decisions

Store immutable asking provenance alongside each request in the existing protected database. Commit an answer delivery receipt in the answer transaction and drain it through the existing continuation sweep. Keep protected action/connection execution continuations as their existing single wake, avoiding duplicate turns. Plain replies use the same owner-gated answer door and leave the request open. Clients never pick the destination.

Recheck current home ownership and binding ownership before delivery. A foreign binding is refused; an absent or retired same-owner binding falls back to main with an explicit note. Preserve run/workflow provenance as data, never as caller-supplied authority.

## Risks / Trade-offs

Worker crashes after model execution can retry computation, as existing continuation recovery does; effects remain governed by existing effector receipts. Owner changes hold delivery rather than rehome it. Legacy rows cannot recover missing historical provenance and retain their recorded main destination.

## Migration Plan

Add columns and a table in the existing protected request database using its idempotent schema mechanism. Existing rows remain readable. No new storage-accounting entry is needed because the database is already accounted for.

## Open Questions

None; deployment remains separate from this draft PR deliverable.
