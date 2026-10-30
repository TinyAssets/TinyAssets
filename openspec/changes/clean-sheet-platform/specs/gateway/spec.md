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
- **WHEN** a sealed endpoint, a token endpoint or a revocation endpoint answers with a redirect to another host
- **THEN** the redirect is not followed

#### Scenario: A URL that is not canonical
- **WHEN** a sealed base URL, origin or endpoint URL carries an empty `?` or `#`, a query or fragment where none belongs, an upper-case or trailing-dot host, a dot segment or a default port
- **THEN** it is refused as `invalid_source`, so what is sealed is what every later parse reads

### Requirement: Only APIs whose every request goes through fetch run
The gateway SHALL run only the pi-ai APIs whose every request goes through `fetch`, and SHALL refuse any other (Bedrock, the legacy Codex backend) when it is sealed and when it is called.

#### Scenario: A source on Bedrock or the legacy Codex backend
- **WHEN** a source names `bedrock-converse-stream` or `openai-codex-responses`
- **THEN** sealing it is refused as `invalid_source`, and the catalog refuses it as `unsupported_source`

### Requirement: Every adapter request goes through the call's guard
Each model call SHALL run inside its own `AsyncLocalStorage` scope in which the global `fetch` is that call's endpoint guard, so an adapter that refuses `options.fetch` (pi-ai's Google adapters) is held to the sealed origin, paths and methods and never follows a redirect. Outside any scope the global `fetch` SHALL be the runtime's own.

#### Scenario: Google's sealed origin redirects
- **WHEN** a Google call's sealed origin answers 302 or 307 to another host
- **THEN** the redirect is not followed, nothing reaches the other host, and the stream ends with `upstream_error`

#### Scenario: Two calls at once
- **WHEN** two owners' Google calls run concurrently
- **THEN** each upstream request carries only its own call's key

### Requirement: The interposer is the module-state guard's one pinned exemption
The interposer SHALL be the one exemption from the platform's module-state guard, pinned to its one file, to the three findings it needs (the scope, the captured runtime fetch, the one write of the global) and to the file's SHA-256: Google's SDK takes no fetch, and nothing else holds it to the seal without forking pi-ai. The scope SHALL hold no data between calls.

#### Scenario: The exemption widens
- **WHEN** the interposer's file changes, gains a finding, or its text appears in another file
- **THEN** the module-state guard fails CI

### Requirement: The gateway serves model calls in pi-ai's pi-messages protocol
`POST /v1/messages` SHALL take pi-ai's pi-messages request with the owner object's envelope (`x-ta-envelope`), open the blob under the binding the envelope names, and run pi-ai's `streamSimple` through pi-ai's provider for the sealed source, sent to the sealed base URL with the opened secret and no ambient credential. It SHALL answer with each pi-ai event re-encoded as the inverse of pi-ai's own pi-messages client, the terminal `done` carrying pi-ai's usage.

#### Scenario: A call through pi-ai's own client
- **WHEN** pi-ai's pi-messages client streams a call through the gateway to pi-ai's adapter for Anthropic Messages, OpenAI Responses, Google Generative AI or a declared custom endpoint
- **THEN** the client rebuilds every event the adapter emitted and the same final message, with the opened secret sent only in the provider's own header

### Requirement: A model call stays within its reservation and deadline
The gateway SHALL clamp a call's output tokens, on the request and on the model, to the envelope's reservation, SHALL refuse a reasoning call whose reservation is under 2048 tokens (no room for the smallest thinking budget beside pi-ai's answer floor) as `reservation_too_small`, and SHALL end the call when the caller stops reading and at the envelope's deadline.

#### Scenario: A thinking budget asks for more
- **WHEN** a reasoning request would raise the output-token limit above the reservation to fit a thinking budget
- **THEN** the limit sent upstream stays within the reservation

#### Scenario: The owner's call is cancelled
- **WHEN** the owner object aborts the call, or the deadline passes
- **THEN** the upstream request is cancelled, and the stream ends with an `error` event of `aborted` or `deadline`

### Requirement: Gateway errors are fixed codes
Every error the gateway returns, throws or streams SHALL be one of its fixed codes, carrying no secret, no upstream body or message and no text of an exception it caught; an upstream failure SHALL be classified from the HTTP status the gateway saw, and the adapter's message SHALL be read only by pi-ai's own overflow detection, never shown.

#### Scenario: An upstream refusal quotes the key
- **WHEN** a provider answers 401, 403, 429 or another 4xx with a body that quotes the credential
- **THEN** the gateway answers `upstream_unauthorized`, `upstream_rate_limited` or `upstream_rejected` with status 502, and neither the key nor the body appears anywhere in the answer

### Requirement: A refusal before any output is answered with its status
A model call the provider refused before producing anything SHALL be answered with its fixed code and that code's non-200 status, not a 200 stream, so the owner settles it at no cost. A failure after the stream started SHALL end the stream with an `error` event of its code.

#### Scenario: The deadline passes mid-answer
- **WHEN** a call's deadline passes after the provider began answering
- **THEN** the answer is a 200 stream that ends with an `error` event of `deadline`

### Requirement: A transient refusal's code is one pi-ai retries
An upstream status SHALL be named so that pi-ai's own retry classifier reads it as the provider meant: 408 `upstream_timeout`, 429 `upstream_rate_limited`, 503 and 529 `upstream_overloaded` and any other 5xx `upstream_server_error`, which it retries; 401 and 403 `upstream_unauthorized` and any other 4xx `upstream_rejected`, which it does not.

#### Scenario: An overloaded provider
- **WHEN** a provider answers 529 before any output
- **THEN** the gateway answers `upstream_overloaded`, and pi's agent, allowed a retry, tries the call again

### Requirement: A context too long for the model reaches pi as an overflow
A model call's refusal SHALL be read for an overflow before the generic mapping: any 413, and any refusal but a 401, 403 or 429 whose adapter message pi-ai's `isContextOverflow` reads as one, SHALL be answered `context_length_exceeded` with status 413, the phrase pi's overflow detection matches, so pi compacts and retries once.

#### Scenario: Anthropic's "prompt is too long"
- **WHEN** a provider refuses a call in its own words for its context's length, in any of the Anthropic, OpenAI Responses or Google wire formats
- **THEN** the gateway answers `context_length_exceeded` with status 413, pi-ai's own client reads the answer as an overflow, and none of the provider's words appear in it

#### Scenario: A tokens-per-minute limit
- **WHEN** a provider answers 429 with words that read like an overflow ("too many tokens")
- **THEN** the gateway answers `upstream_rate_limited`, by its status

### Requirement: Every other overflow is the same refusal
A stream the provider fails for its context's length after a 2xx SHALL end with an `error` event of `context_length_exceeded`, and a request body past the gateway's bound SHALL be refused with it.

#### Scenario: A response that fails mid-stream
- **WHEN** the OpenAI Responses API answers 200 and then fails the response with `context_length_exceeded`
- **THEN** the stream ends with an `error` event of `context_length_exceeded`, and none of the provider's words

### Requirement: Sandbox egress is forwarded raw under the sealed endpoints
`forward` SHALL send a sandbox request's method, path, query, headers and body to the credential's sealed origin only, strip every inbound credential (authorization and API-key headers, cookies, method overrides, the API-key query parameters, our own `x-ta-` headers and Cloudflare's), inject the sealed secret by the sealed auth template, cut the request at five minutes, and pass the provider's answer through.

#### Scenario: A sandbox client sends a placeholder key
- **WHEN** a request carries a placeholder in `authorization`, `x-api-key` and `?key=`
- **THEN** the provider receives only the sealed secret, in the sealed header, and none of the placeholders

### Requirement: Forwarded answers never carry a secret
`forward` SHALL ask the provider for an unencoded answer and SHALL redact every secret the credential holds, as written and JSON-escaped, from the answer's headers and body.

#### Scenario: A provider echoes the key back
- **WHEN** the provider's answer quotes the access token, the refresh token or a client secret, in a header or in the body split across chunks
- **THEN** the sandbox receives `[redacted]` in its place

### Requirement: OAuth connections are exchanged, refreshed and revoked inside the gateway
`exchange` SHALL run the authorization-code grant with PKCE against the source record's token endpoint and seal the tokens at once; `refresh` SHALL use pi-ai's own refresh for a source whose record names it (Sign in with ChatGPT) and the generic refresh-token grant otherwise, and reseal at the next version; `revokeAtProvider` SHALL revoke by RFC 7009 at the sealed revocation endpoint. A rotated refresh token SHALL replace the old one, and one the server does not rotate SHALL be kept.

#### Scenario: A provider refresh for a consumer subscription
- **WHEN** a source asks for pi-ai's own refresh for any provider but Sign in with ChatGPT, or with any token endpoint but OpenAI's
- **THEN** it is refused when it is sealed, so no consumer subscription's OAuth runs in the gateway

#### Scenario: A refused grant
- **WHEN** the token endpoint answers `invalid_grant` with a description quoting the code or token
- **THEN** the call fails as `oauth_invalid_grant` and nothing from the answer is shown

### Requirement: A key-exchange source's code buys a sealed API key
For a key-exchange source (the Python's `pkce_user_key_v1`, OpenRouter's), `exchange` SHALL post the code and verifier once, as JSON, to the sealed exchange endpoint, and seal the key it answers as an API key with no expiry.

#### Scenario: A key exchange that is refused or redirected
- **WHEN** a key-exchange endpoint answers other than `200` with a key, or redirects
- **THEN** the call fails with a fixed code, the redirect is never followed, and nothing is sealed

### Requirement: The doors reach only the connect steps
The gateway SHALL serve the doors Worker through a named entrypoint, `ConnectGateway`, that offers only `seal`, `exchange` and `verify`; model calls, egress forwarding, refresh, reseal and provider revocation SHALL stay on the entrypoint only the owner Worker binds.

#### Scenario: The doors call a model or forward egress
- **WHEN** the doors' binding is asked for `fetch`, `forward`, `refresh`, `reseal` or `revokeAtProvider`
- **THEN** the entrypoint has no such method

### Requirement: The gateway binds nothing and chooses no owner
The gateway Worker's config SHALL bind nothing and serve no URL: no binding of any kind, no routes, no workers.dev or preview URL, no environment, and only the declared keyring secret, checked against wrangler's schema. Its source SHALL never name `OWNER` or `getByName`, SHALL import of the owner Worker's code only the owner grammar, and SHALL keep no module-level mutable state, so the caller always names the owner and the seal holds the call to it.

#### Scenario: A binding or route is added
- **WHEN** the gateway's config gains any key wrangler offers beyond its allow-list, a workers.dev or preview URL, another secret, or loses one of its flags
- **THEN** the config guard fails CI

#### Scenario: The keyring is read elsewhere
- **WHEN** a gateway source other than the keyring module names `GATEWAY_KEYRING`, or any gateway source names `process`, `OWNER` or `getByName`
- **THEN** the isolation guard fails CI

### Requirement: The setup check lists each model with the metadata the owner needs
`verify` SHALL answer a working credential with the models its source serves (pi-ai's catalog for a built-in provider, the declared list for a custom endpoint), each its whole pi-ai record but for what is the gateway's own (provider, API, base URL, headers, compatibility and sampling parameters), since the owner object holds no catalog of its own and lists, prices, compacts and offers thinking levels by them.

#### Scenario: A key is checked at connect time
- **WHEN** the binder verifies a sealed Anthropic key that the provider accepts
- **THEN** the answer lists pi-ai's Anthropic models with their whole catalog records, in catalog order

### Requirement: A declared endpoint's models are whole records
A declared endpoint's models SHALL be pi-ai records of the same fields, each checked whole when it is sealed and again when it is opened.

#### Scenario: A declared model names a field pi-ai's record does not have
- **WHEN** a custom source declares a model with a compatibility block, an unknown thinking level, or a price tier with no threshold
- **THEN** the source is refused `invalid_source`
