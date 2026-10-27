## Context

T2 and T8 put the app and sub-user authorization in the owner's box. T5 selects a box from an authenticated identity or a positive published mapping, never from a caller-supplied owner id. T10 copies published data out at publish time and does not read private stores to list it. C12 K1 keeps owner knowledge in owner files. The shared layer stays the router, the broker and the waker.

## Decisions

1. The shared binding is `slug -> owner` plus a display name. It is not the app document and not the room.
2. The router authenticates the player, looks up the slug, and forwards to that owner's admitted box. The box owner header is the publisher. The caller header is the signed-in player. Client copies of both headers are stripped.
3. A missing route for a published owner is a not-found. The router does not admit the visitor as that owner.
4. Inside the box, a caller who is not the owner may GET the app. Pages, graphs, settings and every other door refuse. The room is not in the box (S5a, ADR-025): the router answers `/app/<slug>/state` from the publisher's broker-held conversation store as the `room:<app>:main` collection, selecting the publisher exactly as it selects the box, and never wakes a box for it.
5. Player records are keyed by the verified caller. A user id inside the JSON body does not choose the writer. A mismatched etag leaves the row unchanged.
6. The box asks the broker to publish. It does not open the shared binding file.

## Non-goals

- Cross-app integer transfers, server-side motion, and scheduled runs from the retired shared-state design.
- A websocket. Clients pull records after a cursor.
- Hiding bytes from the app owner on their own volume. The API still will not return another player's player record.
