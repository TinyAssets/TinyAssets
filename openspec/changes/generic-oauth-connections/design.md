# Design: generic OAuth for connections (Slice 2)

## D1. The offer: discovery rooted at the connection's own host

**Trust root (review round 1, BLOCK).** Authorize, token and registration
endpoints are trusted only through the daemon-owned directory (D7), or
standard discovery from the connection's own declared hosts. An agent-named URL
or issuer is refused at validation: the issuer check
only proves metadata came from the URL the agent chose, so such metadata could
pair a real `authorization_endpoint` with the agent's `token_endpoint`. The
ask's `oauth` carries only `scopes`, a named default-scope `use`, and an
optional public `client_id`. The directory is tried before the discovery
sequence below; an inactive or unmatched entry falls through.

`connection_oauth.discovery.resolve_offer(requested, hosts)` returns
`(offer, "")` or `(None, reason)`:

1. **Discovery** against the connection's hosts (at most two):
   `https://<host>/.well-known/oauth-protected-resource` (RFC 9728, whose
   `resource` must be that host) names the issuers. Otherwise the host itself
   is tried as an issuer. For each issuer, the RFC 8414 URL
   (`/.well-known/oauth-authorization-server` inserted before the issuer path)
   comes first, then OpenID (`<issuer>/.well-known/openid-configuration`). The
   document's `issuer` must equal the issuer it was fetched for.
   No requester-named issuer is accepted.
2. **Coverage.** `S256` is in `code_challenge_methods_supported`, `code` in
   `response_types_supported`, and `authorization_code` in
   `grant_types_supported` (RFC 8414 default when omitted). The requested
   scopes must be a subset of `scopes_supported` when the server lists them.
   There must be a client: a supplied id or a `registration_endpoint`.

The offer `{issuer, authorize_url, token_url, client_id, registration_url,
iss_parameter_supported, scopes, source: "discovered"}` replaces the ask's `oauth` before storage, so it is part of
the displayed-row binding (the dedupe hash). An agent cannot write one
directly, because unknown keys such as `source` are refused. Discovery never
fails an ask. Without an offer the ask needs key fields as before, and the
response carries `oauth_unavailable: <reason>`. With one, key fields are
optional and the response says `primary: "sign_in"` plus the `redirect_uri` a
hand-registered client must list.

The test suite turns discovery off by injection (`tests/conftest.py`). Tests
that exercise it turn it on against a local fake server.

## D2. The flow: the hosted PKCE transport, generalized

`connection_oauth.pkce` now owns the PKCE store (`.hosted-model-auth.db`), the
handle grammar, the S256 check and the callback test. The first-power preset
flow imports them unchanged. A second table, `connection_oauth_flows`, binds
each handle digest to owner, universe, request id, the action digest, the S256
challenge, the client id and the redirect URI, with the same 10-minute TTL and
per-owner cap.

- **Begin** (`POST /mcp/app/model-connect/oauth_begin {request_id,
  code_challenge}`). The request must be the owner's, pending, and a
  `connect` with an offer that still matches what was shown. If the offer has
  no client id, a public client is registered (RFC 7591,
  `token_endpoint_auth_method: none`; a server that issues a secret is
  refused). Begin returns the authorize URL with `state` set to the handle.
- **Callback.** The fixed redirect URI is `/app/model-callback/connect`,
  because a standard client is registered for an exact URI. It is served by
  the existing callback route as a public shell, with no redemption. The app
  strips `code` and `state` before account sign-in reads the query.
- **Exchange** (`POST .../oauth_exchange {flow, code, code_verifier}`). The
  flow is taken once: owner, universe, S256 and the action digest are checked
  and the row is deleted. The code goes to the token URL (RFC 6749 §4.1.3 with
  the verifier). `pending_requests.answer_connect_with_token` then runs the
  same owner, pending, binding and money-floor checks as a pasted answer, and
  `_deposit_answer` deposits under `oauth2` and completes the connect.

## D3. Tokens and refresh

The vault string for `oauth2` is `{"scheme":"oauth2","v":1,access_token,
token_type,expires_at,refresh_token,token_url,client_id,scope}`. The token URL
lives in the bundle, not on the readable connection row, so nothing an agent
configures can redirect a refresh token. Only `bearer` token types are
accepted.

`ConnectionTokens.current(destination, credential, rejected="")`, called by
the broker:

- If the token is not within 60s of expiry and nothing was rejected, the stored
  access token is sent. With no `expires_in`, the token refreshes only on 401.
- Otherwise the broker takes a per-connection in-process lock and an OS file
  lock (`<universe>/.oauth-refresh/<sha>.lock`, polled up to 45s). It
  **re-reads the vault inside the lock**. If another holder has already
  replaced the token, it uses theirs. If not, it refreshes (RFC 6749 §6),
  keeping the old refresh token when none is returned, and writes the rotated
  bundle through `write_credential_vault` before returning.
- On a 401 from the service, the broker calls it again with
  `rejected=<the token it sent>` and retries once, only if the token changed.

The broker scrubs responses against the access and refresh tokens. The
driver refuses a bundle string under any scheme, so a mutated row can never
send a refresh token as a key.

## D4. Failures are records

A refresh that fails (the endpoint refused, no refresh token, the rotated
token could not be saved, or the lock timed out) raises
`ConnectionAuthorizationError`, a `ProxyRequestError` whose `failure` is
`{stage: "connection", class: "auth", provider_detail}`. The detail is the
token endpoint's HTTP status plus its RFC 6749 `error` and
`error_description`, bounded and scrubbed. The failure crosses the broker
process boundary intact. The model provider maps it to
`ProviderAuthenticationError` (`auth_invalid`, the connection stage), and a
workflow call returns it as `failure` with
`error_kind: connection_authorization_failed`. A 401 that survives the one
retry is `auth_invalid` too.

## D5. Rail

`ConnectOAuth` in `app.html` is self-contained. `railBody` calls
`ConnectOAuth.decorate` once at its end, which keeps this change compatible
with the slice 6 rail (#3964). An ask with an offer gets a primary "Sign in
with <authorize host>" button. Its key fields move under "Paste a key
instead". With no key fields, Accept is hidden. Callback handling runs as the
script loads, before account sign-in. After the app is signed in again and the
thread is restored, it redeems the code, refreshes the rail and relays the
owner's line.

## Rollback

Revert the PR. Existing `oauth2` connections then fail closed: the scheme is
unknown to the ledger and the broker, so they are refused. The owner
reconnects with a key. No other stored shape changes.

## D6. Round 1 floor

- **Consent names every host.** The grant sentence names the authorize host,
  the token host, and every host the sign-in contacts (`offer_hosts`).
- **Pin.** `answer_connect_with_token` decodes the bundle and refuses unless
  its `token_url` equals the platform-resolved `token_url` stored on the approved
  request. `_has_sign_in` trusts platform-resolved offers with
  `source: "discovered"` or `source: "directory"`; the requester cannot set it.
- **RFC 9207.** The app forwards the callback's `iss`. `complete` refuses a
  mismatch with the discovered issuer, and refuses a missing `iss` when the
  server advertised `authorization_response_iss_parameter_supported`.
- **Vault held before spending.** `credential_vault.exclusive_credential_vault`
  yields a writer under the exclusive admission. The refresher takes it
  (retrying to the 45s deadline) BEFORE sending the refresh token, re-reads
  inside it, and writes the rotated bundle under the same hold (the write is
  retried to the deadline). If the vault cannot be held, nothing is spent.

## D7. Registered clients remain daemon-owned data (2026-10-04)

The packaged `connection_oauth/providers.json`, or an absolute-path
`TINYASSETS_OAUTH_DIRECTORY` replacement, maps exact declared hosts to trusted
endpoints, scope sets and environment credential names. Relative overrides
fail loading with `oauth_directory_invalid`; optional-directory failures are
logged with a fixed code and fall through to discovery without preventing
launch. Entries missing credentials are inactive. Explicit scopes must fit
the union of the entry's declared scope sets.
An active match takes precedence over a requested public client ID.

Registered clients keep authorization code, S256 PKCE and existing flow
ownership. Only the opaque provider ID is added to offers and token bundles.
Before exchange or refresh the daemon re-pins client ID and token URL against
the current directory, then resolves the secret. Confidential failures expose
fixed codes/status, not provider prose. Secrets are filtered from child
environments and removed at daemon startup and before engine/scoped-broker spawn.

Child refresh uses a private loopback capability bound by the launcher to one
owner and universe. The daemon rechecks canonical admin authority or the
founder-home binding, plus deposit ownership,
uses the existing refresh locks and vault admission, and returns success only.
The child rereads its own vault. Each daemon launch gets a capability revoked
on engine stop/restart or proxy close/failed startup; child proxies reuse the
parent capability. Public-client refresh remains unchanged.

These inheritance protections do not isolate same-UID processes from the
daemon. The concern `docs/concerns/2026-10-04-engine-mcp-shares-daemon-uid.md`
must be resolved before production secret activation. This extension does not
claim deployment or a live registered-provider consent pass.
