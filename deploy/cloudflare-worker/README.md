# Cloudflare Worker — `tinyassets.io/mcp` + `/app` path router

**Status:** The canonical proxy shipped after the 2026-04-20 single-entry
cutover. This repository revision also retires `/mcp-directory*` at the edge;
that newer behavior is not production truth until deploy and post-deploy proof
complete. The Worker routes `tinyassets.io/mcp*`, `tinyassets.io/app*`, and the
apex OAuth discovery documents
`tinyassets.io/.well-known/oauth-protected-resource*` and
`tinyassets.io/.well-known/oauth-authorization-server*` while leaving the
GitHub Pages landing intact for all other paths. The discovery wildcards are
the document name only, so `/.well-known/openai-apps-challenge` stays on the
website. Path-inserted discovery (`/.well-known/oauth-protected-resource/mcp`
and `/.well-known/oauth-authorization-server/mcp`) is forwarded to the apex
document the daemon already serves, so a worker deploy answers those URLs
before the daemon image that mounts them directly is live.

**After deploy:** the canonical public MCP URL returns to
`https://tinyassets.io/mcp`. Installed TinyAssets chatbot connectors pointing
at that URL start working again without any user action.

---

## What this solves

Before the Worker:

- `tinyassets.io` apex serves GitHub Pages (landing page).
- `mcp.tinyassets.io` serves the Cloudflare Tunnel → TinyAssets daemon.
- Installed TinyAssets chatbot connectors point at `https://tinyassets.io/mcp`.
- `tinyassets.io/mcp` has no route rule → falls through to the landing origin's 404
  → Claude.ai reports "Session terminated." This was the 2026-04-19 P0.

With this Worker revision deployed:

- Same DNS (tinyassets.io still Cloudflare-fronted).
- Same GitHub Pages origin for apex paths (landing unchanged).
- NEW: the Worker runs on routes `tinyassets.io/mcp*` and `tinyassets.io/app*`,
  plus the apex OAuth discovery documents.
  Canonical `/mcp` and `/app` requests get proxied to `mcp.tinyassets.io`
  (tunnel origin) as a streaming pass-through. `OPTIONS` preflights are
  answered by the Worker (204, the shared browser CORS policy) and are not
  forwarded: Cloudflare Access would answer them 403 before checking the
  service token. Retired `/mcp-directory*`
  requests terminate at the edge with an ordinary 404. Apex `/` + every other
  path still hits GitHub Pages.

**Why the app route is `tinyassets.io/app*` and not an exact path.** A
Cloudflare route is matched against the **entire URL, including the query
string**. An exact `tinyassets.io/app` route therefore matches only a bare
`/app` — and the two URLs that carry the whole sign-in and billing flow,
`/app?code=…&state=…` (the AuthKit return, built by the page from its own
location) and `/app?subscribed=1` (the Stripe return), would match no route at
all and be answered by the apex website origin. The app shell would load and
sign-in would be dark. Adding `tinyassets.io/app/*` does not help: it covers
descendants, not the shell with a query. Found by a gpt-6-astra review round on
2026-09-29, after an exact+subtree pair had already been written and tested
green — no unit test can see a route pattern's semantics.

**What the wildcard costs, and who pays it.** A Cloudflare `*` matches zero or
more of *any* character, not a path segment, so `/app*` also captures apex
assets whose path merely starts with `app` — `/apple-touch-icon.png` is one the
site really serves. `worker.js` classifies those with `belongsToWebsite` and
forwards them to the website origin **unrewritten** (`passToWebsiteOrigin`),
rather than answering 404 on the site's behalf. The pass-through is scoped to
`app`-prefixed non-app paths only, so it does not reintroduce the fallthrough
ambiguity that made the 2026-04-19 P0 hard to diagnose: `/mcp-directory*` still
terminates here as an ordinary 404. A marker header makes the forward a
terminator rather than something to trust — Cloudflare sends a Worker's
same-route subrequest to the origin instead of re-invoking the script, and if
that ever changed the marker turns a loop into a 404.

**`/mcp/app` is retired, not redirected** (founder directive 2026-09-30, no
back-compat). It still matches the `/mcp*` binding, so it is proxied to the
daemon, which mounts nothing there. It gets **no carve-out**: it is treated like
any other absent path in the connector namespace, so an anonymous request sees
the connector's `401` (exactly what `/mcp/anything` returns) and an
authenticated one a `404`. Either way it is not the app, and no `Location` is
sent. Special-casing it into a "clean 404" would be a special case *for* the
retired path — the back-compat this move removes.

---

## Files in this directory

| File | Purpose |
|---|---|
| `worker.js` | The Worker script itself — pure Fetch API proxy. |
| `wrangler.toml` | Cloudflare Worker deploy config (name, route, compat date). |
| `worker.test.js` | Unit tests via `node --test` cover the canonical allowlist, retired-route 404s, header preservation, streaming pass-through, and 5xx-to-502 translation. |
| `README.md` | This file. |

---

## Deploy path A — Wrangler CLI (recommended for ops)

Requires [Wrangler](https://developers.cloudflare.com/workers/wrangler/install-and-update/)
on PATH (`npm install -g wrangler` or `npx wrangler@latest`).

```bash
cd deploy/cloudflare-worker

# One-time: authenticate. Opens a browser to the Cloudflare login flow.
wrangler login

# Validate the config + worker before pushing.
wrangler deploy --dry-run --outdir /tmp/wrangler-out

# Deploy. Publishes worker + registers every route in wrangler.toml
# (tinyassets.io/mcp*, tinyassets.io/app*).
wrangler deploy
```

Verify:

```bash
# Canary should exit 0 within ~60s of deploy (DNS + edge propagation).
python ../../scripts/mcp_public_canary.py \
    --url https://tinyassets.io/mcp --verbose
```

Rollback:

```bash
# Either deploy a previous version, or delete the Worker + route.
wrangler rollback                               # previous version
wrangler delete                                 # full removal
```

Tail live Worker logs:

```bash
wrangler tail
```

---

## Deploy path B — Dashboard (for operators without Wrangler)

If you'd rather click than CLI:

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com) → `tinyassets.io` zone.
2. **Workers & Pages** → **Create Application** → **Create Worker**.
3. Name: `tinyassets-mcp-proxy`. Click **Deploy** (default "Hello World"
   placeholder is fine at this step).
4. Open the new Worker → **Edit code** → replace the entire editor
   contents with the body of `worker.js` from this directory → **Save and deploy**.
5. On the Worker overview page: **Triggers** → **Routes** → **Add route**. Add
   both, each with Zone `tinyassets.io`:
   - `tinyassets.io/mcp*`
   - `tinyassets.io/app*`

   Do **not** narrow the second to an exact `tinyassets.io/app` (with or without
   a `tinyassets.io/app/*` companion) — see "Why the app route is
   `tinyassets.io/app*`" above; it silently breaks every sign-in and billing
   return while the app shell still loads.
6. Verify with the canary (see path A verify step), then the app surface:
   `python ../../scripts/probe_app_surface.py --verbose` — it checks `/app`, the
   query-bearing returns, the retired path, `/apple-touch-icon.png`, and the
   anonymous `401` on `/app/me`.

**If the Worker fails to save** with a syntax error, double-check the
editor got the full `worker.js` body including the `export default {}`
at the bottom. The dashboard editor sometimes truncates on paste of
large files; `wc -l worker.js` locally should match the editor's line
count.

---

## Canonical URL reference

This revision's routing contract (production proof pending):

| URL | Purpose |
|---|---|
| `https://tinyassets.io/mcp` | **Canonical — installed TinyAssets chatbot connectors.** Worker routes to tunnel. |
| `https://tinyassets.io/app` | **Canonical — the web app** (and the Play/desktop shells' load target). Worker routes to tunnel. |
| `https://tinyassets.io/app?code=…` | The AuthKit sign-in return, and `?subscribed=1` the Stripe return. Worker routes to tunnel — this is why the route is `/app*`. |
| `https://tinyassets.io/app/*` | The app's own API (token exchange, `me`, billing, uploads, voice, connections). Worker routes to tunnel. |
| `https://tinyassets.io/mcp/app*` | **Retired 2026-09-30 — proxied to the daemon, which mounts nothing there: `401` anonymously, `404` authenticated. No redirect, no alias, no carve-out.** |
| `https://tinyassets.io/mcp-directory*` | **Retired — ordinary edge 404; never redirected or proxied.** |
| `https://tinyassets.io/apple-touch-icon.png` | GitHub Pages — the Worker sees it (the `/app*` route) and forwards it to the website origin unrewritten. |
| `https://mcp.tinyassets.io/mcp` | Direct-tunnel origin — Access-gated, not user-facing. Use only for internal Access/service-token debugging. |
| `https://tinyassets.io/` | GitHub Pages landing (unchanged). |

---

## How the Worker handles MCP

MCP streamable-http has two failure modes a naive proxy breaks:

1. **Server-sent events.** MCP returns responses as SSE frames
   (`event: message\ndata: {...}\n\n`). A proxy that calls `.text()` or
   `.json()` on the response buffers the whole body, breaking streaming
   for any response that takes time to generate.

   The Worker treats the response body as a `ReadableStream` and
   passes it through to the client without touching the bytes. 4 tests
   cover this — including one that uses an explicit `ReadableStream`
   to assert the stream identity is preserved.

2. **Session headers.** Claude.ai's MCP client uses `Mcp-Session-Id`
   to persist session state across requests. The Worker forwards all
   non-hop-by-hop headers verbatim, including `Mcp-Session-Id`,
   `Authorization`, `Accept: text/event-stream`, etc. Covered by
   dedicated tests.

Hop-by-hop headers (RFC 7230 §6.1 — `Connection`, `Transfer-Encoding`,
`Upgrade`, etc.) are explicitly stripped to avoid forwarding connection
semantics that don't apply to the upstream hop.

---

## Failure modes + 502 translation

The Worker returns **502 Bad Gateway** with a JSON body in two cases:

- Tunnel origin unreachable (network error from `fetch()`).
- Tunnel origin returns 5xx.

This is deliberate. A 502 is unambiguous: the Worker saw an upstream
problem. Letting the fallthrough go to the landing origin's 404 is what caused the
original P0's diagnostic confusion ("is the tunnel down? or is the
Worker broken? or is the route wrong?"). Explicit 502s end that
ambiguity.

4xx responses pass through untouched — those are client errors the
upstream wants to report verbatim, not proxy errors.

---

## Running tests locally

Node 18+ required (uses the built-in `node:test` runner + Fetch API).

```bash
cd deploy/cloudflare-worker
node --test worker.test.js
```

Expect `30 passed, 0 failed`. CI wires this into the existing
`.github/workflows/docker-build.yml` or its own workflow once the
Worker lands; for now the test runs locally.

---

## Known limitations + follow-ups

- **One-way proxy, no caching.** Cloudflare's edge cache is bypassed
  by default for Worker-proxied requests. MCP responses are per-session
  and shouldn't be cached anyway, so this is correct — but if we ever
  want to cache static MCP-served assets, we'd add `cf: {cacheTtl}` to
  the upstream fetch.

- **No rate limiting in the Worker.** Upstream daemon sees the full
  request volume. If we ever hit abuse, Cloudflare WAF / Rate Limiting
  adds on top of the Worker without code change.

- **Single origin.** Multi-region failover (primary Hetzner + fallback
  Fly) is a Row D concern, not Row Worker. When that lands, this
  Worker's `TUNNEL_ORIGIN` grows into an ordered list + retry logic.

- **CI deploy is automated.** Any push to `main` that touches
  `deploy/cloudflare-worker/**` triggers `.github/workflows/deploy-worker.yml`,
  which runs Wrangler + a post-deploy canary. PRs get a dry-run only.
  Required repo secret: `CLOUDFLARE_API_TOKEN` with scopes
  `Account:Workers Scripts:Edit`, `Zone:Workers Routes:Edit`,
  `Zone:Zone:Read`. Also set `CLOUDFLARE_ACCOUNT_ID` as a repo secret.
