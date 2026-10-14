## Authority

A key resolves to `Identity(owner, metadata={"auth_provider": "api_key",
"outside_origin": bound})` with the same base capabilities an AuthKit outside
client gets. `bound` is the key's outside client and family, admitted against
`outside_grants` at the door. Because the identity carries `outside_origin`:

- each route first checks the key's exact grant for
  `(command center, agent, handle)` and records it as `outside_resource`, so
  `check_resource` confines indirect ids (run ids, branch ids) to that command
  center;
- queued runs capture the outside origin and re-admit on resume
  (`run_identity`), so revoking a key also stops its saved work;
- `TINYASSETS_OUTSIDE_DENY` and the durable outside switch close the door for
  every key at once. **While the outside switch is off, every key is refused.**

## Credential separation

`/api` accepts API keys and nothing else (OAuth bearers, app sessions and
the canary get 401). A key is never sent to the OAuth provider, so it is never
an MCP or app credential. Key management needs the protected owner session; a
bearer, including a key, cannot mint or revoke keys.

## Key format and storage

`ta_key_<16 hex id>_<43 urlsafe secret>`. The store keeps
`key_id, owner, name, sha256(secret), created_at, last_used_at, revoked_at`.
Resolution compares digests in constant time. At most 25 live keys per owner.

## Owner request card (local follow-up)

The agent raises the existing pending request with action `credential_request`,
provider `tinyassets`, consumer `name`, and exact `scopes`. The platform captures
the action in the existing protected request store and renders its own consent
text in Needs You. A digest binds the create click to the reviewed action.
Only the protected
owner API-key route may create the key; ordinary request answers cannot do so.
The first provider is TinyAssets; other providers keep their existing secure
connection flow until a provider-supported issuance adapter exists.

Request-bound minting derives one key id from owner, center and request id.
The key transaction refuses to mint that id twice, including after revocation.
The browser receives the secret once, keeps it only in the open card, and clears
it on close, navigation, account change or page exit. Polling never closes an
unfinished card. A later preview returns only metadata and offers revoke if
the one-time response was lost. Done resolves the request with key id/status
only; no secret reaches request history, answer delivery, telemetry or chat.
REST capabilities are read_graph, converse and run_graph on main; full access
means all currently supported REST operations, not MCP parity or event delivery.
Existing create/list/revoke clients remain compatible. No live grants or
publication are part of this local implementation.

## Transport

Reads call `universe_server.read_graph`, the function the `read_graph` MCP handle
runs, so an outside key reads exactly what an outside MCP client reads. The
command-center listing names no single command center, so it checks the key is
live and then keeps only the command centers where the key's exact `read_graph`
grant admits, one by one; nothing outside the grant is listed. A body that is
not a bounded JSON object is 400. Runs call `universe_server.run_graph`.
Messages call `universe_server.converse` under a one-request provider authority
minted exactly as the MCP middleware mints it. Handle results are returned as
JSON; a result with `error` is 404 (not found) or 400.
