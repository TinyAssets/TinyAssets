// Unit tests for worker.js — run with `node --test worker.test.js`.
//
// Tests cover:
//   - shouldProxy: path matching (/mcp* plus the apex /app subtree).
//   - proxyToTunnel: header preservation, hop-by-hop stripping, streaming
//     pass-through, 5xx → 502 translation, network-error → 502, X-Forwarded-*
//     addition, Host rewrite, CF Access service-token injection.
//
// Uses a stub for globalThis.fetch so we don't actually hit the tunnel.
//
// Why Node's built-in runner instead of wrangler-local: wrangler adds a
// heavy dependency for a pure-JS pure-function Worker. The Worker uses
// only the Fetch API standard primitives available in Node 18+. This
// test suite exercises the same surface at unit scope.

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import worker, {
    belongsToWebsite,
    PASSTHROUGH_MARKER,
    proxyToTunnel,
    shouldProxy,
    TUNNEL_ORIGIN,
    HOP_BY_HOP_REQUEST_HEADERS,
    HOP_BY_HOP_RESPONSE_HEADERS,
} from './worker.js';

// ------- fetch stub harness ------------------------------------------------

let originalFetch;
let lastUpstreamRequest;
let nextUpstreamResponse;
let nextUpstreamError;

beforeEach(() => {
    originalFetch = globalThis.fetch;
    lastUpstreamRequest = null;
    nextUpstreamResponse = null;
    nextUpstreamError = null;

    globalThis.fetch = async (req) => {
        lastUpstreamRequest = req;
        if (nextUpstreamError) throw nextUpstreamError;
        if (!nextUpstreamResponse) {
            return new Response('ok', {
                status: 200,
                headers: { 'Content-Type': 'text/plain' },
            });
        }
        return nextUpstreamResponse;
    };
});

afterEach(() => {
    globalThis.fetch = originalFetch;
});

// ------- shouldProxy -------------------------------------------------------

describe('shouldProxy', () => {
    it('accepts /mcp', () => {
        assert.equal(shouldProxy('/mcp'), true);
    });

    it('accepts /mcp/foo', () => {
        assert.equal(shouldProxy('/mcp/foo'), true);
    });

    it('rejects retired /mcp-directory', () => {
        assert.equal(shouldProxy('/mcp-directory'), false);
    });

    it('rejects retired /mcp-directory descendants', () => {
        assert.equal(shouldProxy('/mcp-directory/foo'), false);
    });

    it('rejects /', () => {
        assert.equal(shouldProxy('/'), false);
    });

    it('rejects /mcpx (no slash, not /mcp)', () => {
        assert.equal(shouldProxy('/mcpx'), false);
    });

    it('rejects /mcp-directory-old', () => {
        assert.equal(shouldProxy('/mcp-directory-old'), false);
    });

    it('rejects paths outside /mcp', () => {
        assert.equal(shouldProxy('/catalog'), false);
        assert.equal(shouldProxy('/assets/logo.png'), false);
    });

    // The app moved from /mcp/app to the apex /app on 2026-09-30. The Worker is
    // the only thing standing between that path and the website origin, so the
    // binding is load-bearing: without these the app URL 404s from GitHub Pages.
    it('accepts /app (the app shell)', () => {
        assert.equal(shouldProxy('/app'), true);
    });

    it('accepts /app/ and the whole app subtree', () => {
        assert.equal(shouldProxy('/app/'), true);
        assert.equal(shouldProxy('/app/me'), true);
        assert.equal(shouldProxy('/app/token'), true);
        assert.equal(shouldProxy('/app/billing/webhook'), true);
        assert.equal(shouldProxy('/app/model-callback/connect'), true);
    });

    // The `tinyassets.io/app*` route has to be the suffix wildcard so
    // query-bearing callback URLs match it at all, which means it also delivers
    // apex assets that merely start with "app". The site really serves
    // /apple-touch-icon.png, so the guard is not hypothetical.
    it('rejects apex assets that merely start with "app"', () => {
        assert.equal(shouldProxy('/apple-touch-icon.png'), false);
        assert.equal(shouldProxy('/app-ads.txt'), false);
        assert.equal(shouldProxy('/apps'), false);
        assert.equal(shouldProxy('/appx/deep'), false);
    });

    // URL.pathname never carries the query, so the callback URLs are just
    // '/app' by the time the Worker decides. The QUERY is what the Cloudflare
    // route has to cope with, and that is wrangler.toml's job, not this
    // function's — pinned here so the two cannot drift apart silently.
    it('decides on the path alone, since URL.pathname drops the query', () => {
        assert.equal(new URL('https://tinyassets.io/app?code=x&state=y').pathname, '/app');
        assert.equal(shouldProxy(new URL('https://tinyassets.io/app?code=x').pathname), true);
        assert.equal(shouldProxy(new URL('https://tinyassets.io/app?subscribed=1').pathname), true);
    });
});

describe('belongsToWebsite', () => {
    it('claims only the app-prefixed siblings the wide route drags in', () => {
        assert.equal(belongsToWebsite('/apple-touch-icon.png'), true);
        assert.equal(belongsToWebsite('/app-ads.txt'), true);
        assert.equal(belongsToWebsite('/apps'), true);
        assert.equal(belongsToWebsite('/appx/deep'), true);
    });

    it('never claims an app path', () => {
        assert.equal(belongsToWebsite('/app'), false);
        assert.equal(belongsToWebsite('/app/'), false);
        assert.equal(belongsToWebsite('/app/me'), false);
    });

    // The retired /mcp-directory* family must keep terminating at the edge as an
    // ordinary 404 — a spec requirement. Pass-through is scoped so it cannot
    // reintroduce the fallthrough ambiguity that made the 2026-04-19 P0 hard to
    // diagnose.
    it('never claims a /mcp path, retired or canonical', () => {
        assert.equal(belongsToWebsite('/mcp'), false);
        assert.equal(belongsToWebsite('/mcp/app'), false);
        assert.equal(belongsToWebsite('/mcp-directory'), false);
        assert.equal(belongsToWebsite('/'), false);
        assert.equal(belongsToWebsite('/catalog'), false);
    });
});

// ------- handler routing ---------------------------------------------------

describe('worker handler — retired /mcp-directory routing', () => {
    const methods = ['GET', 'HEAD', 'POST', 'OPTIONS'];
    const retiredUrls = [
        ['exact path', 'https://tinyassets.io/mcp-directory'],
        ['trailing slash', 'https://tinyassets.io/mcp-directory/'],
        ['arbitrary descendant', 'https://tinyassets.io/mcp-directory/arbitrary/deep/path'],
        [
            'former versioned catalog',
            'https://tinyassets.io/mcp-directory/catalog/2026-06-24-underscore-handles',
        ],
        ['query string', 'https://tinyassets.io/mcp-directory?catalog=legacy'],
    ];

    for (const method of methods) {
        for (const [urlKind, url] of retiredUrls) {
            it(`returns an ordinary 404 for ${method} ${urlKind}`, async () => {
                const response = await worker.fetch(new Request(url, { method }), {});

                assert.equal(lastUpstreamRequest, null, 'must not call the tunnel origin');
                assert.equal(response.status, 404);
                assert.equal(response.headers.get('Location'), null);
                assert.equal(response.redirected, false);
                if (method !== 'HEAD') {
                    assert.equal(await response.text(), 'Not Found');
                }
            });
        }
    }
});

describe('worker handler — canonical /mcp routing', () => {
    for (const method of ['GET', 'HEAD', 'POST', 'OPTIONS']) {
        it(`continues to proxy ${method} /mcp`, async () => {
            const response = await worker.fetch(
                new Request('https://tinyassets.io/mcp?canonical=true', { method }),
                {},
            );

            assert.ok(lastUpstreamRequest);
            assert.equal(lastUpstreamRequest.method, method);
            assert.equal(lastUpstreamRequest.url, `${TUNNEL_ORIGIN}/mcp?canonical=true`);
            assert.equal(response.status, 200);
        });
    }
});

describe('worker handler — canonical /app routing', () => {
    for (const method of ['GET', 'HEAD', 'POST', 'OPTIONS']) {
        it(`proxies ${method} /app to the tunnel`, async () => {
            const response = await worker.fetch(
                new Request('https://tinyassets.io/app?subscribed=1', { method }),
                {},
            );

            assert.ok(lastUpstreamRequest, 'the app URL must reach the daemon');
            assert.equal(lastUpstreamRequest.method, method);
            assert.equal(lastUpstreamRequest.url, `${TUNNEL_ORIGIN}/app?subscribed=1`);
            assert.equal(response.status, 200);
        });
    }

    // The finding that made the route a suffix wildcard: these two URLs carry
    // the entire sign-in and billing flow, and a Cloudflare route matches the
    // whole URL including the query, so an exact `/app` binding sent them to
    // the website origin.
    for (const [what, query] of [
        ['the AuthKit return', '?code=synthetic&state=' + 'f'.repeat(43)],
        ['the Stripe subscribed return', '?subscribed=1'],
        ['the Stripe cancelled return', '?subscribed=0'],
        ['an error return', '?error=access_denied'],
    ]) {
        it(`proxies ${what} on /app with its query intact`, async () => {
            const response = await worker.fetch(
                new Request('https://tinyassets.io/app' + query, { method: 'GET' }),
                {},
            );

            assert.ok(lastUpstreamRequest, `${what} must reach the daemon`);
            assert.equal(lastUpstreamRequest.url, `${TUNNEL_ORIGIN}/app${query}`);
            assert.equal(response.status, 200);
        });
    }

    it('proxies an app API subpath with its query intact', async () => {
        const response = await worker.fetch(
            new Request('https://tinyassets.io/app/models/preferences?universe_id=u1', {
                method: 'GET',
            }),
            {},
        );

        assert.ok(lastUpstreamRequest);
        assert.equal(
            lastUpstreamRequest.url,
            `${TUNNEL_ORIGIN}/app/models/preferences?universe_id=u1`,
        );
        assert.equal(response.status, 200);
    });

    // No back-compat (founder directive 2026-09-30): the edge must NOT redirect
    // or alias the retired path. It stays inside the /mcp/ family, so it is
    // proxied to the daemon — which no longer mounts it and answers 404. That
    // keeps the retirement diagnosable (our origin said no, not the website's).
    it('does not redirect or alias the retired /mcp/app path', async () => {
        const response = await worker.fetch(
            new Request('https://tinyassets.io/mcp/app', { method: 'GET' }),
            {},
        );

        assert.ok(lastUpstreamRequest, 'still proxied, never edge-redirected');
        assert.equal(lastUpstreamRequest.url, `${TUNNEL_ORIGIN}/mcp/app`);
        assert.equal(response.headers.get('Location'), null);
        assert.ok(response.status < 300 || response.status >= 400);
    });
});

describe('worker handler — website pass-through for app-prefixed siblings', () => {
    for (const path of ['/apple-touch-icon.png', '/app-ads.txt', '/apps']) {
        it(`hands ${path} to the website origin unchanged`, async () => {
            const response = await worker.fetch(
                new Request('https://tinyassets.io' + path, { method: 'GET' }),
                {},
            );

            assert.ok(lastUpstreamRequest, 'must be forwarded, not answered 404');
            // Same URL — NOT rewritten to the tunnel origin. That is the whole
            // point: the site keeps serving its own asset.
            assert.equal(lastUpstreamRequest.url, 'https://tinyassets.io' + path);
            assert.equal(lastUpstreamRequest.headers.get(PASSTHROUGH_MARKER), '1');
            assert.equal(response.status, 200);
        });
    }

    it('never reaches the tunnel origin for a website path', async () => {
        await worker.fetch(
            new Request('https://tinyassets.io/apple-touch-icon.png'),
            {},
        );
        assert.ok(
            !lastUpstreamRequest.url.startsWith(TUNNEL_ORIGIN),
            'a website asset must not be sent through the Access-gated tunnel',
        );
    });

    it('terminates rather than looping if its own subrequest comes back', async () => {
        // Cloudflare sends a Worker's same-route subrequest to the origin
        // instead of re-invoking the script. The marker makes that a
        // terminator rather than something we have to trust.
        const looped = new Request('https://tinyassets.io/apple-touch-icon.png', {
            headers: { [PASSTHROUGH_MARKER]: '1' },
        });
        const response = await worker.fetch(looped, {});
        assert.equal(lastUpstreamRequest, null, 'must not forward a second time');
        assert.equal(response.status, 404);
    });

    it('still terminates retired /mcp-directory* at the edge, not via the site', async () => {
        const response = await worker.fetch(
            new Request('https://tinyassets.io/mcp-directory/anything'),
            {},
        );
        assert.equal(lastUpstreamRequest, null, 'no forward of any kind');
        assert.equal(response.status, 404);
        assert.equal(await response.text(), 'Not Found');
    });
});

// ------- proxyToTunnel: basic routing --------------------------------------

describe('proxyToTunnel — URL rewrite', () => {
    it('rewrites host to mcp.tinyassets.io keeping path + query', async () => {
        const req = new Request('https://tinyassets.io/mcp?k=v', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{"jsonrpc":"2.0"}',
        });
        await proxyToTunnel(req);
        assert.ok(lastUpstreamRequest);
        const upstream = new URL(lastUpstreamRequest.url);
        assert.equal(upstream.origin, TUNNEL_ORIGIN);
        assert.equal(upstream.pathname, '/mcp');
        assert.equal(upstream.search, '?k=v');
    });

    it('rewrites Host header to the tunnel host', async () => {
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('Host'), 'mcp.tinyassets.io');
    });
});

// ------- proxyToTunnel: header preservation --------------------------------

describe('proxyToTunnel — headers preserved', () => {
    it('forwards Accept including text/event-stream (MCP SSE)', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            headers: { 'Accept': 'application/json, text/event-stream' },
        });
        await proxyToTunnel(req);
        assert.equal(
            lastUpstreamRequest.headers.get('Accept'),
            'application/json, text/event-stream',
        );
    });

    it('forwards Content-Type', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('Content-Type'), 'application/json');
    });

    it('forwards Authorization + Mcp-Session-Id', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            headers: {
                'Authorization': 'Bearer abc123',
                'Mcp-Session-Id': 'session-42',
            },
        });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('Authorization'), 'Bearer abc123');
        assert.equal(lastUpstreamRequest.headers.get('Mcp-Session-Id'), 'session-42');
    });

    it('strips hop-by-hop headers from the request', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            headers: {
                'Connection': 'close',
                'Content-Type': 'application/json',
            },
        });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('Connection'), null);
        assert.equal(lastUpstreamRequest.headers.get('Content-Type'), 'application/json');
    });

    it('adds X-Forwarded-For from CF-Connecting-IP', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'GET',
            headers: { 'CF-Connecting-IP': '203.0.113.5' },
        });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('X-Forwarded-For'), '203.0.113.5');
    });

    it('adds X-Forwarded-Proto + X-Forwarded-Host', async () => {
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('X-Forwarded-Proto'), 'https');
        assert.equal(lastUpstreamRequest.headers.get('X-Forwarded-Host'), 'tinyassets.io');
    });

    it('preserves caller-supplied X-Forwarded-For', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'GET',
            headers: {
                'CF-Connecting-IP': '203.0.113.5',
                'X-Forwarded-For': '198.51.100.1',
            },
        });
        await proxyToTunnel(req);
        assert.equal(lastUpstreamRequest.headers.get('X-Forwarded-For'), '198.51.100.1');
    });
});

// ------- proxyToTunnel: CF Access service-token injection ------------------

describe('proxyToTunnel — CF Access headers', () => {
    it('injects CF-Access-Client-Id and CF-Access-Client-Secret from env', async () => {
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const env = {
            CF_ACCESS_CLIENT_ID: 'test-client-id.access',
            CF_ACCESS_CLIENT_SECRET: 'test-secret-value',
        };
        await proxyToTunnel(req, env);
        assert.equal(
            lastUpstreamRequest.headers.get('CF-Access-Client-Id'),
            'test-client-id.access',
        );
        assert.equal(
            lastUpstreamRequest.headers.get('CF-Access-Client-Secret'),
            'test-secret-value',
        );
    });

    it('does not inject CF-Access headers when env is missing', async () => {
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        await proxyToTunnel(req, undefined);
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Id'), null);
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Secret'), null);
    });

    it('does not inject CF-Access headers when secrets are absent from env', async () => {
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        await proxyToTunnel(req, {});
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Id'), null);
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Secret'), null);
    });

    it('CF Access headers are present alongside preserved request headers', async () => {
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer user-token', 'Content-Type': 'application/json' },
            body: '{}',
        });
        const env = {
            CF_ACCESS_CLIENT_ID: 'cid',
            CF_ACCESS_CLIENT_SECRET: 'csecret',
        };
        await proxyToTunnel(req, env);
        // Existing headers still present.
        assert.equal(lastUpstreamRequest.headers.get('Authorization'), 'Bearer user-token');
        assert.equal(lastUpstreamRequest.headers.get('Content-Type'), 'application/json');
        // CF Access headers injected alongside.
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Id'), 'cid');
        assert.equal(lastUpstreamRequest.headers.get('CF-Access-Client-Secret'), 'csecret');
    });
});

// ------- proxyToTunnel: method coverage ------------------------------------

describe('proxyToTunnel — method coverage', () => {
    for (const method of ['GET', 'POST', 'OPTIONS', 'DELETE', 'PATCH']) {
        it(`forwards ${method}`, async () => {
            const req = new Request('https://tinyassets.io/mcp', {
                method,
                // Only POST/PATCH/DELETE carry bodies; Request rejects
                // body on GET/OPTIONS in fetch spec.
                body: ['POST', 'PATCH', 'DELETE'].includes(method) ? 'x' : undefined,
            });
            await proxyToTunnel(req);
            assert.equal(lastUpstreamRequest.method, method);
        });
    }
});

// ------- proxyToTunnel: response pass-through ------------------------------

describe('proxyToTunnel — response pass-through', () => {
    it('passes upstream 200 body + headers through', async () => {
        nextUpstreamResponse = new Response('hello', {
            status: 200,
            headers: { 'Content-Type': 'text/plain', 'X-Custom': 'yes' },
        });
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 200);
        assert.equal(res.headers.get('Content-Type'), 'text/plain');
        assert.equal(res.headers.get('X-Custom'), 'yes');
        assert.equal(await res.text(), 'hello');
    });

    it('strips hop-by-hop headers from the response', async () => {
        nextUpstreamResponse = new Response('x', {
            status: 200,
            headers: {
                'Connection': 'close',
                'Transfer-Encoding': 'chunked',
                'X-Ok': 'yes',
            },
        });
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.headers.get('Connection'), null);
        assert.equal(res.headers.get('Transfer-Encoding'), null);
        assert.equal(res.headers.get('X-Ok'), 'yes');
    });

    it('strips every upstream response cookie at the public boundary', async () => {
        const upstreamHeaders = new Headers({
            'Content-Type': 'text/plain',
            'X-Ok': 'yes',
        });
        upstreamHeaders.append('Set-Cookie', 'CF_Authorization=access-token; Secure; HttpOnly');
        upstreamHeaders.append('Set-Cookie', 'application_session=session-token; Secure; HttpOnly');
        nextUpstreamResponse = new Response('stream-safe-body', {
            status: 201,
            statusText: 'Created by origin',
            headers: upstreamHeaders,
        });

        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);

        assert.equal(res.status, 201);
        assert.equal(res.statusText, 'Created by origin');
        assert.equal(res.headers.get('Set-Cookie'), null);
        assert.equal(res.headers.get('Content-Type'), 'text/plain');
        assert.equal(res.headers.get('X-Ok'), 'yes');
        assert.equal(await res.text(), 'stream-safe-body');
    });

    it('preserves separate app session cookies across sign-in, callback and logout', async () => {
        const cookies = [
            '__Host-ta-owner-login=flow; Path=/; Secure; HttpOnly; SameSite=lax',
            '__Host-ta-owner=proof; Path=/; Secure; HttpOnly; SameSite=lax',
            '__Host-ta-approval-return=ref; Path=/; Secure; HttpOnly; SameSite=lax',
            '__Host-ta-model-return=ref; Path=/; Secure; HttpOnly; SameSite=lax',
            'ta_rt=renewal; Path=/app/token; Secure; HttpOnly; SameSite=strict',
            '__Host-ta-owner-login=""; Path=/; Secure; HttpOnly; SameSite=lax; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT',
        ];
        for (const path of ['/app/owner-sign-in', '/app?state=oa_state&code=code', '/app/token']) {
            const headers = new Headers({location: '/app'});
            for (const cookie of cookies) headers.append('Set-Cookie', cookie);
            headers.append('Set-Cookie', 'CF_Authorization=private; Secure; HttpOnly; Path=/; SameSite=lax');
            headers.append('Set-Cookie', 'application_session=unknown; Secure; HttpOnly; Path=/; SameSite=lax');
            nextUpstreamResponse = new Response(null, {status: 303, headers});
            if (path.startsWith('/app?')) {
                const getCookies = nextUpstreamResponse.headers.getSetCookie.bind(nextUpstreamResponse.headers);
                nextUpstreamResponse.headers.getAll = name => {
                    assert.equal(name, 'Set-Cookie');
                    return getCookies();
                };
                nextUpstreamResponse.headers.getSetCookie = undefined;
            }
            const res = await proxyToTunnel(new Request('https://tinyassets.io' + path));
            assert.deepEqual(res.headers.getSetCookie(), cookies);
            assert.equal(res.status, 303);
            assert.equal(res.headers.get('location'), '/app');
        }
    });

    it('rejects app cookies outside app routes and with unsafe attributes', async () => {
        const valid = '__Host-ta-owner=proof; Path=/; Secure; HttpOnly; SameSite=lax';
        const bad = [valid.replace('; Secure', ''), valid.replace('; HttpOnly', ''),
            valid + '; Domain=tinyassets.io', valid.replace('Path=/', 'Path=/app'),
            valid.replace('SameSite=lax', 'SameSite=None')];
        for (const [path, cookies] of [['/mcp', [valid]], ['/app/owner-sign-in', bad]]) {
            const headers = new Headers();
            for (const cookie of cookies) headers.append('Set-Cookie', cookie);
            nextUpstreamResponse = new Response(null, {headers});
            const res = await proxyToTunnel(new Request('https://tinyassets.io' + path));
            assert.equal(res.headers.get('set-cookie'), null);
        }
    });

    it('preserves SSE streaming Content-Type', async () => {
        nextUpstreamResponse = new Response('event: message\ndata: {"x":1}\n\n', {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
        });
        const req = new Request('https://tinyassets.io/mcp', {
            method: 'POST',
            body: '{}',
        });
        const res = await proxyToTunnel(req);
        assert.equal(res.headers.get('Content-Type'), 'text/event-stream');
    });

    it('does NOT buffer body — stream identity preserved', async () => {
        // Build an explicit stream so we can verify it's the same one.
        const upstreamBody = new ReadableStream({
            start(controller) {
                controller.enqueue(new TextEncoder().encode('chunk-1'));
                controller.close();
            },
        });
        nextUpstreamResponse = new Response(upstreamBody, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
        });
        const upstreamResponseBody = nextUpstreamResponse.body;
        const req = new Request('https://tinyassets.io/mcp', { method: 'POST' });
        const res = await proxyToTunnel(req);
        // The exact upstream stream must cross the proxy boundary unchanged.
        assert.strictEqual(res.body, upstreamResponseBody);
        assert.equal(await res.text(), 'chunk-1');
    });
});

// ------- proxyToTunnel: failure paths --------------------------------------

describe('proxyToTunnel — failure translation', () => {
    it('upstream 500 → 502 Bad Gateway with JSON body', async () => {
        nextUpstreamResponse = new Response('internal error', { status: 500 });
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 502);
        assert.equal(res.headers.get('Content-Type'), 'application/json');
        const body = await res.json();
        assert.equal(body.error, 'bad_gateway');
        assert.equal(body.upstream_status, 500);
    });

    it('upstream 503 → 502 Bad Gateway', async () => {
        nextUpstreamResponse = new Response('', { status: 503 });
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 502);
    });

    it('upstream 2xx unaffected by 5xx translation', async () => {
        nextUpstreamResponse = new Response('ok', { status: 200 });
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 200);
    });

    it('upstream 4xx passes through (client errors aren\'t proxy errors)', async () => {
        nextUpstreamResponse = new Response('bad request', { status: 400 });
        const req = new Request('https://tinyassets.io/mcp', { method: 'POST' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 400);
    });

    it('JSON 5xx passes through with body and status intact', async () => {
        // The reason a refusal exists must survive the layer reporting it. This is
        // the 2026-08-28 case: the origin answered 503
        // {"error":"billing_unavailable","detail":"no key"} and the user was shown
        // "tunnel origin returned 5xx" instead.
        nextUpstreamResponse = new Response(
            JSON.stringify({ error: 'billing_unavailable', detail: 'no key' }),
            { status: 503, headers: { 'Content-Type': 'application/json' } },
        );
        const req = new Request('https://tinyassets.io/app/billing/checkout', {
            method: 'POST',
        });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 503, 'the origin status must survive');
        const body = await res.json();
        assert.equal(body.error, 'billing_unavailable');
        assert.equal(body.detail, 'no key');
        assert.equal(res.headers.get('X-TA-Origin-Status'), '503');
    });

    it('JSON 5xx with a charset parameter still passes through', async () => {
        nextUpstreamResponse = new Response(JSON.stringify({ detail: 'boom' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json; charset=utf-8' },
        });
        const res = await proxyToTunnel(
            new Request('https://tinyassets.io/mcp', { method: 'GET' }),
        );
        assert.equal(res.status, 500);
        assert.equal((await res.json()).detail, 'boom');
    });

    it('HTML 5xx is still translated — that is the tunnel, not the app', async () => {
        // A sick tunnel, a dead origin, and the apex fallthrough all emit HTML.
        // Keeping this translated is the whole point of the block.
        nextUpstreamResponse = new Response('<html>502 Bad Gateway</html>', {
            status: 502,
            headers: { 'Content-Type': 'text/html' },
        });
        const res = await proxyToTunnel(
            new Request('https://tinyassets.io/mcp', { method: 'GET' }),
        );
        assert.equal(res.status, 502);
        const body = await res.json();
        assert.equal(body.error, 'bad_gateway');
        assert.equal(body.upstream_status, 502);
    });

    it('a 2xx is not marked with X-TA-Origin-Status', async () => {
        nextUpstreamResponse = new Response('ok', { status: 200 });
        const res = await proxyToTunnel(
            new Request('https://tinyassets.io/mcp', { method: 'GET' }),
        );
        assert.equal(res.headers.get('X-TA-Origin-Status'), null);
    });

    it('network error on upstream fetch → 502', async () => {
        nextUpstreamError = new TypeError('failed to connect');
        const req = new Request('https://tinyassets.io/mcp', { method: 'GET' });
        const res = await proxyToTunnel(req);
        assert.equal(res.status, 502);
        const body = await res.json();
        assert.equal(body.error, 'bad_gateway');
        assert.match(body.message, /failed to connect/);
    });
});

// ------- constants sanity --------------------------------------------------

describe('constants', () => {
    it('TUNNEL_ORIGIN is https://mcp.tinyassets.io', () => {
        assert.equal(TUNNEL_ORIGIN, 'https://mcp.tinyassets.io');
    });

    it('hop-by-hop lists are non-empty + lowercase', () => {
        assert.ok(HOP_BY_HOP_REQUEST_HEADERS.size > 0);
        for (const h of HOP_BY_HOP_REQUEST_HEADERS) {
            assert.equal(h, h.toLowerCase());
        }
        assert.ok(HOP_BY_HOP_RESPONSE_HEADERS.size > 0);
        for (const h of HOP_BY_HOP_RESPONSE_HEADERS) {
            assert.equal(h, h.toLowerCase());
        }
    });
});
