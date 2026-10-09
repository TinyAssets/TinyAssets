// worker.js — path-based router for tinyassets.io.
//
// Problem:
//   - tinyassets.io apex serves GoDaddy Website Builder (landing page).
//   - mcp.tinyassets.io is the Cloudflare Tunnel → workflow daemon MCP
//     (internal tunnel origin, Access-gated — not a public user-facing URL).
//   - Installed Claude.ai connectors point at https://tinyassets.io/mcp.
//   - Without this Worker, tinyassets.io/mcp falls through to GoDaddy's
//     404 handler → "Session terminated" in Claude.ai (the 2026-04-19 P0).
//
// Fix:
//   This Worker runs on routes `tinyassets.io/mcp*` and `tinyassets.io/app*`.
//   Requests to `/mcp` (the connector endpoint) and `/app` (the web app) are
//   forwarded to the internal tunnel origin at `mcp.tinyassets.io`,
//   authenticated via Cloudflare Access service-token headers. The broad
//   route also catches retired `/mcp-directory*` requests so this Worker can
//   terminate them consistently with an ordinary 404 instead of forwarding
//   them to either origin.
//
// Security model (host directive 2026-04-20):
//   - `tinyassets.io/mcp` is the only public connector URL, and
//     `tinyassets.io/app` the only public app URL.
//   - `mcp.tinyassets.io` exists in DNS as the tunnel origin but is
//     Access-gated: direct requests without CF-Access service-token headers
//     return 401/403. Only this Worker can reach it, via the secret headers
//     injected below from Cloudflare Worker env secrets (CF_ACCESS_CLIENT_ID
//     + CF_ACCESS_CLIENT_SECRET — set in Cloudflare dashboard, never in git).
//   - Do not document or share mcp.tinyassets.io in user-facing contexts.
//
// Design constraints:
//   - MCP streamable-http emits SSE (text/event-stream) for long
//     responses. Must not buffer the body; pass the ReadableStream
//     straight through.
//   - Accept, Content-Type, Authorization, Mcp-Session-Id, and every
//     other request header preserved. Upstream decides which to act on.
//   - POST, GET, OPTIONS all supported (OPTIONS for CORS preflight).
//   - 5xx from the tunnel becomes 502 Bad Gateway with a clear body —
//     NOT GoDaddy fallthrough. Ambiguity on failure mode is what caused
//     the original P0; explicit status here.
//   - Pure proxy: no response-body rewriting.
//
// Canonical connector URL: https://tinyassets.io/mcp  (apex + path, user-facing).
// Canonical app URL:       https://tinyassets.io/app  (apex + path, user-facing).
// Tunnel origin:           https://mcp.tinyassets.io  (Access-gated, internal only).

const TUNNEL_ORIGIN = 'https://mcp.tinyassets.io';

// Hop-by-hop headers that a proxy MUST NOT forward. Per RFC 7230 §6.1.
// Cloudflare strips most of these automatically, but being explicit
// protects against future runtime changes.
const HOP_BY_HOP_REQUEST_HEADERS = new Set([
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
    'host', // we rewrite Host to the tunnel origin
]);

const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
]);

const FORBIDDEN_RESPONSE_HEADERS = new Set([
    ...HOP_BY_HOP_RESPONSE_HEADERS,
    'set-cookie',
]);

// Access's tunnel cookie must never become a public-origin credential. Only
// app-owned, host-only cookies cross this boundary, on app routes only.
function appCookieAllowed(cookie, pathname) {
    if (!(pathname === '/app' || pathname.startsWith('/app/'))) return false;
    const [pair, ...attributes] = cookie.split(';').map(part => part.trim());
    const name = pair.split('=', 1)[0];
    const paths = new Map([
        ['__Host-ta-owner', '/'], ['__Host-ta-owner-login', '/'],
        ['__Host-ta-model-return', '/'], ['__Host-ta-approval-return', '/'],
        ['ta_rt', '/app/token'],
    ]);
    if (!paths.has(name)) return false;
    const attrs = attributes.map(value => value.toLowerCase());
    return attrs.includes('secure') && attrs.includes('httponly')
        && attrs.filter(value => value.startsWith('path=')).join() === 'path=' + paths.get(name)
        && !attrs.some(value => value.startsWith('domain='))
        && attrs.some(value => value === 'samesite=lax' || value === 'samesite=strict');
}

/**
 * Proxy one request to the tunnel origin.
 *
 * Preserves method, body stream, and allowed non-hop-by-hop headers.
 * Only explicitly allowed app cookies cross the public boundary.
 * Rewrites Host to `mcp.tinyassets.io` (Cloudflare's edge routes the
 * subrequest to the tunnel based on hostname, so this is load-bearing).
 *
 * Injects CF Access service-token headers so the Access-gated tunnel
 * origin accepts the subrequest. `env` carries the secret values from
 * the Cloudflare Worker environment (set via dashboard, never in git).
 */
async function proxyToTunnel(request, env) {
    const incoming = new URL(request.url);
    const upstream = new URL(TUNNEL_ORIGIN);
    upstream.pathname = incoming.pathname;
    upstream.search = incoming.search;

    const forwardedHeaders = new Headers();
    for (const [name, value] of request.headers) {
        if (!HOP_BY_HOP_REQUEST_HEADERS.has(name.toLowerCase())) {
            forwardedHeaders.set(name, value);
        }
    }
    forwardedHeaders.set('Host', upstream.host);

    // CF Access service-token headers — authenticate this Worker as the
    // authorised caller. mcp.tinyassets.io rejects requests without them.
    // Values come from Worker env secrets (Cloudflare dashboard only).
    if (env && env.CF_ACCESS_CLIENT_ID) {
        forwardedHeaders.set('CF-Access-Client-Id', env.CF_ACCESS_CLIENT_ID);
    }
    if (env && env.CF_ACCESS_CLIENT_SECRET) {
        forwardedHeaders.set('CF-Access-Client-Secret', env.CF_ACCESS_CLIENT_SECRET);
    }

    // Preserve X-Forwarded-* so the upstream can log + rate-limit by
    // real client IP rather than Cloudflare's edge.
    const clientIp = request.headers.get('CF-Connecting-IP');
    if (clientIp && !forwardedHeaders.has('X-Forwarded-For')) {
        forwardedHeaders.set('X-Forwarded-For', clientIp);
    }
    if (!forwardedHeaders.has('X-Forwarded-Proto')) {
        forwardedHeaders.set('X-Forwarded-Proto', incoming.protocol.replace(':', ''));
    }
    if (!forwardedHeaders.has('X-Forwarded-Host')) {
        forwardedHeaders.set('X-Forwarded-Host', incoming.host);
    }

    // Body: pass the ReadableStream through. For bodyless methods
    // (GET, HEAD, OPTIONS) this is null automatically.
    // ``duplex: 'half'`` is required by the fetch spec when sending a
    // ReadableStream body. Cloudflare Workers tolerate its absence but
    // Node 18+ undici (used by tests) requires it.
    const init = {
        method: request.method,
        headers: forwardedHeaders,
        body: request.body,
        // redirect: 'manual' so redirect responses come back to the
        // client untouched. An MCP server shouldn't 30x but if it
        // does, the client needs to see it verbatim.
        redirect: 'manual',
    };
    if (request.body !== null && request.body !== undefined) {
        init.duplex = 'half';
    }
    const upstreamRequest = new Request(upstream.toString(), init);

    let upstreamResponse;
    try {
        upstreamResponse = await fetch(upstreamRequest);
    } catch (err) {
        return new Response(
            JSON.stringify({
                error: 'bad_gateway',
                detail: 'tunnel origin unreachable',
                upstream: upstream.host,
                message: String(err && err.message ? err.message : err),
            }),
            {
                status: 502,
                headers: {
                    'Content-Type': 'application/json',
                    'Cache-Control': 'no-store',
                },
            },
        );
    }

    // 5xx from the tunnel. The point of translating it is to let a caller
    // distinguish "tunnel origin sick" from "Worker code broken" from "apex
    // fallthrough to GoDaddy 404" — a real distinction that cost us the
    // 2026-04-19 P0 outage, and worth keeping.
    //
    // But translating EVERY 5xx overshot the goal: it replaced the body too, so
    // an origin that deliberately answered `{"error": "billing_unavailable",
    // "detail": "..."}` reached the browser as "tunnel origin returned 5xx". The
    // user was handed an infrastructure diagnosis for an application decision, and
    // whoever read the logs was told the tunnel was sick when it was not. The
    // reason for the failure was destroyed by the layer reporting it.
    //
    // The discriminator: a sick tunnel, a dead origin, or an apex fallthrough emits
    // an HTML error page. Our application emits JSON. So a JSON 5xx is the origin
    // ANSWERING and is passed through untouched, with the original status intact
    // and `X-TA-Origin-Status` marking it as origin-authored so the distinction the
    // translation exists for survives at the edge. A non-JSON 5xx is still
    // translated, because that is the case that actually means "not our app".
    if (upstreamResponse.status >= 500 && upstreamResponse.status < 600) {
        const contentType = (
            upstreamResponse.headers.get('content-type') || ''
        ).toLowerCase();
        if (!contentType.includes('application/json')) {
            return new Response(
                JSON.stringify({
                    error: 'bad_gateway',
                    detail: 'tunnel origin returned 5xx',
                    upstream_status: upstreamResponse.status,
                }),
                {
                    status: 502,
                    headers: {
                        'Content-Type': 'application/json',
                        'Cache-Control': 'no-store',
                    },
                },
            );
        }
    }

    // Streaming pass-through. Do NOT call .text() / .json() / .arrayBuffer()
    // on the response — those buffer the whole body and break SSE.
    const responseHeaders = new Headers();
    for (const [name, value] of upstreamResponse.headers) {
        if (!FORBIDDEN_RESPONSE_HEADERS.has(name.toLowerCase())) {
            responseHeaders.set(name, value);
        }
    }
    // Workers also exposes getAll('Set-Cookie'); never comma-split Expires.
    const cookies = typeof upstreamResponse.headers.getSetCookie === 'function'
        ? upstreamResponse.headers.getSetCookie() : upstreamResponse.headers.getAll('Set-Cookie');
    for (const cookie of cookies) {
        if (appCookieAllowed(cookie, incoming.pathname)) responseHeaders.append('Set-Cookie', cookie);
    }
    // A 5xx reaching here is one the origin authored. Mark it, so "the app said
    // 500" stays distinguishable from "the tunnel is sick" without having to
    // overwrite what the app said.
    if (upstreamResponse.status >= 500 && upstreamResponse.status < 600) {
        responseHeaders.set('X-TA-Origin-Status', String(upstreamResponse.status));
    }

    return new Response(upstreamResponse.body, {
        status: upstreamResponse.status,
        statusText: upstreamResponse.statusText,
        headers: responseHeaders,
    });
}

/**
 * CORS preflight — MCP clients in browsers issue OPTIONS before POST.
 * Pass through to the origin (the daemon + FastMCP handle CORS headers
 * themselves); this is purely the method-allow check.
 */
function shouldProxy(pathname) {
    // Two path families belong to the tunnel:
    //   /mcp   — the canonical MCP connector endpoint (and its sub-paths).
    //   /app   — the web app (moved off /mcp/app on 2026-09-30).
    // The broader Cloudflare `tinyassets.io/mcp*` binding intentionally reaches
    // this Worker so stale directory callers receive the same
    // method-independent ordinary 404.
    //
    // `/mcp/app*` still matches the `/mcp/` family here, so it is still
    // PROXIED — and the daemon, which no longer mounts those routes, answers
    // exactly what it answers for any other absent `/mcp/*` path. That is the
    // intended retirement: the edge does not redirect or alias the old path,
    // and the refusal comes from the app origin rather than the website
    // origin, so it stays diagnosable.
    //
    // Note this decides on `URL.pathname`, which NEVER carries the query — so
    // `/app?code=…` is `/app` here. That matters because the Cloudflare ROUTE
    // matches the whole URL *including* the query, which is why the binding has
    // to be `tinyassets.io/app*` and cannot be an exact `tinyassets.io/app`.
    if (pathname === '/mcp' || pathname.startsWith('/mcp/')) return true;
    if (pathname === '/app' || pathname.startsWith('/app/')) return true;
    return false;
}

/**
 * Does this path belong to the WEBSITE, having only been dragged in here by the
 * query-capable `/app*` binding?
 *
 * `tinyassets.io/app*` is required so the OAuth return `/app?code=…&state=…`
 * and the Stripe return `/app?subscribed=1` reach the daemon at all — a
 * Cloudflare route is matched against the entire URL including the query, so an
 * exact `tinyassets.io/app` route matches ONLY a bare `/app`, and sign-in would
 * land on the website origin.
 *
 * But a Cloudflare `*` matches any character, not a path segment, so that
 * binding also captures apex assets whose path merely begins with "app" —
 * `/apple-touch-icon.png` is one the site really serves. Those must keep
 * reaching the website origin, so the Worker hands them back rather than
 * answering 404 on the site's behalf.
 *
 * Deliberately narrow: only `/app`-prefixed non-app paths pass through.
 * `/mcp-directory*` keeps terminating here as an ordinary 404, which is a spec
 * requirement — the fallthrough ambiguity is what made the 2026-04-19 P0 hard
 * to diagnose, and this does not reintroduce it for the MCP family.
 */
function belongsToWebsite(pathname) {
    return pathname.startsWith('/app') && !shouldProxy(pathname);
}

//: Marks a website pass-through subrequest as ours. Cloudflare sends a Worker's
//: same-route subrequest to the origin rather than re-invoking the script, so
//: this should never be seen twice — it is a terminator, not a mechanism, so a
//: platform change cannot turn the pass-through into an infinite loop.
const PASSTHROUGH_MARKER = 'X-TA-Edge-Passthrough';

async function passToWebsiteOrigin(request) {
    if (request.headers.get(PASSTHROUGH_MARKER)) {
        // Only reachable if the platform ever re-invoked us for our own
        // subrequest. Fail closed and visibly rather than loop.
        return new Response('Not Found', { status: 404 });
    }
    const marked = new Request(request);
    marked.headers.set(PASSTHROUGH_MARKER, '1');
    return fetch(marked);
}

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (belongsToWebsite(url.pathname)) {
            return passToWebsiteOrigin(request);
        }

        if (!shouldProxy(url.pathname)) {
            // Retired `/mcp-directory*`, plus a defensive catch — if the route
            // somehow matched some other path, return 404 rather than leaking a
            // proxy to the tunnel for paths the tunnel isn't meant to serve.
            return new Response('Not Found', { status: 404 });
        }

        // OPTIONS + POST + GET + everything-else: all proxied identically.
        return proxyToTunnel(request, env);
    },
};

// Export internals for unit tests.
export {
    belongsToWebsite,
    PASSTHROUGH_MARKER,
    proxyToTunnel,
    shouldProxy,
    TUNNEL_ORIGIN,
    HOP_BY_HOP_REQUEST_HEADERS,
    HOP_BY_HOP_RESPONSE_HEADERS,
};
