The sweep holds: only `conversation_agent` was a missing selector. Now checking `agent_stage_id`, then the remote test's identity binding.

**Verdict: APPROVE.** All four prior findings are fixed. I found no new floor or correctness defect. I read the code but ran no tests; the test results below are your Linux oracle numbers, not mine.

The four changed modules (`outside_authority`, `extension_ui`, `extension_capabilities`, `ta_capabilities`) are byte-identical to their packaging mirror copies. The brief doesn't collide with another lane.

**Finding 1, `conversation_agent` scope: AGREE**
- `tinyassets/outside_authority.py:226` now counts `conversation_agent` as an agent selector. It falls under the same "more than one selector is refused" rule and the exact `check_identity(..., agent=...)` check.
- I checked the parameters of all seven public tools myself. The only selectors that name an agent are `agent_id` (converse), `agent_binding_id` (read_graph and write_graph) and `conversation_agent` (get_status).
- `agent_definition_id` falls under the "indirect selector needs an explicit universe" rule. `agent_stage_id` is a private import stage (`api/graph_reads.py:275`), not a way to address an agent.
- The parametrized regression test covers all three selectors, both granted and ungranted.

**Finding 2, a malformed `settings.yaml` hid every UI: AGREE**
- `extension_ui.fence` now catches `SettingsError` for each projection, logs the UI and agent IDs, and hides only that projection. The `try` wraps only `_enabled`.
- The selection reset at `extension_ui.py:110` still sees the hidden UI ID in `projections`, so a selected card that is now hidden falls back to default. The test checks this, checks that the ordinary UI survives, and checks the log entry.

**Finding 3, activation ceiling refused instead of intersecting: AGREE**
- The `current - ceiling` refusal is gone.
- `store.active` (`extension_state.py:143-152`) still fails closed when there is no active row for this owner, universe, agent, name, revision and generation. Its return value is discarded, but the call still acts as the liveness gate.
- Binding resolution keeps its own check (`extension_capabilities.py:153`, "binding exceeds current grant").
- No ceiling-refusal code remains anywhere else, hooks included. The design already records that the jail shares the launch socket before U1, so nothing was lost.
- The regression test covers tools, commands and hooks after a new connection is added.

**`ta_capabilities` identity binding: AGREE**
- `dispatch` wraps `_dispatch` in `identity_context(self.outside_identity)`, which resets the previous identity on exit.
- That identity is captured once per launch, when `Capabilities` is built (`ta_capabilities.py:53`, constructed at `:290`). That is the same moment the bridge's `copy_context` (`:185`) already captured it, so cross-user exposure doesn't change. Binding it explicitly just keeps it present through `asyncio.to_thread` and through dispatches that arrive without an HTTP context.

**Finding 4, remote MCP never ran through the real effector: AGREE**
- `test_real_effector_remote_wire_and_outside_admission` runs the real `_SsrfHardenedHttpDriver` against a real loopback HTTP sink, through the effector test's in-process proxy and real vault token. The effector is not mocked.
- The only change is the driver's idle window. `reply_stream` is a real driver argument (`outbound_connections.py:1569`).
- It asserts:
  - the MCP session, protocol and accept headers, plus the bearer the vault injected;
  - a byte-exact JSON-RPC body, with a `$ta.ref`-shaped literal and non-ASCII text left untouched;
  - an outside-effect lease in flight during the call, with that launch's identity in the effector's context;
  - for stalled replies, exactly one tool call and no replay, `stalled` correctly set on the response, and `mcp_outcome_unknown` when the stall carried no data;
  - that the private session ID does not leak into the result.
- The test passes the launch socket through without claiming per-package isolation, as the brief requires.

**One stale note, not blocking:** `openspec/changes/one-extension-unit/review-k1-continuation.md` item 4 still says the real-effector test is "in progress". The test is now in the working tree, so update that line with the 42-pass oracle result when you commit.

VERDICT: APPROVE
