Still reviewing: checking whether the OAuth resource binding swaps out the owner's endpoint allowlist instead of intersecting with it.

Checking the broker fence's lock ordering against the new attachment lock for deadlocks, then I'll write up findings.

**Note:** the working tree changed while I was reviewing. The uncommitted changes were committed as `3fefed011b`, and the foundation was merged in as `7cb44172be` (on top of `dc34dd2b10`). The line numbers below are from the current HEAD. The code I reviewed is the same.

I found no floor break that can be exploited through a reachable path today. `offer["resource"]` is only produced by `connection_oauth/mcp.discover()`, which nothing calls yet. Directory and discovered offers from `resolve_offer` never carry a resource. `RemoteMcp` is not wired to any caller. Findings 1 and 2 are latent, but they sit on the egress and activation boundaries, so they must be fixed before task 1.2 or 1.6 wiring.

### 1. Resource-bound tokens skip the owner's endpoint allowlist (floor, latent)
`tinyassets/storage/outbound_connections.py:1407-1417`
- **Problem:** when the token has a `resource`, the code replaces `resource.allowed_endpoints` with one endpoint built from `original.resource`, instead of narrowing the owner's list. The driver then checks only that replacement (around `outbound_connections.py:4515`). The owner's approved HTTP allowlist is never checked against the token's resource.
- **Trigger:** a token stored with a `resource` outside the backing connection's allowlist. Once `discover(endpoint, …)` is wired, the endpoint comes from paste or discovery. A request whose `url` equals that resource then goes out with the bearer token, even though the owner never approved that host or path. That breaks design.md L73 ("validate discovered endpoints through broker policy") and L77.
- **Fix:** before replacing, run the original check: `_enforce_endpoint_allowlist(_parse_canonical_https_url(original.resource, allowed_ports=frozenset({443})), verb, resource.allowed_endpoints, resource.access_mode)`. The narrowed list is then an intersection, not a substitute.

### 2. The agent can mark its own attachment `active` (floor, exposed partial path)
`tinyassets/mcp_attachment.py:124-136`
- **Problem:** the daemon-facing broker capability op `capability_kind="mcp"`, action `configure`, accepts any next revision in any state. An agent can therefore move `draft → connecting → active` and set `protocol_version` and `catalog_hash` itself, with no owner decision or coordinator.
- **Impact today:** none on egress, because the backing HTTP grant already allows POST to that endpoint.
- **Risk:** it contradicts design.md L17, L19 and L31: activation must go through the owner's activation policy and coordinator, and must "never create a plausible active connection". Any later card or ta consumer that reads `state == "active"` as owner-approved would trust a value the agent set itself.
- **Fix:** on this path, refuse a `value.state` of `active`, and refuse a change to `endpoint` after draft, until the coordinator-only path exists. Allow only draft, connecting, failed and revoked, plus the protocol and catalog fields.

### 3. Most `tools/call` failures after the send look definite, not uncertain (correctness, duplicate side effects)
`tinyassets/mcp_remote.py:149-179`
- **Problem:** only "stream ended without a result" raises `AmbiguousProxyOutcome`. Every other failure after the POST was written raises an ordinary `McpError`, which reads as a known failure. That includes:
  - a 5xx, 408 or 429 status from a proxy;
  - invalid JSON or an oversized event;
  - an ID mismatch or duplicate response;
  - a server-initiated request such as `ping`;
  - `_check_authority` failing partway through the stream.
- **Trigger:** the server runs the tool, then sends a malformed or oversized SSE event, or a `ping`. The caller sees "MCP tool call failed" and an owner or retry path calls again, so the side effect happens twice. That breaks design.md L44.
- **Fix:** for `method == "tools/call"`, convert any exception raised after the broker stream is admitted into `AmbiguousProxyOutcome`. Keep two exceptions: a JSON-RPC `error` response, and the 401/403 or session-404 cases that happen before execution.

### 4. After `SignInRequired`, the instance stays stuck in a half-cleared state (correctness)
`tinyassets/mcp_remote.py:133-135`
- **Problem:** this branch clears `_session` but keeps `_version`, `_tools` and `catalog_hash`.
- **Impact:** after the user re-authenticates, `discover()` skips initialize because `_version` is still set. It sends `tools/list` with no session, the server refuses, and `McpError` is raised every time. Meanwhile `call()` with the old `catalog_hash` passes the stale-catalog check and posts without a session.
- **Fix:** reset everything, the same way the 404 branch does.

### 5. The session-leak check rejects valid servers (correctness)
`tinyassets/mcp_remote.py:160`
- **Problem:** the check is a substring match on the whole message (`self._session in json.dumps(message)`). The spec allows short session IDs made of any visible ASCII, such as `"1"` or `"abc"`. With an ID like that, ordinary results that contain the string are refused.
- **Fix:** only apply the check when the session ID is at least ~16 characters, or drop it. The session is not a TinyAssets secret, and the broker's scanner already covers credentials.

### 6. A server-supplied regex can stall the daemon's event loop (DoS)
`tinyassets/mcp_remote.py:251`
- **Problem:** `jsonschema.validate` runs synchronously inside the async `call`. It applies untrusted `pattern` and `patternProperties` values from the remote server's catalog to arguments the model writes.
- **Trigger:** a malicious server publishes `"pattern": "(a+)+$"` and the model sends a long near-match. The daemon's loop stalls for every turn of that owner.
- **Fix:** run validation with `asyncio.to_thread` and a timeout, or refuse catalogs whose schemas contain `pattern` or `patternProperties` that aren't simply bounded.

### Areas checked with no findings
- **Broker locks:** the attachment lock, fence and SQLite ordering has no deadlock cycle. `bound_send` checks the endpoint, incarnation and revision while holding the lock that metadata writes also take. Revoking or deleting the backing connection blocks the send through the `JOIN` on `revoked_at` and `incarnation`.
- **Peer check:** `verify_peer` closes the connection when verification fails.
- **Token refresh:** the bundle keeps its `resource` on refresh.
- **OAuth discovery:** protected-resource discovery requires an exact resource match and uses the SSRF-hardened `request_json`.
- **Client metadata document:** its `redirect_uris` match `redirect_uri(public_resource)`.
- **Schema refs:** `$ref` network retrieval is refused.
- **SSE parsing:** the CR/LF handling and size bounds hold.
- **Plugin runtime mirror:** matches the source.
- **Colliding lanes:** none beyond the ones you already documented.

VERDICT: ADAPT
