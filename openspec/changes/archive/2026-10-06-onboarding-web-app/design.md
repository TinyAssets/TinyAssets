## Context

Grounded in the built surface on `feat/app-mvp` @ `6bcdcbf0` (2026-08-20),
verified against the live `https://tinyassets.io/mcp` and this worktree's tree.
The change backfills the spec for a surface that was built to a founder directive
under time pressure; it records the as-built design and the load-bearing
decisions so the surface is durable per the spec-driven standard.

### The problem the shape solves

A standalone onboarding web app calling `https://tinyassets.io/mcp` is blocked by
two independently verified facts:

- **No CORS on `/mcp`.** An `OPTIONS` preflight from a foreign origin returns
  `403` (Cloudflare). A browser SPA on any other origin cannot call `/mcp`.
- **The deposit hot-patch was not durable.** The earlier `/mcp/connect/*` browser
  deposit form returns `404` live — its hot-patch was lost on a container
  recreate. Any design validated only against a local process would not have
  caught that; only the cloud-served, ships-in-the-image shape does.

## Decisions

### D1 — Serve the app from the daemon, same-origin to `/mcp`

The daemon serves the SPA at `/mcp/app`. Same origin means the page calls `/mcp`
with a relative fetch and a Bearer header: no CORS, no proxy, no server-side
token custody. This is the single decision that removes every local-machine
dependency — the app is bytes in the daemon image and goes live by deploying the
daemon.

- **Why under `/mcp/`:** in production only `/mcp/*` is routed to the daemon (the
  Cloudflare tunnel). A top-level route (`/app`) would be served by the marketing
  site, not the daemon. `/mcp/app` reaches the daemon with no infra change —
  proven by `/mcp/.well-known/*` and `/mcp/connect/login` both reaching the daemon
  today.
- **Route registration:** a Starlette `Route("/mcp/app", …)` added to
  `create_streamable_http_app()` **before** `*canonical_app.routes`, mirroring
  `starlette_discovery_routes()`. Exact-path routes listed before the transport
  resolve first; this is the same mechanism that already serves
  `/mcp/.well-known/oauth-protected-resource` in production.

### D2 — No auth-middleware change: the app route is anonymously loadable

The SPA must load **before** the user signs in. Verified live: anonymous
`GET /mcp/app` (and other `/mcp/*` subpaths) return `404`, **not** a `401`
challenge — so the auth middleware does not challenge anonymous GETs on `/mcp/*`
in the deployed mode. The onboarding route therefore needs no change to
`tinyassets/auth/middleware.py`. The converse `401` an anonymous caller sees comes
from the pre-dispatch write-tool classifier (a POST `tools/call` on a write
handle), which is exactly right: the SPA calls `converse` **with** a Bearer.

### D3 — In-browser PKCE, token in the browser (not a backend session)

WorkOS AuthKit is a public-client SPA-capable AS: its token endpoint returns
`access-control-allow-origin: *` (verified), so the browser performs the
code→token exchange itself. The access token is held in `sessionStorage` and sent
as a Bearer to same-origin `/mcp`. The token binds to the MCP resource via the
RFC 8707 `resource` parameter on both authorize and token requests, so its
audience matches what `/mcp` accepts. There is no server-side session store,
proxy, or cookie — which structurally eliminates the localhost-CSRF,
server-session-lifetime, and token-in-proxy classes entirely.

- **Public config, no drift:** the route injects `client_id`, the AuthKit issuer,
  the authorize/token endpoints, and the resource — all derived from the same
  Protected Resource Metadata the connector advertises (`app_config()` reads
  `protected_resource_metadata()`). The app's authorization server and resource
  therefore cannot diverge from what `/mcp` itself honors.
- **Redirect URI** is computed client-side as `origin + pathname`
  (`https://tinyassets.io/mcp/app`), so it always matches the serving origin.

### D4 — Hardened served page

The prior local-proxy build was rejected in cross-family review for
architecture-specific issues (localhost CSRF, server-side session store,
manual-token-in-JS, static path traversal, refresh crash). The same-origin design
removes all of them by construction. Retained hardening:

- A **per-request CSP nonce** gates the inline `<script>`/`<style>`;
  `default-src 'none'`, `connect-src` limited to `'self'` + the AuthKit origin,
  `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'none'`. No
  `'unsafe-inline'`.
- The SPA renders **all** universe/user text via text nodes, never `innerHTML` —
  so a hostile `converse` reply cannot inject script and steal the token.
- Injected config JSON is escaped (`<` → `<`, U+2028/2029 stripped) so no
  value can break the script context. Only public values are injected; a
  secret-leak test guards this.
- `no-store`, `nosniff`, `no-referrer` on the response.

### D5 — Funnel over canonical handles only

`converse` (verbatim reply), `get_status` (heartbeat), and
`write_graph target=connection operation=connect_llm` (deposit). When the
universe has no engine, `converse` returns a `held / setup_required` envelope with
a platform-authored `note`; the SPA renders that honestly as the universe's own
voice and surfaces a connect-subscription affordance — never a faked reply. No
other tool is invented or called.

## Risks / tradeoffs

- **Deploy-gated acceptance.** By directive the onboarding funnel is validated
  only against the deployed cloud daemon by a real user. Unit tests + a static
  render check verify the code unit; they are not the acceptance proof.
- **connect_llm dependency.** The deposit step completes only when the
  `byo-llm-connect-flow` `connect_llm` handler is also deployed. Until then the
  app renders the honest setup-required state.
- **Callback query string in logs (low, PKCE-mitigated).** The OAuth callback
  lands on `/mcp/app?code=…&state=…`; if the daemon access-logs query strings the
  code reaches logs. The SPA strips the query immediately (`history.replaceState`)
  and PKCE makes a logged code unusable without the browser-only verifier. Confirm
  the daemon does not retain `/mcp/app` query strings at deploy.
- **Token in `sessionStorage`, apex-origin blast radius (accepted).** The founder
  directive requires the browser to hold the token and call `/mcp` directly (no
  token-holding backend/BFF), so the access token lives in `sessionStorage` on the
  `tinyassets.io` origin. Any same-origin compromise elsewhere on the apex in the
  same tab could read it. Mitigations: the SPA has no DOM-XSS sink (all untrusted
  text via text nodes) and a strict CSP; the token is short-lived and tab-scoped
  (cleared on tab close and on sign-out). A BFF with an HttpOnly cookie would
  shrink the blast radius but is exactly the token-holding backend the directive
  rejects, so it is out of scope. Accepted tradeoff.
- **Sign-out does not end the WorkOS SSO session (mitigated).** AuthKit advertises
  no `end_session_endpoint`, so the SPA cannot RP-initiate a logout. Sign-out
  clears the local token; to stop a shared device from silently re-authenticating
  as the prior user through a surviving SSO cookie, sign-out sets a marker that
  makes the next authorization request use `prompt=login` (forced re-auth). Normal
  first sign-in stays silent.

## Open questions

- Should the app eventually move to first-party native shells (iOS/Android) per
  the app-experience design note, reusing this same-origin `/mcp/app` contract?
  Out of scope here; the web SPA is the fastest cloud-served, usable-today form.
