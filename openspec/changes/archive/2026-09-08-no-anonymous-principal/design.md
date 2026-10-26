# Design: no anonymous principal

Revised after Codex design round 1 (2026-09-02, ADAPT): exempt paths
corrected to the routes that exist, `runs.actor` and the Source-event thread
named as identity sources, the canary kept narrow, `/pulse` moved under the
proxied prefix with `image_tag`, and the PR-1 boundary widened to what must
land together.

Extended under the founder-approved ownership takeover on 2026-09-03. The
completed three review records remain preserved and no fourth review is
opened. New live evidence showed that MCP hosts own separate credential planes,
so this change includes the hosted connector's metadata + runtime linking
contract and requires direct, ChatGPT and Claude sessions for one account to
converge on one principal and home universe.

## D1. Identity is present or the request is refused

`tinyassets/auth/middleware.py`

- `_current_identity: ContextVar[Identity | None]` with default `None`.
- `auth_middleware(token)`: no token -> `_current_identity.set(None)`,
  return `None`. Present-but-invalid token -> `None` in every mode (today dev
  mode downgrades it to anonymous).
- `current_identity()` -> `Identity`; raises `PermissionError("Authentication
  required")` when unset. `current_identity_or_none()` exists for the places
  that legitimately branch on presence: the ASGI challenge path, status's
  `request_identity` block (`bearer_present`), and the app's
  `_app_identity_required` gate (which imports the deleted sentinel today
  and moves into this PR).
- `AuthContextMiddleware.__call__`: seeds `None`, not a sentinel. After
  `auth_middleware`, if identity is `None` and the path is not in the exempt
  table (D3), answer 401 with the `WWW-Authenticate` challenge
  (`invalid_token=True` when a token was present, else the plain challenge).
  The anonymous-write pre-dispatch classifier, the 1 MiB anonymous body cap
  and `register_anonymous_write_challenge_tool` are deleted: there is no
  anonymous request body to classify.
- A cached hosted connector can issue `tools/call` before it has linked a
  bearer. That exact unauthenticated shape is an authentication-bootstrap
  response, not a tool dispatch: it returns an MCP error result carrying
  `_meta["mcp/www_authenticate"]`. `initialize`, `tools/list`, non-tool JSON-RPC,
  malformed requests, and oversized bodies still receive the transport
  challenge/refusal. No handler runs and no principal is constructed.
- `require_auth` / `require_action_scope`: the `user_id == "anonymous"`
  branches go; `current_identity()` already refused.

`tinyassets/auth/provider.py`

- `ANONYMOUS` deleted. `HOST` stays (a named identity).
- `AuthProvider.challenge_unauthenticated()` deleted: challenging is the
  transport rule, not a provider option. `resolve_always_writes` and
  `writes_require_identity` stay only as scope-enforcement switches.
- `DevAuthProvider.resolve_token(token)`: returns a named local identity
  from `UNIVERSE_SERVER_DEV_USER` for any token; `create_provider()` in dev
  mode raises at startup when that variable is unset. A missing token in dev
  mode is still `None` -> 401: local clients send any bearer.
- The `oauth_sessions.user_id ... DEFAULT 'anonymous'` column default becomes
  `NOT NULL` with no default (migration fails loudly naming any row that
  carries it).

`tinyassets/api/permissions.py`

- `current_request_actor_id()` returns the subject or raises; it never
  returns `"anonymous"`. `is_authenticated_request()` becomes
  `current_identity_or_none() is not None`. `current_actor_id()` is the same
  as `current_request_actor_id()`; one of them is deleted.
- `OperatorRequestAdmissionVerdict.actor_id / tenant_id` are never defaulted.

`tinyassets/api/engine_helpers.py`, `tinyassets/identity.py`, and every
`os.environ.get("UNIVERSE_SERVER_USER", "anonymous")`

- Deleted. The actor is the authenticated subject, or the write refuses. The
  git author slug in `identity.py` is derived from the bound identity only;
  with none bound it raises rather than falling back to an anonymous slug.

`tinyassets/engine_mcp_server.py` (`_bind_founder_identity`) and
`tinyassets/onboarding/__init__.py` (`_app_identity_required`)

- An empty `actor_id` is `_binding_error`, never a bound stand-in. The app
  gate compares against `None`, not the sentinel. Both land in PR 1 because
  both import the deleted symbol.

## D2. Runs and events carry their principal explicitly

`runs.actor` is an identity source, not an author field: it has a SQL
`DEFAULT 'anonymous'`, `create_run(actor="anonymous")` defaults, and
`canonical_dispatch` reads it back as authority context. In PR 1:

- `create_run` and every caller require `actor`; the column loses its default
  (migration fails loudly on a row that carries `'anonymous'` and lists it;
  such rows are historical and are never re-dispatched).
- Scheduled runs and the automations consumer already carry a stored owner
  principal and pass it to `run_fn`; the **Source-event thread does not**
  (`webhook_inbound._emit_source_event` builds `SchedulerEvent(event_type,
  event_id, payload)` with no identity, `scheduler._dispatch_event` calls
  `run_fn` without `principal_id`, and `api/runs` falls back to a request
  identity that does not exist on that thread). The hook itself records no
  owner today: `webhook_hooks` stores universe, branch and source ids only,
  and `_action_create_source` knows the caller transiently. So (Codex round
  2): `webhook_hooks` gains `owner_principal_id`, written by
  `_action_create_source` from the authenticated caller and returned by
  `resolve`; `SchedulerEvent` gains `owner_principal_id`, stamped in
  `_emit_source_event` from the resolved hook; it flows `_dispatch_event` ->
  `_inbound_event_run_fn` -> `enqueue_universe_branch_run` ->
  `_bind_run_provider_call`, whose explicit-principal path already needs no
  lookup. BOTH mint paths record the owner (Codex round 3): `_action_mint_webhook`
  (a plain branch hook, delivered directly by `handle_hook` ->
  `enqueue_universe_branch_run`) and `_action_create_source` (a Source, delivered
  through the bus); the direct path passes `principal_id` from the resolved hook
  exactly as the bus path does. A legacy hook with no recorded owner refuses to
  deliver, logging the hook prefix (fail closed; the owner re-creates it). This is the fix the
  2026-09-02 event-subscription concern asked for, and it lands with D1
  because the raising `current_identity()` otherwise breaks that path.
  Binding the owner in the HTTP `ContextVar` alone does not cross the
  queue/thread boundary.

Authors: `BranchDefinition.author`, `NodeDefinition.author`, `extensions`
authors, `branch_versions` publisher, `catalog/serializer` author, `market`
claimer / staker / caller, `runtime_ops.owner_actor`, `selector_dispatch.actor`,
`interlocutor.actor_id`: no retired-marker default (continuation 3). A missing
author at write time raises; readers normalize the historical marker through
the dependency-free `tinyassets.principals.named_principal` compatibility
decoder and treat the row as unowned, never as a principal that can match a
caller. New source and schema defaults use the empty string. Interlocutor `T0`
remains only as an internal public-projection classification; it carries an
empty actor id and is unreachable through unsigned MCP transport.

## D3. Exempt paths keep their own named authentication

The routes that exist today, not a sketch:

| path | who authenticates | principal bound |
|---|---|---|
| `/.well-known/*` and `/mcp/.well-known/*` (both are mounted by `starlette_discovery_routes`; production challenges advertise the `/mcp/` variant; the `/oauth/*` descriptor in `create_wellknown_routes` is unmounted and gets no exemption) | OAuth discovery | none: no handler reads state |
| `/mcp/app` static shell; `/mcp/app/token` (PKCE exchange, refresh, logout) | the app's PKCE flow / refresh cookie | the signed-in user, or the flow itself; every other `/mcp/app/*` route requires the bearer through `_app_identity_required` |
| `/mcp/connect/*` (the existing traversal-safe predicate, not a prefix) | connect-deposit session | the depositing user |
| `/hooks/inbound/<id>` (the existing exactly-one-segment predicate, not a prefix) | per-hook secret | the hook's OWNER, stamped on the emitted event (D2) |
| `/mcp/app/billing/webhook` | Stripe signature (already exempt with its own auth) | none: the handler binds the customer from the event |

Everything else, including `POST /mcp` `initialize`, is 401 without a valid
bearer. The table is one predicate with a test per row, and no row is a
wildcard prefix (Codex round 2: literal prefixes would open deeper or
crafted paths that the existing predicates refuse).

## D4. Probes are the `canary` service principal, kept as narrow as today

Codex: a coarse `read`/`list` canary would read any public universe, and the
handle layer does not enforce capabilities uniformly. So the canary does NOT
gain a capability set. It keeps its existing argument fence and gains a
**central service-principal handle allowlist** in the middleware:

- The bearer `TINYASSETS_WIKI_CANARY_TOKEN` resolves to identity `canary`.
- A request under it is admitted only for: `initialize`,
  `notifications/initialized`, `tools/list`, `tools/call get_status` with no
  arguments, `tools/call read_graph target=status`, and the wiki canary's
  exact `write_page` / `read_page` shapes it already enforces. Anything else
  is refused before dispatch with a scope error.
- `GET /mcp/pulse` is also admitted under that bearer and returns the four
  release fields. The website never receives this service credential.
- Where: in `AuthContextMiddleware.__call__`, after the bearer resolves to
  `canary` and before `await self.app`, validating every item of a single or
  batch JSON-RPC body before replaying it (Codex round 2). That is outside
  FastMCP dispatch, so neither the legacy fat tools nor
  `_DeprecatedToolVisibility` (which filters `tools/list` only) can bypass it.
- Every probe script in `scripts/` (the nine in the inventory) sends it;
  without the variable set they exit 2 naming it.
- `scripts/mcp_public_canary.py`: an unauthenticated `initialize` MUST answer
  the 401 challenge (assertion 1); `--assert-handles` proceeds with the bearer.
- The 14 workflows pass the secret (`p0-outage-triage.yml` and
  `restart-daemon.yml` included).

## D5. Protected `GET /mcp/pulse` and the website

- Under the `/mcp` prefix because the Cloudflare Worker proxies only `/mcp*`.
- Body: `git_sha`, `image_tag`, `deployed_at`, `uptime_seconds`. `image_tag`
  is included because `scripts/deployed_sha.py` corroborates `git_sha` with
  it and exits "cannot tell" without it; the gate is not weakened.
- Served through the auth middleware. Only a valid user bearer or the named,
  exact-purpose canary bearer reaches it.
- `scripts/deployed_sha.py` reads it with the canary bearer instead of
  `tools/call get_status`.
- Public website live clients do not call it without a user bearer. Until the
  site has a signed-in session they show the checked-in snapshot and an honest
  sign-in-required state. The playground's first live response is the OAuth
  challenge.

## D5a. Hosted connector OAuth contract

The canonical seven tools advertise one OAuth-only security scheme with the
OIDC scopes WorkOS can issue: `openid`, `profile`, `email`, `offline_access`.
Both the top-level `securitySchemes` field and `_meta.securitySchemes` mirror are
emitted because the installed MCP model permits extension fields and older
OpenAI hosts read the compatibility mirror.

Transport 401 remains the normal pre-session path. For a hosted connector that
already cached the tool catalog, an unauthenticated `tools/call` returns a
bounded JSON-RPC error result with `isError: true` and
`_meta["mcp/www_authenticate"]`. It includes the routed protected-resource URL,
an OAuth error code, and a human-readable description. The requested tool is
never dispatched. A batch is eligible only when every item is a `tools/call`;
mixed or malformed input gets the transport challenge.

## D6. Status reports presence, never a stand-in

`api/status.py`: `request_identity.principal_fingerprint` from the
authenticated subject only; `bearer_present` stays; no anonymous session
branch.

## D7. What lands where

**PR 1 (this change):** D1, D2's run/event parts, D3, D4, D5, D6, and the
tests they need. Codex round 1 established that the sink deletion cannot
be a separate green PR for: `_app_identity_required`,
`_bind_founder_identity`, `runs.actor` defaults and callers, and the
Source-event propagation; those are in PR 1.
**Continuation 2:** hosted connector metadata + runtime linking challenge and
the corrected direct/ChatGPT/Claude account-convergence matrix.
**Continuation 3:** the remaining string gates and author defaults, protected
canary pulse, and the `grep anonymous tinyassets/` zero-line test.
**Continuation 4:** spec sync, environment-variable catalog, Hard Rules 11 and
14 bearer wording, archive.

## D9. The cutover seam is removed

The prior design negotiated with the pre-cutover production by making an
unauthenticated request and temporarily kept `/mcp/pulse` public. The founder's
2026-09-03 platform-wide decision removes that exception. Every current probe
sends the canary bearer and fails loudly when the deployed server cannot honor
it. A rollback to a build that accepts only unsigned probes is an authentication
regression, not a compatible state for the gate to mask.

This deliberately means the new deployed-sha contract is authoritative only
after the protected-pulse build is running. Release automation uses its normal
deployment receipt during that one cutover operation, then Hard Rule 14 resumes
against the protected live endpoint. No code path falls back to an unsigned
`get_status` read.

## D8. Testing

- `signed_in(user_id)` fixture replaces every `_logout()` /
  `auth_middleware(None)` that expected an anonymous read to succeed; those
  tests assert the 401 / `PermissionError`.
- A test per D3 row; a test that a Source event carries and runs under its
  hook owner; a test that the canary bearer is refused on every handle
  outside its allowlist.
- The canary runs against the container oracle with and without the token
  before the deploy PR merges.

## Decisions taken here, flagged for the founder

Codex round 1 listed these as product choices the directive does not make.
Each is decided below with the reason; any can be reversed by the founder.

1. **Canary authority: narrow allowlist, not a capability.** Least privilege;
   the probes never needed more.
2. **Dev principal: `UNIVERSE_SERVER_DEV_USER`, fail-loud, no fixed name.** A
   fixed `local-operator` is a default identity by another name.
3. **Website: checked-in snapshot until signed in; the playground shows the
   challenge first.** Release pulse is not a public exception.
4. **Historical synthetic authors stay unowned and are not rewritten.**
   Rewriting attribution invents provenance. A single read-only compatibility
   decoder recognizes the retired stored marker while no writer can emit it.
5. **`/mcp/pulse` fields: `git_sha`, `image_tag`, `deployed_at`,
   `uptime_seconds`.** Nothing that names a universe or a user.
6. **Historical rows are quarantined, not fatal (Codex round 3).** Sessions
   whose user id is the retired marker are deleted when resolved; run rows with
   an unowned actor remain owner-reachable only when `owner_user_id` proves the
   caller; hooks with no recorded owner refuse delivery with a logged error and
   the caller's uniform 404 until the owner re-creates them. No deployment
   fails merely because historical data exists.
