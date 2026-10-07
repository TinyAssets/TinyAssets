# Tasks — onboarding-web-app

Backfill for a surface built to the founder directive 2026-08-20. Final
acceptance is a real/new user against the DEPLOYED cloud daemon — never a local
run. Built + verified items are checked; deploy-side and acceptance items remain.

## Slice 1 — daemon-served same-origin route (built)
- [x] 1.1 Register `GET /mcp/app` in `create_streamable_http_app()` before the MCP
      transport (mirrors `starlette_discovery_routes()`), served under `/mcp/` so
      the production tunnel reaches it with no infra change.
- [x] 1.2 Dark flag `TINYASSETS_ONBOARDING_APP`: route returns 404 until set.
- [x] 1.3 Verify anonymous `GET /mcp/app` reaches the daemon (404, not a 401
      challenge) so the SPA loads pre-sign-in with no auth-middleware change.
- [x] 1.4 Self-contained SPA shipped in the package (`tinyassets/onboarding/`);
      confirm it lands in the Claude-plugin runtime mirror (incl. `app.html`).

## Slice 2 — in-browser WorkOS PKCE, same-origin /mcp (built)
- [x] 2.1 In-browser Authorization Code + PKCE against AuthKit (public client),
      `resource` = the advertised MCP resource (RFC 8707); token in `sessionStorage`.
- [x] 2.2 Same-origin `fetch('/mcp')` with the Bearer; MCP initialize +
      `tools/call`; SSE/JSON envelope parsing.
- [x] 2.3 Public-config injection derived from the connector's Protected Resource
      Metadata (client id + AuthKit endpoints + resource); not-configured → honest
      notice, not a broken redirect.

## Slice 3 — funnel over canonical handles (built)
- [x] 3.1 Single `converse` chat thread rendering the universe's reply verbatim.
- [x] 3.2 `held / setup_required` envelope rendered honestly with a
      connect-subscription affordance.
- [x] 3.3 Deposit via `write_graph target=connection operation=connect_llm`
      (`{service, auth_material_b64}`); credential base64'd in-browser, sent to
      /mcp, cleared from the field, never logged.
- [x] 3.4 `get_status` heartbeat (host/universe-name).

## Slice 4 — hardening (built)
- [x] 4.1 Per-request CSP nonce on inline script/style; `default-src 'none'`,
      `connect-src 'self' + AuthKit origin`, `frame-ancestors/base-uri/form-action`
      locked. No `'unsafe-inline'`.
- [x] 4.2 DOM-XSS-safe: all universe/user text via text nodes, never `innerHTML`.
- [x] 4.3 Config injection escaped against script-context breakout; secret-leak
      test.
- [x] 4.4 Unit tests: dark flag, handler 200/404, config injection + escaping,
      per-request nonce, no-secret-leak, route shape (17 tests).
- [x] 4.5 Cross-family (Codex) adversarial review of the daemon-served build:
      no DOM-XSS/nonce/JSON-breakout issues. Adapt fixes applied — `/mcp/app`
      exempted from the auth challenge so onboarding loads in every auth mode
      (`_auth_challenge_path`), deposit credential cleared from the DOM before any
      await, sign-out forces `prompt=login` next authorize; token-in-sessionStorage
      documented as a directive-mandated tradeoff. ASGI-level middleware test added.

## Slice 5 — deploy + acceptance (host / real user)
- [ ] 5.1 Host: register/confirm a WorkOS public client with `redirect_uris`
      including `https://tinyassets.io/mcp/app`; set
      `TINYASSETS_ONBOARDING_APP_CLIENT_ID`.
- [ ] 5.2 Host: set `TINYASSETS_ONBOARDING_APP=1`; deploy the daemon.
- [ ] 5.3 Confirm the daemon does not retain `/mcp/app` query strings in logs.
- [ ] 5.4 Acceptance: a real/new user against `https://tinyassets.io/mcp/app`
      signs in, meets their universe, connects a subscription (needs the
      `byo-llm-connect-flow` `connect_llm` handler deployed), and chats — end to
      end in the cloud.
