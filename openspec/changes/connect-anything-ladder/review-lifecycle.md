Still reviewing — checking whether the broker path in `_remove_http` mutates before the detach-only branch.

Checking the new tests to confirm UI-originated connect works end to end, then I'll run that one test file.

**Cross-family review: MCP lifecycle, accounts and secret entry (draft #4496)**

I didn't run any tests. The broker paths use unix sockets, so a Windows run would mostly skip and wouldn't count as a pass. This review reads the code only and relies on the Linux results you reported.

**Finding 1: correctness and data loss (ADAPT). A connect answer can re-key an existing plain HTTP connection that has the same label.**
- `tinyassets/api/pending_requests.py:3379-3394`: the new answer-time `account_label_in_use` guard only refuses when a *prior MCP attachment* exists for that label. If the label belongs to an existing plain HTTP connection with no attachment, `prior is None` and the code falls through to `connect_http`.
- `tinyassets/api/http_connection.py:965-971` then deposits *or rotates* the secret. Adding an endpoint counts as an extension, not a conflict (`:661-680`).
- Result: the old account's key is overwritten, its existing graphs now send the new account's key, the MCP endpoint is added to its allowlist, and MCP attaches to it. This contradicts the stated decision that each account is a separate label.
- How it happens:
  - Most likely, the agent-originated connect path (`_validated_connect`, the #4469 sheet) never goes through `offer()`'s label check in `onboarding/mcp_connect.py:17`. Example: the agent asks to connect MCP as `github` (`mcp_url=https://other-host/mcp`, bearer) while the owner already has an HTTP connection named `github`. The owner approves the sheet and pastes a key, and the existing `github` key is replaced.
  - Less often, the Account form loses a race: `offer()` checks the label, then someone creates the label before the answer arrives.
- Fix: at answer time, refuse when the label's HTTP connection exists and is not this request's own earlier deposit. Retries still need to reconcile, so record the deposited `connection_id` and incarnation on the request row before calling `activate`, and treat "exists, no attachment, not recorded on this request" as `account_label_in_use`.
- Test gap: the only `account_label_in_use` test goes through `owner_control` and the offer path (`tests/test_mcp_connect_flow.py:471`). Nothing exercises the answer-time guard.

**Finding 2: low severity, fails closed, not blocking. An unhandled exception on the detach path returns a 500 instead of a 409.**
- `mcp_runtime.detach` raises `McpError` when no attachment exists or the incarnation changed. `metadata` can also raise `GrantResolutionError` when its compare-and-swap loses a race.
- `_remove_http` (`http_connection.py:1159-1164`) and `onboarding/connections.py` `run()` don't catch either one.
- Example: a crafted `detach_mcp` request for a plain HTTP label. The UI only shows the button when `row.mcp` exists. No state changes and the UI says "unconfirmed". It's still worth mapping these to `connection_changed`.

**Finding 3: low severity, the consent text is accurate only for the MCP path.**
- `_grant_sentence` (`pending_requests.py:1909-1910`) says "Inject the API key only in the X header."
- The pinning is enforced for MCP (`mcp_attachment.py:241`, `mcp_remote.py:171`). But the HTTP connection the approval creates doesn't pin the header name: `authenticated_external_call` can still send the same key to the same approved endpoint under a different allowed header name (`outbound_connections.py:2296-2301`).
- The key never reaches another host, so this doesn't cross the floor. The sentence just promises more than the HTTP connection enforces. Either say "MCP sends the key in the X header" or pin the header name on the HTTP connection.

**Verified sound**
- **Cross-owner access:** `connections`, `metadata` and `authorized_connection` are all scoped to the principal. `_remove_http` refuses `owner_user_id != actor`. The OTHER account gets a 409 (test at `:483`).
- **Owner-session controls:** `connect_mcp` and `detach_mcp` both require `owner_sessions.require` with the exact origin, the cookie and a matching owner. A request with no cookie gets a 403.
- **Stale incarnation:** detach checks the observed incarnation against the current one twice, once in `_remove_http` and once under `control(home)`.
- **Tombstone and approval reuse:**
  - The tombstone bumps the revision and clears tools and the catalog hash, so in-flight `bound_send` calls fail.
  - An `activate` call that's mid-discovery loses its compare-and-swap (`expected=raw`).
  - Re-activating returns "reconnect" (test at `:433`).
  - `local_operation` refuses to change the endpoint, header or request after the draft stage.
- **No second approval system:** this uses the existing `request_from_user` sheet, and `require_current` lets Settings-origin rows through. I see no collision with another lane.
- **Header slot authority:** the header is validated by both `_reject_forbidden_header_name` and the new denylist. Whether a header name is set has to match `auth_scheme == "header"` on both the request side and the broker side. The broker refuses any change to `header_name` (parametrized test at `:490+`).
- **Merge `80aa3f9ea7` (`aclient.py`):**
  - Moving `verify_peer` inside `try/except OSError` changes one thing: an `OSError` from a peer-identity mismatch now surfaces as `ProxyRequestError` ("broker unavailable"). It still fails closed and the writer is still closed.
  - `for_owner` now passes `refresh_factory`, and `prepare` returns `None` for bearer and header connections.
  - `mcp_binding` and `refresh` both reach `OPEN`.
- **Remote connect still works:** both bearer and header schemes reach `active`, and the call dispatches (`:510-520`).
- **Credential exposure:** the secret doesn't appear in the saved request, the answer or the result (`:521`). The UI renders everything with `textContent`.

VERDICT: ADAPT
