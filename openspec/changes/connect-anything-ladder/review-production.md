Reviewing the approval dispatch path now — checking how `approved_call` resolves the owner and whether `asyncio.run` runs inside an event loop.

Checking whether the OAuth callback that finishes an MCP sign-in runs inside an event loop (where `asyncio.run` would fail).

Now checking the `policy()` "explicit rule" test against `agent_rules` semantics — this decides whether destructive MCP tools are forced to ask first.

Running the one focused test file allowed by the brief.

The files changed while I was reviewing (the author is still editing). Re-reading the current versions before reporting.

I found three problems that need fixing. None of them is a cross-user custody leak, but one lets a destructive MCP tool run without the owner being asked. **VERDICT: ADAPT**

The author edited `tinyassets/mcp_runtime.py` and `tinyassets/bound_requests.py` while I was reviewing: an execution guard and a `request_id` argument were added to `approved_call`. The findings below are against that newer version.

### F1 — Consent bypass: a rule for one connection turns off ask-first on every MCP connection (high)
`tinyassets/mcp_runtime.py:193-199`. `policy()` normally forces ask-first for destructive or unannotated tools. It skips that when `explicit` is true, and `explicit` is true for any owner-edited behaviour rule of the same action class (`r.connection == conn or not r.seeded`), whatever connection that rule names.

- **How it happens:** the owner saves an `app.write` rule for an unrelated connection, say GitHub. `agent_rules.decide()` correctly ignores that rule for the MCP connection and falls back to the seeded `("app.write", DO)` (`agent_rules.py:94`). But `explicit` is now true, so the ask-first override is skipped. The agent's `ta mcp:<conn>:<destructiveTool>` then runs with no card.
- **Fix:** match the way `decide()` picks rules (`r.connection in ("", conn)`). Better still, treat the rule as explicit only when the rule `decide()` actually chose is owner-edited.
- **Missing test:** nothing in `tests/test_mcp_connect_flow.py` sets an owner rule.

### F2 — Approved MCP calls whose outcome is unknown can't be reconciled (medium-high)
`tinyassets/mcp_runtime.py:242,266` and `bound_requests.py:706-709`.

- `_decide` writes an effect intent keyed `key`, but `approved_call` passes no `op_id`, so `call()` makes a fresh `new_op_id()`.
- On `AmbiguousProxyOutcome`, the receipt keeps only `error_kind` and `state`, so the `op_id` is dropped.
- Nothing calls `RemoteMcp.reconcile(op_id)`.

Nothing gets replayed, because the `unresolved` status blocks re-approval. But the card asks the user to reconcile an operation whose id was never stored. Fix: derive `op_id` from `key`, or store it on the intent or receipt, and wire up a status path.

### F3 — A refused or failed approved MCP call leaves the request stuck in `approved` (medium)
`bound_requests.py:687-703`.

- **Exceptions after the reservation:** by the time `approved_call` runs, the intent is `sent` and the request is `approved`. Any exception it raises escapes `_decide`: `RequestRefused` from `_current` inside the new guard when the owner presses Stop mid-call, `McpError("MCP execution authority changed")`, a stale catalog, `GrantResolutionError`, or protocol errors. The result:
  - no receipt is written and the task is never woken;
  - the request stays `approved`, so later decides just return it;
  - `inline_requests.py` catches only `RequestRefused` and `ControlUnavailable`, so the route returns a 500.
  
  The non-MCP effector returns `error_kind` results instead of raising. The fix is to catch these: refusals before anything is sent → `failed`/`dry_run`; after sending → `unknown` with the `op_id`.
- **Exceptions from validation:** `_authority` → `policy` → `validate_packet` raises `McpError`, not `RequestRefused`. If the catalog refreshes between the card being captured and approved, the user gets a 500 instead of "Preview changed" at line 594. If it raises at the second `_current` (line 681, after the `approved` commit), the request is stuck as above. It fails closed, but wrongly.

### Non-blocking
- **F4:** `refresh_catalog` (`ta_capabilities.py:81`) contacts every MCP server on each `catalog` call. One unreachable server, or a failed compare-and-swap write, raises out of the call, and the whole `ta` catalog fails, platform tools included.

### Checked and sound
- **Activation binding:** the attachment is bound to the answered request. `activation_complete` needs the request answered, the same `mcp_url`, and the server-written `mcp_incarnation`/`mcp_connection` values, and `local_operation` makes the endpoint and request id immutable after the draft stage.
- **Stale or forged catalogs:** every call rediscovers tools and checks `catalog_hash`, so a catalog an agent forged through the capability write can't run.
- **Approvals:** standing scopes are refused for MCP (`approval_scopes.py:37`), and `HAND_OFF`, soul authority and consent are re-checked on the approved path.
- **Stopped tasks:** `require_current` refuses activation when the originating task has stopped or expired.
- **Event loop:** `asyncio.run` only runs from threadpool contexts (the approval door and the OAuth exchange).
- **Owner identity:** `approved_call`'s `current_identity()` is the session owner, via `identity_context` in `inline_requests.py`.

### Tests
`tests/test_mcp_connect_flow.py` skips all 5 tests on Windows (POSIX-only), so I have no local pass. The Linux run you have going is the only real check.

VERDICT: ADAPT
