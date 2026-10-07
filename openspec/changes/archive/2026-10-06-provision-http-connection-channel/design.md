# Design — connect_http (provision a generic http connection)

## Handler shape (mirror connect_llm)

`tinyassets/api/http_connection.py::connect_http(*, universe_id="", payload=None) -> dict`,
dispatched from `universe_server.py` write_graph `target=="connection"` as a new
`operation=="connect_http"` sibling of `connect_llm` (before the
`_cloud_connections_impl` GitHub fall-through). Returns `json.dumps(result)`; no
costly/submit/admission wrapper (the vault write takes its own per-universe lock).

### Auth (stronger of the two precedents)
1. `permissions.is_authenticated_request()` + `actor = current_actor_id()`;
   reject empty/`anonymous` → `{"error":"authentication_required"}`.
2. `uid = _request_universe(universe_id)`; require an explicit `admin` row from
   `list_universe_acl(base, universe_id=uid)` (NOT `universe_access_allows`, which
   admits write-collaborators and can public→read short-circuit). Denial →
   uniform `{"error":"not_found","resource":"connection"}` (existence-probe safe).

### Payload (JSON object)
```
{ "destination": "<stable channel key, e.g. webhook:acme>",  # required
  "auth_scheme": "bearer",                 # optional; Slice 1 accepts only bearer
  "secret": "<token>",                     # required; <=200k
  "allowed_endpoints": [ {"host":"api.example.com","path_template":"/v1/messages","methods":["POST"]}, … ] }  # >=1
```
The handler names NO service — the owner supplies the host/path (a neutral example
is shown above on purpose). Validate: `destination` non-empty + used as the SINGLE
key for both the vault record (`service` and `destination`) and the connection
`destination` (keeps the resolver lookup key == consent/soul-authority key — the
map's two-destination alignment). `auth_scheme`, if present, must be `bearer`
(Slice 1); anything else → `unsupported_auth_scheme` (`none`/`basic`/`header`/
`oauth1a` deferred — the resolver returns one secret). `allowed_endpoints`
normalized via the ledger's own `_parse_allowed_endpoints` (host lower-cased, path
template + methods validated) — reject empty (SSRF boundary).

### Steps
1. Vault deposit: `write_credential_vault(_universe_dir(uid),
   [{"credential_type":"http","service":<destination>,"destination":<destination>,
   "token":<secret>}], owner_user_id=actor, universe_id=uid)`. List-wrapped.
   `credential_ref = f"vault://http/{destination}"`. Setting `service==destination`
   makes the upsert key `(http, destination)` distinct per channel AND the
   resolver finds it by `destination`. Map `PermissionError` →
   `credential_ownership_transfer_unsupported`, `ValueError` →
   `connection_setup_invalid`, else `deposit_failed` (fail-closed, atomic).
   auth_scheme=none → skip deposit, `credential_ref="vault://http/"`+none path.
2. `ledger = ConnectionLedger(Path(base)/"outbound.db",
   verify_authenticated_principal=lambda: actor)`.
3. Deterministic ids: `sha256(actor∥uid∥"http"∥destination)` → `http_<32>` /
   `http_grant_<32>` (mirror `cloud_connections._ids`).
4. Idempotent create: `get_connection_resource` first; if None →
   `create_connection(connection_id, owner_user_id=actor, connection_class="http",
   connection_type="http", auth_scheme="bearer", scopes=<method-union>,
   provider="http", destination, credential_ref, allowed_endpoints)`. The connection
   SCOPE is the sorted/uppercased/de-duped union of the endpoints' HTTP methods — the
   verb string the `authenticated_external_call` effector matches the packet `verb`
   against (`verb in resource.scopes`). It must NOT be the literal `("http",)` type
   token (which admits no HTTP verb, so every outbound call fails "verb outside
   granted connection scope"; #2521). `connection_type`/`connection_class`/`provider`
   ("http") carry the type discrimination. Else conflict-check EVERY immutable field
   (owner, connection_type, connection_class, provider, auth_scheme, scopes,
   destination, credential_ref, revoked_at, AND the endpoint allow-list compared as an
   UNORDERED set — `_canonical_policy` sorts endpoints/methods/query names so a reorder
   stays idempotent) → any real mismatch, including a changed endpoint set, returns
   `{"error":"connection_conflict"}` BEFORE the vault write. EXCEPTION (bounded legacy
   migration): a pre-fix row whose only difference is the legacy `("http",)` scope,
   otherwise policy-identical, is UPGRADED in place to the method union
   (`_upgrade_http_connection_scopes`, guarded by `scopes_json = '["http"]'`) rather
   than stranded — deterministic ids + no policy-update path would otherwise make it
   unrecoverable.
5. Idempotent grant: `get_grant` first; if None → `grant_connection(grant_id,
   connection_id, owner_user_id=actor, universe_id=uid,
   unprompted_action_cap=ActionCap("http_calls", <cap>, "requests"))`; else
   conflict-check.
6. Return `{"status":"provisioned","connection_id","grant_id","provider",
   "destination","connection_class","auth_scheme" (read from the resource),
   "allowed_endpoints":[…as_dict],"action_cap","next":[grant effector consent for
   <destination>, enable the outbound flag, build a node with effect
   authenticated_external_call]}`. Never echo `credential_ref`/secret. As-built
   ordering: the full conflict-check runs BEFORE the vault deposit, so a hard
   mismatch never rotates a live credential.

## Decisions / risk handling (from the substrate map)
- **Two-destination alignment:** one `destination` value serves the vault key,
  the connection destination, and the later consent/soul-authority key. Documented
  + enforced by construction.
- **Upsert-vs-resolver key mismatch:** solved by `service==destination`.
- **Allow-list is load-bearing + caller-supplied:** normalized/validated by the
  ledger before storage; empty rejected. This is the real egress boundary.
- **Feature flag:** `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` gates the
  effect at dispatch; creating the connection does not require it. Return does not
  falsely imply live posting — the `next` hint covers consent; the flag is a
  deploy prerequisite tracked as a host-action.
- **oauth1a excluded** (single-secret resolver).
- **Redaction:** only redacted `ConnectionView`/projection returned; secret-free
  error envelopes (mirror connect_llm).

## Out of scope (Slice 2)
Served-surface build verbs (write_graph target=branch + connect_http + consent +
webhook mint on `engine_mcp_server.py`) so the universe builds channels itself;
`oauth1a`/multi-secret; a typed http credential bundle.
