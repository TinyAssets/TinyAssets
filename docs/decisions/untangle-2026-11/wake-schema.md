# Wake event schema as built (2026-11-18)

The owner names the digests to read in each wake it delivers. The MCP
`events/list` schema advertises the allowed change and digest names. Both now
read the same table in `contract/src/wake-kinds.ts`. Mail and room changes
share the inbox digest. Board changes use the board digest. Each digest
appears once, in the established wire order, even when mail and rooms changed
together. The owner still owns the three bit values in its persisted wake rows.

The MCP test reads the schema through the public endpoint. The existing owner
doorbell test reads a delivered wake and follows its cursor through the real
inbox and board doors. Separately, 121 ordered inputs through length four
matched the eight expected combinations on Node 22 and 24. Moving rooms to the
board digest or returning the inbox digest twice failed that comparison.

App room collections use the persisted prefix `room:`. Subscription checking,
the public schema's example, the owner's change classification and the inbox
digest's SQLite range now read it from `contract/src/ids.ts`. The import
bundler takes collection names verbatim from `conversations.db`; the owner
writes those same names. The owner-object spec and digest fixtures use stored
names such as `room:app:lobby`. Mail pair IDs belong to the mail ledger.
