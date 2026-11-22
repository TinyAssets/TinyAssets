# events-transient as built (2026-11-22)

Owed from #5721's review: MCP Events answered a refusal for the moment as `-32024 Forbidden`,
on which a client stops. #5729 (`small-shape.md` §3) already made `asEvent` answer one as
`-32603` with `data.reason` and `data.retryAfterMs`, so on main that bug is gone. What was
left was the shape: two MCP mappings for the same refusal. `asEvent` had its own copy of the
transient and invalid answers, and `asRequest` (#5748, for the definition's resources,
prompt and skills) had the same answers again. The events door also built its own
`InvalidParams` and `Forbidden` errors by hand, apart from either mapping.

## Research (2026-11-22, before the build)

1. **The contract** (`contract/src/refusal.ts`). A code is either `transient` or terminal.
   A transient one says nothing about the request: the same request succeeds later, and
   `retryAfter` says when. `invalid` is the 400 family. `refusalOf` rebuilds a refusal that
   crossed RPC, and reads the runtime's `overloaded` as `object_overloaded`, which is
   transient. Every door surface (`doors/src/refused.ts`) reads only `transient`,
   `retryAfter`, `invalid` and `named`, never a code from text.
2. **The doors' other MCP paths, as they were:**
   - HTTP at `/mcp` (`asJson`): the code's status and `Retry-After`, e.g. `503` with `60`.
   - Tool calls (`asTool`): the tool's error result, with the refusal's message as its text.
   - Resources, prompt and skills (`asRequest`): `-32602` for an invalid request, else
     `-32603`, with `data: {reason, retryAfterMs?}`.
   - Events (`asEvent`): `-32602` with no data for an invalid request, `-32603` with the same
     `data` as `asRequest` for a transient one, then the design sketch's own codes.
3. **MCP's core spec**, `modelcontextprotocol/modelcontextprotocol` at `862397cec702` (its
   `HEAD`), 2026-07-28 `basic/index.mdx` "Error Codes" and `schema.ts`. There is no code for
   a temporary failure. `-32000` to `-32019` are legacy, and receivers MUST NOT read a meaning
   into them. `-32020` to `-32099` belong to the spec, which defines only `-32020` to `-32022`.
   So `-32603`, with the wait in `data`, is the one portable answer for "try again".
   `@modelcontextprotocol/server` 2.4.0 (pinned) has the same codes in `ProtocolErrorCode`.
4. **The MCP Events design sketch**, `experimental-ext-triggers-events`
   `docs/design-sketch-proposal.md` at `c77b84395eff`, still its `HEAD` today:
   - `InvalidParams` is plain JSON-RPC.
   - Its one recoverable failure is `-32603` with `data.reason`.
   - `Forbidden` means stop (open question 6).
   - A principal that is not authenticated MUST be answered `Forbidden`.
   - Its codes `-32023` to `-32027` are provisional: the next five in the spec's range,
     requested by SEP-3415.

So every MCP request except a tool call has one answer for an invalid refusal and one for a
transient refusal. Events adds only its sketch's codes for a terminal refusal.

## Built

1. **One mapping for every MCP request except a tool call** (`refused.ts` `asRequest`).
   `asEvent` hands any invalid or transient refusal to `asRequest`. It keeps only the
   sketch's codes for a terminal refusal: `ResourceExhausted` for a full agent, with its
   `{limit, max}` or thrown as a bug, and `Forbidden` for the rest. Its own copies of the
   `-32602` and `-32603` answers are gone, and so are `EVENT_ERRORS.invalidParams` and
   `.internal`.
2. **The events door's own errors are refusals** (`events.ts`), so they reach the client
   through the same mapping:
   - Its `InvalidParams` checks are now `Refusal("invalid", …)`, and the `invalid` wrapper
     is gone.
   - Its two hand-built `Forbidden` errors are now `Refusal("refused", …)`. Those still
     answer the same code, message and `data.reason`.
   - `NotFound`, `Unsupported` and `CallbackEndpointError` stay as they are. Each carries
     its own typed `data`, and no refusal code covers it.
3. **The one wire change.** An invalid events request, whether it comes from the door or
   from the object (`callbackUrl`, `callbackSecret`), now carries `data: {reason}`, as a
   resource's or prompt's invalid request already did. The `wire` recorder shows it.
4. One reason was corrected. The door's check that the object listed the asked event gave
   the reason `no read_graph`. The object's `wakeEvents` checks no `read_graph` for a command
   center: it returns the one subscribing agent's own event. The reason now says what the
   check finds: `this agent subscribes another event`.

## Tests

- `doors/test/refused.test.ts`:
  - `asRequest` and `asEvent` give the same answer for an invalid refusal, and for each
    transient one (a closed gate, a restore, a refusal's own wait, an overloaded object).
  - `asRequest` answers a terminal refusal `-32603`.
  - `asEvent` answers a terminal refusal `Forbidden`, with its reason.
- `doors/test/events.test.ts` and `doors/test/runs.test.ts` stand as they were. Across the
  real RPC, a full pool's refusal answers `-32603` and the retry succeeds. A run's subscribe
  answers `-32024`.

## Found and left

- [ ] `asTool` answers a transient refusal with its message only, and drops its wait. By the
  2026-07-28 spec (`server/tools.mdx`), a tool's execution error is a result the model
  reads, not an error a client branches on, so the JSON-RPC mapping does not apply to it.
  Some messages tell the model to try again (`BUSY`, the overload's). Others do not
  (`MAINTENANCE`, `RESTORING`, `storage_busy`'s bare code). How a tool result should tell
  the model to wait needs its own research: what Claude and ChatGPT do with an `isError`
  result (`untangle/tool-transient`).
- The sketch's `-32023` to `-32027` sit in the range the 2026-07-28 spec reserves for itself.
  They stay, because they are the MCP project's own provisional allocations for this
  extension (SEP-3415). If SEP-3415 renumbers them, the change is in `EVENT_ERRORS` alone.
- The `this agent subscribes another event` branch cannot be reached while
  `wake-kinds.json` has one subscribing event. It is kept as the check that the object's
  agent and the asked event agree.
