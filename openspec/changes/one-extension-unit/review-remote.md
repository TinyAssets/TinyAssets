Reading done; checking how the existing ta connection dispatch calls the effector, then one test file.

**VERDICT: ADAPT.** I found no floor (cross-user) violation. One correctness finding should be fixed or explicitly scoped before merge; the rest are low.

**What I checked**
- I read all four modified source files, both new modules and the new design section, and compared the adapter with the existing `Capabilities` connection dispatch (`tinyassets/ta_capabilities.py:120-149`) and the governed effector.
- I ran one test file: `tests/test_extension_remote.py` gave 7 passed. I didn't run `tests/test_mcp_remote.py`.

**Floor and authority: AGREE, no findings**
- **Bindings are owner-scoped.** `connections()` filters on the owner and universe and drops revoked views. The bindings table is keyed by owner, universe and agent, and `extension_bindings` is added to `OWNER_ONLY_TABLES` (`account_deletion.py:117`).
- **Revision and grant fencing holds.** `_bindings` (`extension_capabilities.py:100-118`) only accepts grants the user already holds for every declared verb, and never creates one. `connection()` (`:120-141`) rechecks the incarnation, the activation ceiling, the grant ID and the verb on every use. `ExtensionStore.bindings` reads by exact revision and generation, so revoking or re-activating makes old pins unreadable. `check()` (`extension_remote.py:79-84`) runs before each exchange and after each streamed message.
- **No credential leakage found.** `auth_header` is always empty, so no `header_name` is sent and OAuth stays inside the effector worker. Response headers, including the session ID, never reach the box: discover returns only tools and the hash, and call returns only the result. Egress is still limited by the connection's allowlist, so an author-supplied `row["url"]` can only reach endpoints the owner already allowed.
- **No blind replay.** A tools/call failure after admission, or a lost result, becomes `AmbiguousProxyOutcome`. A refusal raised before the stream opens (`Held`) re-raises with nothing sent. Discovery retries once, only on an explicit 404 session expiry.

**Findings**

1. **`tinyassets/extension_remote.py:45-49` with `mcp_remote.py:157-180`: approvals bind to one JSON-RPC frame. Correctness; suggest DISAGREE_EVIDENCE unless fixed.**
   - **What happens:** if the owner's rule for this connection is ask-first, the effector (`authenticated_external_call.py:1167-1183`) saves the exact packet as an owner card. That packet is a single message (`initialize`, `tools/list` or `tools/call`), plus a server-issued `MCP-Session-Id` header after the first message.
   - **Approving initialize or tools/list** sends one orphaned message whose result goes nowhere.
   - **Retrying doesn't help.** The next `invoke` opens a new session, so the packet differs, scoped `matches()` misses, and a new card appears. Remote MCP can never get past discovery under ask-first.
   - **Approving a held tools/call** replays it later with a session ID that has probably expired (404, so nothing happens after the owner approved). If the session is still alive, the tool runs and the agent never sees the result.
   - **No rule can tell discovery from a tool call.** Every exchange is a POST to the same path, so `classify()` gives one operation. Meanwhile `requires_approval` (`mcp_remote.py:335`) is dead code.
   - **Suggested adapt:** classify `initialize`, `notifications/*` and `tools/list` as read operations, and hold only `tools/call`. Make approval bind to `(tool, arguments, catalog_hash)` and run inside a fresh session, or remove the session header from the captured packet. Add an ask-first test; today only hand-off is tested.
   - **Default case:** with no rule configured, a POST is treated as `app.write`, so this only bites owners who set ask-first. That's why I rate it correctness rather than floor.

2. **`extension_remote.py:46-47`: the "outcome unknown" mapping is too broad and drops detail. Low; AGREE as conservative.** Every `outbound_request_failed` becomes `mcp_outcome_unknown` with the effector's `detail` thrown away. That includes discovery (no side effect) and proxy refusals that happen before anything is sent. Suggest limiting "unknown" to tools/call and passing the sanitized `detail` through.

3. **`mcp_remote.py:136-146`: the cancellation comment is false for `EffectorTransport`. Low.** No broker CANCEL is sent. The `to_thread` POST keeps running after cancellation, so the tool may still run on the server. The follow-up `notifications/cancelled` is a new POST through the effector, which can itself be held or reviewed. Fix the comment, and decide whether to send that notification at all on this transport.

4. **`extension_state.py:130`: `bindings or {} if active else {}` parses as `bindings or ({} if active else {})`. Low; AGREE, no bug.** Revoke passes `{}` today, so behaviour is correct, but `bindings if active else {}` says what's meant.

5. **`ta_capabilities.py:112`: `import inspect` sits inside the hot path. Nit; AGREE.**

**Suggested dispositions:** 1 is DISAGREE_EVIDENCE (needs the change or an explicit "ask-first unsupported" refusal plus a test). 2 and 3 are AGREE with small follow-ups. 4 and 5 are AGREE.

VERDICT: ADAPT


## Dispositions

AGREE on the ask-first correctness finding. Session exchanges now explicitly
refuse deferred approval before capturing any HTTP frame; existing owner standing
rules can admit the operation. A negative test proves no orphaned action capture.
Full semantic MCP tool approval continuation remains work, not a claimed feature.
AGREE on conservative uncertain outcomes: never replay after an unclassified
transport failure. AGREE on cancellation wording: current effector workers cannot
be forcibly cancelled; the server notification is best effort. Parenthesized the
binding revoke expression for clarity. Import placement is not a floor/correctness
finding and needs no scope expansion. No APPROVE verdict is claimed.
