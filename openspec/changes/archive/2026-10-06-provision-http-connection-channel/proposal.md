# Provision a generic http connection so a user can build an outbound channel

## Why

A user must be able to build a channel (Slack, any HTTP API) that their universe
can **act on** — the founder's "add channels through the channel-agnostic node"
goal. The whole channel-agnostic substrate already exists and is reachable via
the connector: the ONE generic outbound effector
(`authenticated_external_call`), the SSRF-hardened broker, the inbound webhook
receiver, per-connection endpoint allow-lists, and the effector-consent + soul
authority gates. The single missing reachable verb is **creating the generic
`http` connection**: depositing its credential and binding it to the universe.
Today the only caller of `ConnectionLedger.create_connection` is the GitHub-Pipes
path (`api/cloud_connections.py`); there is no way for a user to provision a
plain `http` connection. So outbound channels cannot be built at all.

## What Changes

- Add `write_graph target=connection operation=connect_http` → a new
  `tinyassets/api/http_connection.py::connect_http` handler that, owner-only:
  1. deposits an `http` credential into the per-universe vault
     (`write_credential_vault([{credential_type:"http", service:<key>,
     destination:<key>, token:<secret>}])`), forming `credential_ref =
     vault://http/<key>`;
  2. validates + normalizes caller-supplied `allowed_endpoints` (host lower-cased,
     `/`-rooted path templates, explicit method set) — the egress boundary;
  3. `ConnectionLedger.create_connection(connection_type="http", auth_scheme=…,
     allowed_endpoints=[…], credential_ref=…, destination=<key>, …)` +
     `grant_connection(...)` bound to the universe, both idempotent with
     deterministic ids and conflict-checks (mirroring the GitHub-Pipes path);
  4. returns a redacted projection + a `next` hint to grant effector consent for
     the destination. The secret is never echoed.
- Auth gate mirrors `connect_llm`: authenticated + an explicit `admin` ACL row
  (not the public→read short-circuit), uniform `not_found` on denial.
- `auth_scheme` restricted to the schemes the general vault resolver actually
  supports single-secret: `none | bearer | basic | header` (defer `oauth1a`,
  which needs 4 secrets the resolver can't return).

## Impact

- New capability spec: `outbound-connection-provisioning`.
- Affected code: `tinyassets/api/http_connection.py` (new),
  `tinyassets/universe_server.py` (dispatch), packaging mirror; tests.
- **Scope = Slice 1 (connector only).** Exposing this + `write_graph
  target=branch` on the SERVED surface (so the universe builds channels itself)
  is Slice 2, deferred.
- **Deploy prerequisite (host-action):** outbound effects fire only when
  `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` is truthy in the daemon env
  (via `apply-daemon-env`); connect_http can create the connection regardless,
  but a live post needs the flag on.
- Authority/credential-sensitive (deposits + grants a credential) → Codex shape
  review before build; build/effect verified live before "done".
