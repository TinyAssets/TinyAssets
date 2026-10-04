# Tasks: generic-oauth-connections (Slice 2)

Owner: claude-code. One PR. Tier 2: credential custody plus a public surface.
Cross-family review is owed before landing.

- [x] 1. `connection_oauth.pkce`: the shared PKCE store, handle grammar,
  S256 check and callback test. The first-power preset flow imports them, and
  the fixed generic callback `/app/model-callback/connect` is added.
- [x] 2. `connection_oauth.transport`: every OAuth request goes through the
  SSRF-hardened driver, allowlisted to one URL, bounded, with RFC 6749 error
  detail scrubbed.
- [x] 3. `connection_oauth.discovery`: the `oauth` request validation, RFC
  9728, RFC 8414 and OpenID discovery with issuer checks, coverage, and RFC
  7591 public-client registration.
- [x] 4. The `connect` ask resolves the offer before storage. Sign-in is the
  primary action, key fields are optional with an offer, and the grant
  sentence names the sign-in host and scopes.
- [x] 5. `connection_oauth.flow` plus the `oauth_begin`/`oauth_exchange`
  operations on the app sign-in ingress. The flow is bound to owner,
  universe, request and action digest, and redeemed once.
- [x] 6. `answer_connect_with_token` and a shared `_deposit_answer`.
  `connect_http` accepts `oauth2` only from that path, and a bundle is refused
  under any other scheme.
- [x] 7. `connection_oauth.tokens`: the bundle, the code exchange, refresh
  with rotation, and single-flight `ConnectionTokens` with a re-read inside
  the lock.
- [x] 8. The broker sends the current access token, refreshes once on 401,
  and scrubs both tokens. `ConnectionAuthorizationError` carries the failure
  record across the process boundary. The provider maps it to `auth_invalid`,
  and the effector returns `failure`.
- [x] 9. Rail: the `ConnectOAuth` controller, "Sign in with <host>" as the
  primary action, key paste folded under it, and the callback taken before
  account sign-in.
- [x] 10. Tests (`tests/test_generic_oauth_connections.py`, local fake
  servers only): discovery, preference both ways, the round trip through the
  real callback, refresh before expiry and on 401, concurrent single-flight,
  rotated-token persistence, the failure record, custody, cross-user, and the
  rail JS.
- [x] 11. Tier 2 cross-family review round 1: BLOCK (credential exposure via
  agent-named endpoints). Fixed: endpoints only from discovery on the
  connection host, every host named in consent, token URL pinned, RFC 9207
  `iss`, vault held before a refresh token is spent. Round 2 verifies these.
- [ ] 12. Live acceptance in the founder's app (see the PR). Then deploy,
  check with `deployed_sha.py --assert-contains`, sync the spec delta and
  archive.
