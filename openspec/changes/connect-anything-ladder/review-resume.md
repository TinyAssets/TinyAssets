Still reviewing — checking the packaged runtime mirror and the imported routes next.

Wrapping up — checking one lock-ordering detail before the verdict.

**VERDICT: ADAPT**

The design is sound, but two things need fixing before merge: the packaged runtime copy wasn't updated, and the final deposit takes its locks in the opposite order from the approval path. `tests/test_mcp_oauth.py` passes locally (13 passed). Nothing else was run.

**What holds up**
- **Session binding:** an MCP `oauth_begin` without a live owner session is refused before client registration (`flow.py:123`). In `complete`, a check against the initiating session's hash runs inside the flows transaction before the flow row is deleted. So another session of the same owner can't use up the flow (`flow.py:228-230`), and a sealed verifier that fails to decrypt is refused the same way.
- **Verifier:** it is encrypted with AES-GCM using the flow handle as associated data, and the browser's verifier is ignored for MCP. If an older resource flow has no session hash, sign-in has to restart (`flow.py:252`).
- **Logout during deposit:** checking the session again with `BEGIN IMMEDIATE` around `answer_connect_with_token` correctly serializes the deposit against logout.
- **Imported routes:** I found no bypass. `answer_connect_with_token` is reached only from `flow.complete`, and it needs a token bundle tied to the token URL shown to the owner. `inline_requests` and `inline_model_connect` don't call the OAuth flow.
- **Token/credential exposure:** none found.

**Findings**

1. **Packaged runtime copy is stale (correctness, release).**
   - **Where:** `packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/connection_oauth/{flow,pkce}.py` and `.../onboarding/model_connect.py`.
   - **Trigger:** HEAD keeps these copies in step (both the `flow.py` and `model_connect.py` copies changed in the range), but the uncommitted edits touch only `tinyassets/`. The plugin would ship the old flow, with the browser-held verifier, no session binding and no schema change, and any copy-parity check will fail.
   - **Remedy:** run the packaging sync before committing.

2. **Lock order is inverted against `bound_requests._decide` (correctness).**
   - **Where:** `flow.py:270-275`. The new guard takes the `owner-sessions.db` write lock (`BEGIN IMMEDIATE`) first. `answer_connect_with_token` is wrapped in `@_coordinated`, so it then takes `control(home)`. `_decide` (`bound_requests.py:567-588`) takes `control(home)` first, then the sessions lock.
   - **Trigger:**
     1. An owner clicks an approval card in one tab while an MCP sign-in is finishing on the same home.
     2. `control()` doesn't wait, so `complete` returns `ControlUnavailable` (marked retryable).
     3. By then the flow row is deleted and the code redeemed, so the tokens are thrown away and the "retry" can only be a fresh sign-in.
     4. The other way round, `_decide` blocks for up to 10 seconds on the sessions lock.
   - **Remedy:** in `complete`, take `control(home)` before `_session_guard`, matching `_decide`. `control` is re-entrant within a process, so `@_coordinated` will nest cleanly. Add a test where `control` is held during the exchange.

3. **Schema migration can race (minor correctness).**
   - **Where:** `pkce.py:70-74`.
   - **Trigger:** `ALTER TABLE ... ADD COLUMN` runs in autocommit before `BEGIN IMMEDIATE`. Two processes opening the database for the first time after deploy can both see the column missing; one then fails with `duplicate column name`, and that request returns a 500.
   - **Remedy:** catch `sqlite3.OperationalError` containing "duplicate column", or run `_ensure_schema` inside the immediate transaction.

**Non-blocking**
- `oauth_begin` now calls `require(optional=True)` for every flow, generic ones included. That function still refuses a request whose Origin header isn't exactly the protected origin, or whose owner-view cookie belongs to a different user, so the generic browser flow is now refused in those two cases too. This is arguably correct, but it changes generic behaviour.

The parts already listed as missing (pasted MCP request shape and activation, the `ta` catalog/dispatch/annotation policy, durable continuation, and the #4483 browser repair) were not reviewed, and this verdict doesn't cover them.

VERDICT: ADAPT
