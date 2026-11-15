# b13-signin as built (2026-11-15)

`untangle/b13-signin` builds `d2` B13's sign-in half, which X4 left out (`x4.md` item 7): the sign-in the doors and the deploy kit both hold moves into the contract, and `identity.json` gains `connected`, `connect_callback` and the app's page paths. The doors' sign-in wire is unchanged.

1. **`contract/src/signin.ts` holds what both sides read of the sign-in,** as d2 B13 decided: the `UPSTREAM` record's type (`UpstreamConfig`) with its one parser (`upstreamConfig`), `SECRET_ORIGIN`, the pure `authorizeUrl` builder and `SIGN_IN_MAX_AGE_S`.
   - The doors read the record with it (`doors/src/index.ts`), and `oidc.ts`'s upstream builds its URL with it. The doors take a sign-in by its max age (`authorize.ts`).
   - The kit builds its record on `SECRET_ORIGIN` (its own `WORKOS_API` goes), and its early sign-in check fetches `authorizeUrl` (its own `authorizeLink` goes). The probe's sign names `SIGN_IN_MAX_AGE_S`, not "600".
   - `oidcUpstream` stays in the doors. The deploy test imported it only for its `authorizeUrl`, which is now the contract's one builder. Its code exchange, token checks and erasure run in the doors alone.
2. **The kit checks its record with the doors' parser** (`upstreamFrom`), so a record the doors would refuse at every sign-in stops the kit before any deploy. The test used to hold the two together. Behaviour change, declared: a discovery whose `jwks_uri` the doors refuse (one with a query, say) now stops the kit. Before, it deployed a record that refused every sign-in.
3. **The identity's URLs that both sides read are `contract/src/identity.ts`'s,** beside `APP_RESOURCE`: `ORIGIN`, `UPSTREAM_CALLBACK`, `WEB_CLIENT` and `WEB_REDIRECT`.
   - The doors' `config.ts` (`PUBLIC_ORIGIN`, `UPSTREAM_CALLBACK`, `WEB_CLIENT`, `WEB_REDIRECT`) and the kit's `facts.ts` (`ORIGIN`, `CALLBACK`, `FIRST_PARTY`) each built them from `identity.json`. Now both import them.
   - The URLs only one side reads stay with it: the doors' paths, the kit's `AUTHORIZE` and `E2B_HOOK_URL`.
   - The canary's MCP door and its resource document read `identity.mcp` (`deploy/canary.ts`), in place of a literal `/mcp`.
   - Not `signin.ts`: `identity.ts` was already the home of the identity's URLs (`APP_RESOURCE`, which the owner reads too), and an origin is no sign-in value.
4. **`identity.json` gains `connect_callback` (`/oauth/connect/callback`) and `connected` (`/app/connected`).**
   - The app's three pages are `first_party.resource`, `first_party.redirect` and `connected`. The doors key them by those paths (`pages.ts`).
   - The identity guard holds both new paths to `routes`.
5. **The deploy test imports the contract, not `doors/src/{config,oidc}.ts`,** so `deploy/guards/rules.ts`'s one owed import goes, and the file with it. The layer table needed no change: the kit and its tests could already import the contract.
6. **Bundles.** `identity.json` is bundled whole, so the two keys move the runner (`76c86a0b…`, main `98992400…`) and the probes (`32fd5052…`, main `3d9bfa38…`). The bundles differ only by those two keys. The image (`s0e740fbe1f9fae8b`) is unchanged. The pair check is owed at the dark redeploy.
7. **Left, with reasons:**
   - The identity's host and MCP resource are still built from `identity.json` in three places: the doors' `PUBLIC_HOST` and `MCP_RESOURCE`, the kit's `ZONE` beside the contract's own `ZONE` (`egress.ts`), and `plugins/build.mjs`'s `ORIGIN`, `SERVER` and `LEGAL`. They are not sign-in values, and they span lanes D and V. **Fix: `untangle/identity-urls`.**
   - `oidc.ts`'s `unbase64url` and `bounded` stay as `tasks.md` §10 lists them (lane X, owed). Using `fromBase64url` would change the 502 page's words for a malformed token, and this PR keeps the doors' wire unchanged. **Fix: `untangle/x-oidc-readers`.**
