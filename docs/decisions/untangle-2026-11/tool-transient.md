# tool-transient as built (2026-11-22)

Owed from #5758 (`events-transient.md`, "Found and left"): an MCP tool call's error result
(`asTool`) carried a refusal's message only. A refusal for the moment lost its wait, and the
model was told nothing about whether to call again: `BUSY` and the overload's message said
"try again shortly", `MAINTENANCE` and `RESTORING` said nothing, and `storage_busy` said "in
30 seconds" by a copy of its code's wait. Every other MCP request already had one mapping
(`asRequest`).

## Research (2026-11-22, before the build)

1. **MCP 2026-07-28**, `modelcontextprotocol/modelcontextprotocol` at `862397cec702`:
   - `server/tools.mdx` "Error Handling": a tool's execution error (an API failure, invalid
     input, a business rule) is a result with `isError: true`, "actionable feedback that
     language models can use to self-correct and retry"; a protocol error (unknown tool, a
     malformed request) is a JSON-RPC error. Clients SHOULD give tool execution errors to the
     model, and MAY give it protocol errors. 2025-11-25's page, which SDK 1.x clients speak,
     says the same.
   - `schema.ts` `CallToolResult.isError`: errors from the tool SHOULD be in the result, "not
     as an MCP protocol-level error response. Otherwise, the LLM would not be able to see that
     an error occurred and self-correct."
   - No field names a wait. `schema.ts` has no retry hint. `basic/index.mdx` `_meta` reserves
     only progress, protocol-version, client and server info, log level, subscription and
     trace keys, and says implementations MUST NOT assume anything about values at those keys.
   - The proposals are not adopted. SEP-3304 (a `-32023 RateLimited` error with a typed
     retry-after) was closed unmerged on 2026-09-22. SEP-3313 (structured tool-failure
     classification, retry semantics among it) is an open draft. Issue #3003 asks for a
     structured error path and is open.
2. **The pinned SDK**, `@modelcontextprotocol/server` 2.4.0: `tools/call`'s handler answers
   anything a tool's handler throws, a `ProtocolError` included (only
   `UrlElicitationRequired` excepted), as `createToolError(message)`: `{content: [{type:
   "text", text: message}], isError: true}`. So a tool call cannot answer a JSON-RPC error
   with `data`. Its result's content is the one channel.
3. **What main agents give the model:**
   - Claude: the Messages API MCP connector (`platform.claude.com/docs/en/agents-and-tools/mcp-connector`,
     read 2026-11-22) hands the model an `mcp_tool_result` block of `tool_use_id`, `is_error`
     and `content` (text blocks). It names no `_meta` or structured field.
   - ChatGPT: the Apps SDK reference says the model reads `content` and `structuredContent`,
     and that `_meta` goes to the widget only. That was read before this session.
     `developers.openai.com` and `platform.openai.com` cannot be reached from this host, so it
     was not read again today.
   - So the text is the one place every main agent's model reads the wait. `_meta` would reach
     no model, and `structuredContent` is not sent (`mcp.ts`: every result once, as text).
4. **The transient codes** (`contract/src/refusal.ts`, `contract/guards/refusal.test.ts`): `busy`,
   `egress_rate`, `events_busy`, `events_raced`, `inbound_app_unconfigured`, `mailbox_full`,
   `object_overloaded`, `restoring`, `storage_busy`, `unavailable`. The guard pins that these
   are exactly the codes with a wait. Their messages, and what each told a reader:
   - `busy`: `BUSY` (slots, the tool path) said "try again shortly". `DISCARDED` (an object reset
     for an import) said the owner "was discarded", which reads as final. The gateway's bare
     `busy` is its code by design (`gateway/src/failure.ts`).
   - `object_overloaded`: "this account is busy; try again shortly".
   - `storage_busy`: "... try again in 30 seconds." and "... try again in 1 second.", each a
     copy of the refusal's wait.
   - `restoring`: `RESTORING` said "only the restore's doors take it", which tells a model to
     go elsewhere, not to wait.
   - `unavailable`: `MAINTENANCE` and `OUTSIDE_OFF` gave no wait. `staleRefusal` said "try again
     in a few minutes" against its code's 60 seconds. Code mode threw a closed gate as
     `refused`, a terminal code, at its own start and at each script call.
   - `events_busy` and `events_raced` said "post again shortly" to the runner, which reads
     `Retry-After`.
   - `egress_rate`, `mailbox_full`, `inbound_app_unconfigured` and the inbound door's bare
     `unavailable` show their code (egress, inbound). They never reach a tool call.

## Built

1. **One reading of a refusal for every MCP answer** (`contract/src/refusal.ts` `told`): for the
   moment, with its wait in seconds; a malformed request; or final. `asRequest` and `asEvent`
   (`doors/src/refused.ts`) read it, and so does `asTool`. A terminal refusal that carries a
   wait of its own (no code throws one today) no longer gets `retryAfterMs`. A client is told
   to try again exactly when a model is.
2. **The tool's error result says it** (`asTool`): the refusal's message, then one line,
   `callAgain`. It is "This refusal is temporary: try the same call again in N seconds." for a
   refusal for the moment. It is "This refusal is final: the call is malformed, and is refused
   again until it is corrected." for an invalid one. Any other gets "This refusal is final:
   the same call is refused again until what it names changes." "Final" does not say that
   nothing could ever change. A `tier_runs` or `conflict` refusal clears when a run ends or
   the row is read again. It says that waiting does not help, which is what the contract's
   terminal means.
3. **A `code` script's refused tool call** (`owner/src/codemode.ts` `scripted`) throws into the
   script with the same text. That is the other place a model reads a tool call's refusal.
   The object cannot import the doors, so `told` and `callAgain` are the contract's.
4. **A closed gate in code mode is `unavailable`**, at the script's start and at each call, as
   at every other door. It is for the moment, not final.
5. **Messages say what happened, never when** (the rule is now in the contract's header):
   - The wait is the refusal's, and each surface says it as its protocol does: `Retry-After`,
     `retryAfterMs`, and the tool's text.
   - "try again ..." / "post again ..." are gone from `BUSY`, the overload's message,
     `storage_busy`'s two messages, `staleRefusal`, `EVENTS_BUSY` and `EVENTS_RACED`.
   - `STORAGE_BUSY_RETRY_S` is no longer exported, since its one reader outside the table was
     the copy in the message.
   - `RESTORING` now reads "this account is held while it is restored, or while its restore
     waits for an operator's check".
   - `DISCARDED` now reads "this account was reset for its import and has not started again
     yet".
6. The doors spec gains "A refused tool call tells the model whether to call again".

## Tests

- `doors/test/refused.test.ts`:
  - `asTool`'s final line for a terminal refusal, a terminal one carrying its own wait, and an
    invalid one.
  - Its temporary line, with the wait, for each transient case `asRequest` is checked
    against: a closed gate, a restore, a refusal's own wait, an overloaded object.
  - `asRequest` names no wait for a terminal refusal that carries one.
- `doors/test/mcp.test.ts`, on both revisions through the real SDK:
  - An owner refusal's text ends final.
  - A `busy` refusal of `agents_list` across the real RPC tells the model to call again in 1
    second, and the call made again succeeds.
- `owner/test/code-door.test.ts`:
  - A closed gate refuses `codeRun` as `unavailable`.
  - A script's refused calls carry the final line, or the gate's temporary line with 60
    seconds.
- The literal messages in `ledger-bounds`, `archive-read`, `control`, `e2b-hooks`, `egress` and
  `doors-live` follow the new messages.

## Found and left

- `told` reads a transient refusal's wait as `retryAfter ?? 0`. The `?? 0` is never taken:
  the contract's guard pins that every transient code names a wait, and a refusal's own wait
  only replaces it. It stays as the type's narrowing, not a fallback anyone relies on.
- `docs/decisions/untangle-2026-11/recheck-doors-e2b.md` quotes the overload's old message.
  It is a record of what was built then, and stays as it is.
- The gateway's `busy` stays its bare code: provider prose can echo credentials, so the
  gateway lets out nothing but its code (`gateway/src/failure.ts`). The tool's line says it is
  temporary.
