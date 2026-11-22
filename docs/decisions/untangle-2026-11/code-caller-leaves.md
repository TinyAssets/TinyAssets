# code-caller-leaves as built (2026-11-22)

Owed by `fact15-caps-2.md` (#5784): code mode (the MCP `code` tool, any caller; pi-codemode
its library; the codemode Worker its VMs) had a 5-minute wall time whose only job was to end a
script whose caller had left. Owed: let a caller's leaving stop its script, so the wall time
becomes the caller's own wait. Fact 15: the only limits are the tier's, the owner's, vendors'
and recorded technical ceilings, each with its reason. Research first (Fact 14), read at
`475c51e5a`.

## Research

### How a `code` call reaches the VM

- **Doors to owner.** Every tool call (`/mcp`, `/api/tools/<name>`, the owner app's
  `/app/api/tools/<name>` and its door routes) is `callDoor` (`doors/src/tools.ts`): a Durable
  Object RPC call `codeRun(binding, request)` on the owner's stub. It carried nothing of the
  caller, so its leaving could not cross.
- **Owner to codemode.** `codeRun` runs the script with pi-codemode's `RemoteCodemodeSandbox`,
  whose `remote()` calls the `CODEMODE_VM` service binding's `codemodeExecute(name, request,
  exchange)`; every tool call comes back to the owner through `exchange`, an RPC stub. The
  sandbox's `execute(script, { signal })` takes an `AbortSignal`, which the owner aborted only
  at its own deadline (`late`).
- **The pattern that crosses.** The egress door hands the gateway a stop already
  (`EgressService.forward`'s `stop`, `contract/src/gateway.ts`): a `ReadableStream` the caller
  closes, read by the callee, whose read resolving or failing aborts the call. A stream passed
  over RPC also breaks when the caller's side of the call is gone, so it carries that end too.

### Whether a caller's leaving reached the script: no

- **The MCP SDK** (`@modelcontextprotocol/server` 2.4.0, probed in Node with its own client,
  2.4.0): the tool callback's context has `mcpReq.signal`, aborted when the HTTP request's
  signal aborts, in both revisions the doors serve. `doors/wrangler.jsonc` sets
  `enable_request_signal`, so a client's disconnect fires the request's signal in workerd.
- **2026-07-28** ("Cancellation", "Transport-Specific Cancellation", read from the spec's own
  repository): over Streamable HTTP, closing the response stream is the cancel, and a server
  MUST treat a disconnect as one; a client's timeout closes it. Probed: the SDK client's timeout
  aborts its fetch, and the tool's `mcpReq.signal` aborts.
- **2025-11-25** (its Streamable HTTP transport): disconnection SHOULD NOT be taken as a cancel;
  a cancel is `notifications/cancelled`, a request of its own. Served statelessly (a fresh
  instance a request, no session), nothing here can match it to the call: request ids repeat
  across a grant's clients (each SDK client counts from 0), so matching by grant and id could
  stop another conversation's script. Probed: the SDK client's timeout sends only the
  notification and keeps the call's stream open; a dropped connection still aborts
  `mcpReq.signal`. Stateless, we hold no event ids, so a disconnected legacy client can never
  resume to read the answer either: stopping on its disconnect loses nothing.
- **No MCP client sends a deadline**: neither revision has one (the 2026 `_meta` envelope
  carries the revision, client info and capabilities, probed), so the wall time cannot become
  the caller's own deadline. It becomes the caller's own wait: its leaving.
- **Who speaks what.** Claude Code 2.1.330 (its binary, on this host): the v2 MCP client only
  ("this build has only the v2 client"), revisions up to 2026-07-28, and a tool timeout of
  1e8 ms by default (`MCP_TOOL_TIMEOUT`, at most 2^31 - 1). The connectors, Gemini CLI 0.66.0
  (10 minutes, `MCP_DEFAULT_TIMEOUT_MSEC`) and pi 1.4.0 (60 s) are not known to speak
  2026-07-28 (`fact15-caps-2.md`).

### What pi-codemode does on an abort (probed in workerd over `CODEMODE_VM`)

- A script calling tools in a loop: aborted at once, `{ kind: "aborted" }`, no call after, the
  codemode Worker's `codemodeExecute` resolved within 2 ms.
- A script awaiting a pending tool call: that call `cancelled`, the remote execution ended.
- A script awaiting a promise nothing can settle: pi ends it at once ("The script is waiting on
  a promise that can never settle"), so no script idles holding a VM.
- A script computing between calls: workerd runs every isolate on one thread, so the test
  cannot show whether the abort reaches the VM before its next exchange. Either way its
  compute ends at the interrupt budget (`CODEMODE_INTERRUPT_BUDGET`, about 100 s), and H2's
  probe 1 measures it live.
- `timeoutMs`: pi's sandbox takes one, its default 300,000 ms; `0` ends a script at once;
  `Infinity`, `2^31 - 1` and `2^31` never ended a short script.

### What bounds a script once its caller cannot leave

Its compute (the interrupt budget) and its memory (the VM cap), each a codemode Worker ceiling;
its tool calls, each its door's own, admitted again (a revoke, a narrowed profile, a run's end,
the switch or outside access off refuses the next); a run's engine stopping, which closes its
connection; and the doors' own invocation ending, which breaks the stop stream. What is left
is a caller that stops waiting without leaving: a 2025-11-25 client past its own timeout.

## Decided

1. **The caller's stop is a door's contract, as data.** A row of `contract/doors.json` whose
   work runs while its caller waits says so (`"stop": true`, only `codeRun`); `doorProblems`
   holds it true, on a door that takes a binding and a request, and `door-steps.ts` on a door
   that makes its own units. The generators give it a third parameter
   (`stop: ReadableStream<Uint8Array>`, `OwnerDoors`, `DoorMethods`) and name the doors
   (`StopDoors`). The doors hand it (`callDoor`, `takesStop`): an `IdentityTransformStream`
   whose writer closes when the caller's `left` aborts or the call ends. The owner's runner
   reads it (`#leased`): a call without one is refused `invalid`, loudly; its read ending
   aborts the handler's `door.left` (`Leaving`, typed only on `StopDoors`), and the reader is
   let go when the call settles. One home: the door table says which doors, `callDoor` makes
   the stop, the runner reads it, the handler honours it.
2. **Every caller hands its leaving.** `callDoor` takes `left` from every caller: `/api` and
   the owner app hand their request's signal; `/mcp` hands `mcpReq.signal`, and for a
   2025-11-25 request (no per-request envelope) also `LEGACY_WAIT_MS` past its start
   (`doors/src/mcp.ts` `leaving`). A run's own MCP door is `serveMcp` too.
3. **The wall time is the caller's own wait.** `CODEMODE_TIMEOUT_MS`, the owner's deadline
   checked at each tool call and its `late` abort go. The script runs under `door.left`; once
   it aborts the script is stopped, answered `{ kind: "stopped" }` with its calls and what
   landed (a call in flight `cancelled`), and logged (`console.warn`) with the same, since its
   answer has no reader. pi's own deadline is set to the most a timer takes (`TIMER_MOST_MS`,
   2^31 - 1 ms), never reached by a waiting caller.
4. **One technical ceiling stays, where its reason is:** `LEGACY_WAIT_MS`, 10 minutes, for a
   2025-11-25 MCP caller, whose leaving cannot reach a stateless server; the longest wait known
   of a client that may speak it (Gemini CLI's), so none is cut while it waits. A 2026-07-28
   caller, `/api` and the owner app have none: their leaving is their request's end.
   `limits-audit.md`: the `CODEMODE_TIMEOUT_MS` row is replaced by `TIMER_MOST_MS` and
   `LEGACY_WAIT_MS`, and the summary recounted (183 technical ceilings, 212 rows, after
   merging main).
5. **Tests.** Owner: a script whose stop closes while its fourth call is held at the FILES gate,
   fifty calls after it, is answered `stopped`, three writes landed and the fourth
   `cancelled`, no call after; a call without its caller's stop refused `invalid`
   (`code-door.test.ts`); no clock ends a script (`codemode.test.ts`: pi's timers faked and
   moved ten minutes on while its one call is pending), replacing "stops a script past its
   wall time". Doors: a 2026-07-28 client's cancel of an endless script ends it, the object's
   answer seen at the owner gate (`profiles.test.ts`). Guards: the stop's table rules in both
   halves. The RPC recorder hands a stop door a waiting caller's stop, as the doors do, so its
   lines are unchanged. Specs: `owner-object` (the requirement and a scenario), `doors` (the
   stop each tool call hands). H2: probe 4 is now a waiting caller's 10 minutes of calls (no
   clock of ours; whether the owner object's limit on one invocation binds), probe 7 a caller
   that leaves.

## Found and left

- The gateway reads its egress stop the same way (`gateway/src/forward.ts`, one line beside
  its own aborts of the same controller), and the egress door makes its `halt` as `callDoor`
  makes its stop: kept, not a copy. Each side's line is its own call's, and a shared helper
  would wrap one line.
- The owner object's CPU for one long script: each tool call runs in the owner through
  `exchange`, a stub made in `codeRun`'s invocation, and Cloudflare does not say whether those
  calls count as that invocation's. With the 5-minute bound gone a waiting caller's script may
  run longer; H2's probe 4 (10 minutes of calls back to back) shows it, owed below.

## Owed

- [ ] live: H2's probe 4 holds that a waiting caller's 10 minutes of tool calls back to back
  ends with its value (no owner object reset), and probe 7 that a caller that leaves stops its
  script (the owner Worker's log line), before code mode turns on (`tasks.md` §5).
