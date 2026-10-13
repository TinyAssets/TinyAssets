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
