## Why

The way new users onboard is the founder's top priority, but there was **no
user-facing app** to do it — only the raw MCP connector inside a third-party
chatbot. A founder cannot install → sign in → meet their universe → connect a
subscription → chat from one place they own.

Two hard constraints shaped the answer (founder directive 2026-08-20):

1. **Nothing may depend on a local machine.** "Working" means it works in the
   cloud, for new users, with the developer's computer off. A local proxy or a
   test against a local daemon is a false-green — it skips the exact cloud-deploy
   step where things actually break (the earlier `/mcp/connect` deposit form died
   *on* the container recreate).
2. **The app must talk to the backend only through the seven canonical handles**
   (`converse`, `get_status`, `read_graph`, `write_graph`, `run_graph`,
   `read_page`, `write_page`) — it invents no backend tools.

The naive shape — a standalone web app calling `https://tinyassets.io/mcp` — is
blocked: the live `/mcp` sends no CORS headers (a browser preflight returns 403),
so a cross-origin page cannot call it. This change resolves that structurally by
serving the app **from the daemon itself**, same-origin to `/mcp`.

## What Changes

Add a **daemon-served onboarding web app** at `/mcp/app`:

1. **Same-origin static SPA.** The daemon serves a self-contained single-page app
   at `/mcp/app` — same origin as `/mcp`, so the page calls `/mcp` directly with a
   Bearer token: no CORS, no proxy, no server-side token injection. Served under
   `/mcp/` so the production Cloudflare tunnel (which forwards only `/mcp/*`)
   reaches it with no infra change. It ships in the daemon image and goes live
   purely by deploying the daemon — zero local-machine dependency.
2. **In-browser WorkOS PKCE sign-in.** The SPA runs WorkOS AuthKit OAuth 2.0
   Authorization Code + PKCE entirely in the browser (public client, no secret),
   binding the token to the MCP resource (RFC 8707). The access token lives only
   in the browser (`sessionStorage`) and is sent as a Bearer to same-origin
   `/mcp`. The platform never proxies or stores it.
3. **The onboarding funnel over canonical handles only.** Sign in → a single
   `converse` chat thread rendering the universe's first-person reply verbatim →
   an honest connect-subscription state (the setup-required envelope rendered as
   the universe's own note) that deposits via `write_graph target=connection
   operation=connect_llm` → a `get_status` heartbeat.
4. **Dark-flagged.** `TINYASSETS_ONBOARDING_APP` gates serving (the route returns
   404 until set); the app ships dark and is enabled by an env flip at deploy.
5. **Hardened served page.** Public config only (client id + discovered AuthKit
   endpoints + resource, derived from the connector's own Protected Resource
   Metadata so it cannot drift), a per-request CSP nonce, and a DOM-XSS-safe SPA
   (all universe/user text rendered as text nodes).

The app is a body-surface/window only: no universe logic, identity, or persona
lives in the client (design note `2026-06-30-tinyassets-universe-app-experience`).

## Capabilities

### New Capabilities
- `onboarding-web-app`: the daemon-served, same-origin onboarding SPA at
  `/mcp/app` — dark-flagged serving, in-browser WorkOS PKCE bound to the MCP
  resource, public-config injection, a hardened served page, and the sign-in →
  converse → connect_llm → get_status funnel over canonical handles only.

### Modified Capabilities
- `live-mcp-connector-surface`: the daemon MAY serve additional same-origin static
  routes under `/mcp/` (e.g. `/mcp/app`); such routes are excluded from
  `tools/list`, are never counted among the canonical advertised handles, and do
  not trip the advertised-handle drift canary.

## Impact

- New: `tinyassets/onboarding/__init__.py` (route, dark flag, public-config
  injection, CSP), `tinyassets/onboarding/app.html` (the self-contained SPA),
  `tests/test_onboarding_app.py`.
- Modified: `tinyassets/universe_server.py` `create_streamable_http_app()` — the
  `/mcp/app` route registered before the MCP transport (mirrors the discovery
  routes). `tinyassets/auth/middleware.py` — `/mcp/app` exempted from the OAuth
  challenge (mirrors the `.well-known` carve-out) so the SPA loads in every auth
  mode; its own `/mcp` tool calls stay challenged. Claude-plugin runtime mirror
  rebuilt.
- Deploy-side host-actions (no local step): register/confirm a WorkOS public
  client whose `redirect_uris` include `https://tinyassets.io/mcp/app`, set
  `TINYASSETS_ONBOARDING_APP_CLIENT_ID`, and set `TINYASSETS_ONBOARDING_APP=1`.
- Depends on the `byo-llm-connect-flow` `connect_llm` deposit handler being
  deployed for the connect-subscription step to complete end to end; chat and
  status work as soon as the app route deploys.
- Final acceptance is a real/new user against the DEPLOYED cloud daemon
  (`tinyassets.io/mcp/app`) — never a local run.
