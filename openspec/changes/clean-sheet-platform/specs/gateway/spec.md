## ADDED Requirements

### Requirement: One keyring, held only by the gateway
The gateway Worker SHALL hold the platform's only credential key as one versioned keyring in its own Worker secret, `GATEWAY_KEYRING` (`{"active": kid, "keys": {kid: base64 of 32 bytes}}`), read by one module and never placed in `process.env`. A missing or malformed keyring, or an active kid it does not hold, SHALL fail every call loudly; there SHALL be no default key and no unsealed mode.

#### Scenario: No usable keyring
- **WHEN** the keyring is missing, not JSON, holds a key that is not 32 bytes, or names an active kid it does not hold
- **THEN** every seal, open, model call and forward fails as `keyring_unavailable`

### Requirement: Each credential is sealed to its owner, connection, version and kid
The gateway SHALL seal each credential with AES-256-GCM under a key for that owner alone, HKDF-SHA256 of the root for the blob's kid with the owner as its info, a fresh 96-bit nonce per seal, and additional data binding the owner, the connection, the version and the kid.

#### Scenario: A blob opened under the wrong binding
- **WHEN** a blob sealed for Alice's connection `c1` at version 1 is opened as Bob's, as connection `c2`, as version 2, or relabelled with another kid the ring holds
- **THEN** the open is refused as `credential_refused`, with no detail of which field differed

#### Scenario: A blob changed in transit
- **WHEN** any byte of a blob's ciphertext, nonce or header is changed, or a part is added or removed
- **THEN** the open is refused as `credential_refused`

#### Scenario: A blob under a retiring kid
- **WHEN** a blob sealed under kid `k1` is opened after the ring's active kid became `k2`
- **THEN** it opens, and the gateway also returns it resealed under `k2` at the next version, which opens only at that version

### Requirement: A credential reaches only the endpoints sealed with it
The sealed plaintext SHALL carry the secret together with its source data: the pi-ai provider and API, the base URL, how the secret is sent, and the endpoints (an HTTPS origin, its paths and its methods) it may reach. The gateway SHALL send a secret only to a sealed endpoint, refuse a path holding an encoded separator or dot, never follow a redirect with it, and never send it to a URL a request names.

#### Scenario: Endpoints sealed for one provider used at another
- **WHEN** a request using a credential sealed for one provider's origin names another host, a path or a method not sealed, or a path that resolves onto another host
- **THEN** it is refused as `endpoint_refused` and nothing is sent

#### Scenario: An upstream redirects
- **WHEN** a sealed endpoint answers with a redirect to another host
- **THEN** the redirect is not followed

### Requirement: The gateway serves model calls in pi-ai's pi-messages protocol
`POST /v1/messages` SHALL take pi-ai's pi-messages request with the owner object's envelope (`x-ta-envelope`), open the blob under the binding the envelope names, and run pi-ai's `streamSimple` through pi-ai's provider for the sealed source, sent to the sealed base URL with the opened secret and no ambient credential. It SHALL answer with each pi-ai event re-encoded as the inverse of pi-ai's own pi-messages client, the terminal `done` carrying pi-ai's usage.

#### Scenario: A call through pi-ai's own client
- **WHEN** pi-ai's pi-messages client streams a call through the gateway to pi-ai's adapter for Anthropic Messages, OpenAI Responses, Google Generative AI or a declared custom endpoint
- **THEN** the client rebuilds every event the adapter emitted and the same final message, with the opened secret sent only in the provider's own header

### Requirement: A model call stays within its reservation and deadline
The gateway SHALL clamp a call's output tokens, on the request and on the model, to the envelope's reservation, and SHALL end the call when the caller stops reading and at the envelope's deadline.

#### Scenario: A thinking budget asks for more
- **WHEN** a reasoning request would raise the output-token limit above the reservation to fit a thinking budget
- **THEN** the limit sent upstream stays within the reservation

#### Scenario: The owner's call is cancelled
- **WHEN** the owner object aborts the call, or the deadline passes
- **THEN** the upstream request is cancelled, and the stream ends with an `error` event of `aborted` or `deadline`

### Requirement: Gateway errors are fixed codes
Every error the gateway returns, throws or streams SHALL be one of its fixed codes, carrying no secret, no upstream body or message and no text of an exception it caught; an upstream failure SHALL be classified from the HTTP status the gateway saw.

#### Scenario: An upstream refusal quotes the key
- **WHEN** a provider answers 401, 403, 429 or another 4xx with a body that quotes the credential
- **THEN** the stream ends with `upstream_unauthorized`, `upstream_rate_limited` or `upstream_rejected`, and neither the key nor the body appears anywhere in the answer

### Requirement: Sandbox egress is forwarded raw under the sealed endpoints
`forward` SHALL send a sandbox request's method, path, query, headers and body to the credential's sealed origin only, strip every inbound credential (authorization and API-key headers, cookies, the API-key query parameters, our own `x-ta-` headers and Cloudflare's), inject the sealed secret by the sealed auth template, cut the request at five minutes, pass the provider's answer through, and redact the secret from the answer's body.

#### Scenario: A sandbox client sends a placeholder key
- **WHEN** a request carries a placeholder in `authorization`, `x-api-key` and `?key=`
- **THEN** the provider receives only the sealed secret, in the sealed header, and none of the placeholders

#### Scenario: A provider echoes the key back
- **WHEN** the provider's answer body quotes the secret, split across chunks
- **THEN** the sandbox receives `[redacted]` in its place

### Requirement: OAuth connections are exchanged, refreshed and revoked inside the gateway
`exchange` SHALL run the authorization-code grant with PKCE against the source record's token endpoint and seal the tokens at once; `refresh` SHALL use pi-ai's own refresh for a source whose record names it (Sign in with ChatGPT) and the generic refresh-token grant otherwise, and reseal at the next version; `revokeAtProvider` SHALL revoke by RFC 7009 at the sealed revocation endpoint. A rotated refresh token SHALL replace the old one, and one the server does not rotate SHALL be kept.

#### Scenario: A refused grant
- **WHEN** the token endpoint answers `invalid_grant` with a description quoting the code or token
- **THEN** the call fails as `oauth_invalid_grant` and nothing from the answer is shown

### Requirement: The gateway binds nothing and chooses no owner
The gateway Worker's config SHALL bind nothing and serve no URL: no binding of any kind, no routes, no workers.dev or preview URL, no environment, and only the declared keyring secret, checked against wrangler's schema. Its source SHALL never name `OWNER` or `getByName`, SHALL import of the owner Worker's code only the owner grammar, and SHALL keep no module-level mutable state, so the caller always names the owner and the seal holds the call to it.

#### Scenario: A binding or route is added
- **WHEN** the gateway's config gains any key wrangler offers beyond its allow-list, a workers.dev or preview URL, another secret, or loses one of its flags
- **THEN** the config guard fails CI

#### Scenario: The keyring is read elsewhere
- **WHEN** a gateway source other than the keyring module names `GATEWAY_KEYRING`, or any gateway source names `process`, `OWNER` or `getByName`
- **THEN** the isolation guard fails CI
