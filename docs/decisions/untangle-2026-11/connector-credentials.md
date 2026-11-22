# connector-credentials as built (2026-11-22)

`untangle/connector-credentials`, slice C1 of `packages_connectors_research.md` §2 ("C1 service
egress + key connectors"), owed by D4 (`agent-mcp.md`): the agent's own MCP servers get their
credentials the connector way, by the coordinator's call (`next_builders.md`, "Agent
definition"): `mcp.json` lists URLs only, egress injects the sealed key, and no credential is
ever in the sandbox. The key path only; OAuth connectors are C2.

## Research first (Fact 14), read 2026-11-22

What the research left to this slice, each from the code at `d7fbb0519` or a primary source:

1. **Where the sealed key lives.** The model keys' own path does everything a key needs
   (`gateway/src/keyring.ts` `seal`/`open`, the owner's `connection_secrets` row, the blob bound
   to owner, connection and version, crypto-deleted at revoke). A connector is a connection:
   the same `connections` row, kind `api_key`, the same door steps (`connectionPrepare`,
   `GATEWAY.seal`, `GATEWAY.verify`, `connectionCommit`). No second store. What blocked it was
   only the sealed shape: `Upstream` required a model API (`egress-scope.ts`), and the
   owner's admission refused any call that was not a model's (`egress_unmetered`).
2. **How a key reaches the server.** Two ways need no host of ours:
   - **E2B's per-host header transform** (`tasks.md` step 5 item 3 names it; the egress token
     already rides one, `network.ts`): E2B's proxy would set the header on every request to the
     server's host. Rejected: the plaintext would have to reach the owner's object to write the
     network document (its "no plaintext" rule, `connections.ts`), it would sit in E2B's sandbox
     info, readable by whoever holds the project's E2B key (`network.ts`'s own note on the egress
     token), and a transform binds a host, not a URL: every path and method there gets it.
   - **The egress door** (the research's §2c, the vault model: Anthropic Managed Agents' MCP
     credentials are "keyed by an `mcp_server_url`... the token is injected automatically",
     [V platform.claude.com/docs/en/managed-agents/vaults.md]). The engine is handed the server
     as a URL on the run's own egress path, `…/r/<run>/<secret>/c/<connection>/<server path>`,
     the door the model clients already use; the gateway's `forward` strips every inbound
     credential and puts the sealed one in. The key stays in the gateway. Chosen.
3. **Which header and scheme.** Servers differ: the MCP authorization spec has the client send
   `Authorization: Bearer <token>` and never in the query (2025-11-25, Authorization, "Access
   tokens MUST NOT be included in the URI query string"), while the MCP Registry's own example
   remote declares `X-API-Key` with `isSecret` (`registry/docs/reference/server-json/
   generic-server-json.md`). So the header and scheme are the owner's data for each key, sealed
   with it, never a vendor's code. The app's form fills the spec's own pair in, for the owner
   to change.
4. **What stops a key for host A going to host B.** The seal: a service's upstream is its one
   URL, sealed as one endpoint (that origin, that exact path, the streamable HTTP transport's
   `POST`, `GET` and `DELETE`, spec 2025-11-25 Transports), its base URL held to it on every
   seal and open (`serviceBase`), and the gateway sends only where `egressTarget` allows, the
   rule model keys already go by. The host must be a name by the egress table's own rule
   (`checkDestination`: no IP address, no name ending in a number), so the key is bound to a
   host the sandbox's network can name and D4's `agentServers` can filter.
5. **Revocation.** The model key's: `connectionRevoke` crypto-deletes the blob in its unit and
   ends the open streams on that connection (`egress.end("connection")`); the next call is
   `egress_connection_refused`, and the next run's server is no longer bound to it.
6. **Logs.** A connector's call is an `egress_reservations` row like a model call's: its
   connection, method, path (the sandbox's request, which never held the key), status and bytes,
   uncharged. The gateway redacts the key from the server's answer, headers and body, as it
   does a model key; no log line carries the value.

## As built

1. **A service in the egress contract (`contract/src/egress-scope.ts`).** `ServiceUpstream`
   `{kind: "service", base_url, auth}` beside a model source's `Upstream`, and `ServiceScope`
   `{kind: "service", base_url, endpoints}` beside `ModelScope` (`EgressScope` the union).
   `serviceSource(url, auth)` makes a key's sealed values from the owner's URL; `keySource` is
   what the gateway's `seal` checks (a model record's, or a service's); `upstream` and
   `egressScope` read either back, each a service held to its one URL. `SERVICE_PROVIDER` is the
   provider a service's connection names, so no model path takes it for a model source.
2. **The gateway (`gateway/src/forward.ts`).** A service's every call on its sealed URL is
   free; a metered forward of one is `unmetered` before anything is sent; its setup check
   (`verify`) opens it and returns its scope and no models, since an MCP server answers only its
   own protocol, with a session (a wrong key is the server's own refusal at the run's first
   call).
3. **The connector door (`doors/src/connect.ts` `connections/connector`).**
   `{url, key, header, scheme, display_name?}`, a model key's steps; the source is the server's
   host (refused past 64 characters, a connection source's grammar), held here to the egress
   table's rule (`checkDestination`; the contract's `serviceSource` refuses only an IP address,
   so the sandbox image's import graph never reaches the policy document). The owner app has a form
   for it under the model sources (`doors/app/app.js`). A commit is held to the provider and
   scope agreeing (`connections.ts`).
4. **The run's grant (`owner/src/launch.ts`, `network.ts`, `runs.ts`).** At admission, in the
   unit that makes the run's network, each server `agentServers` kept whose URL an active
   connector's scope allows (`connectorsOf`, the newest for a URL: a newer key is its rotation)
   is bound to it; the grant records the connectors (`run_grants.connectors`, owner migration
   22), and the start carries those servers on the run's egress path (`throughConnectors`,
   `contract/src/egress.ts` `connectorUrl`). No runner change and no protocol change: the start's
   `mcp_servers` is protocol 3's, a URL.
5. **The admission (`owner/src/egress.ts`).** A call may name the run's model connection or one
   of its connectors, each only in its own role (a connector is a service's key; the model
   connection a model source's); a connector's call is free, on its sealed endpoints, counted to
   the open-call rate, and only while the owner's egress policy as it stands lets its server
   out (`network.ts` `serverUnreached`, the rule `agentServers` keeps it by), so a narrowed
   policy stops a live run's calls through the door as the re-applied network stops direct ones.

## Departures from the research

- **No `agent.json` `connectors` list.** The research put the grant in `agent.json`. Binding by
  the server's URL needs no second list to keep in step with `mcp.json`: a server's URL is
  already the agent's own word for it, and adding or changing a server is already a widening
  the owner approves (`definitionWidening`). The grant is still explicit and per run.
- **No curated service records.** Records for platform-registered clients are C3; a key needs
  none, and E1's non-catalog record path has not landed. A key's sealed values are the owner's
  URL and header, checked by the contract's own rule.
- **Only servers reached by URL.** A stdio server that calls a vendor API itself gets no key:
  that needs substitution at a TLS-terminating proxy (the research's §2c item 8, not built).

## Owed

- [ ] `untangle/connector-oauth`: C2, OAuth connectors (`cimd`, owner `provided`, `dcr`), their
  tokens refreshed under the lease, by the same binding and door.
- [ ] live: at the dark redeploy, one http server bound to a connector reached through the egress
  door on each engine, its key at the server and nowhere in the sandbox (B5's check, with a key).
