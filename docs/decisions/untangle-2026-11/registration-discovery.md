# Agent registration discovery as built (2026-11-18)

On main `f934871ad`, six MCP tool lists described the method as “How the agent is
reached: pull or mcp_event”. The owner already accepts two more methods supplied
by the wake records. The owner app lists only the callbacks an owner installs,
by kind ID, and MCP Events lists event types. Neither supplies this field's
values. A registration proposal captures the submitted fields. The owner checks
the method when approving it.

The registration door now refers explicitly to its delivery-method schema.
The contract expands that reference to a string whose description contains
the methods from `admittedMethods()`. Both MCP discovery and code mode's
generated declaration show field descriptions. The latter omits schema
examples. No enum is introduced. The registration tool also identifies the
outside MCP connection that files a proposal and the owner's separate path for
API-key agents.

`custom`, `directory` and `run` retain their existing tool selections. Seeing
the tool does not grant the right to file. The `own_card` check requires the
outside origin, grant and MCP source. A run's binding carries no such source.
An API-key agent registers in the owner's session, with the key's verified
identity, through the owner app's protected `outside-agents` backend route.
The shipped browser interface has no control for calling this route. An API-key
agent can use pull or an owner-installed callback. Subscription by the
client requires MCP.

The current wake records, the owner's `DELIVERY_METHODS` vocabulary and the
SQLite constraint in migration 1 agree on all four admitted methods. The wider
stored vocabulary also names the unadmitted `a2a` and lets the owner distinguish
it from an unknown method. Before adding another method, review this vocabulary
and the persisted constraint. A name outside the stored set requires a new
migration and verification against an existing owner database.

The intended wire change is confined to the registration tool and its delivery
field descriptions, wherever the tool's full schema is served. The owner app's
tool names and titles, proposal value checks and approval stay as before.
