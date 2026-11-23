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

#### Scenario: The sweep before a kid is retired
- **WHEN** the owner's sweep asks `reseal` for a connection's blob, or `resealDoorbell` for a doorbell subscription's, sealed under `k1` while the active kid is `k2`
- **THEN** the blob comes back resealed under `k2` at the next version, which opens only at that version, so no blob of either kind is left under `k1`

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
The gateway SHALL run only the pi-ai APIs whose adapter sends every request through the `fetch` it is handed and on which a sandbox call can be metered, each a row of one contract table (`api-rules.ts`) with its rules and whether a declared endpoint may speak it, and SHALL refuse any other API, and a declared endpoint on an API its row does not allow, when it is sealed and when it is opened.

#### Scenario: A source on Bedrock or the legacy Codex backend
- **WHEN** a source names `bedrock-converse-stream` or `openai-codex-responses`
- **THEN** sealing it is refused as `invalid_source`, and opening a credential sealed on it is refused as `credential_refused`

#### Scenario: A declared endpoint on an API with no declared adapter
- **WHEN** a custom source declares its models on `mistral-conversations`
- **THEN** sealing it is refused as `invalid_source`, though a built-in Mistral source seals

### Requirement: An adapter's requests reach only the run's own fetch
Each run of a pi-ai adapter the gateway makes (the meter's learning runs and answer reads, `meter.ts`) SHALL hand the adapter that run's own fetch, and the adapter SHALL send every request through it, never the global `fetch`. The gateway serves no model call of its own: sandbox egress is forwarded raw (`forward.ts`), never following a redirect.

#### Scenario: An adapter reaching past its fetch
- **WHEN** each API-key source's model call, and a declared endpoint's on each API one may speak, is learned while the global `fetch` refuses every request
- **THEN** every one is learned, so no adapter of an API the gateway runs reached the global `fetch`

### Requirement: Gateway errors are fixed codes
Every error the gateway returns or throws SHALL be one of its fixed codes, carrying no secret, no upstream body or message and no text of an exception it caught; an upstream failure SHALL be classified from the HTTP status the gateway saw: 401 and 403 `upstream_unauthorized`, 408 `upstream_timeout`, 429 `upstream_rate_limited`, any other 4xx `upstream_rejected`, 503 and 529 `upstream_overloaded`, any other 5xx `upstream_server_error`.

#### Scenario: An upstream refusal quotes the key
- **WHEN** a provider answers 401, 403, 429 or another 4xx with a body that quotes the credential
- **THEN** the gateway answers `upstream_unauthorized`, `upstream_rate_limited` or `upstream_rejected` with status 502, and neither the key nor the body appears anywhere in the answer

### Requirement: Sandbox egress is forwarded raw under the sealed endpoints
`forward` SHALL send a sandbox request's method, path, query, headers and body to the credential's sealed origin only, strip every inbound credential (authorization and API-key headers, cookies, method overrides, the API-key query parameters in any case, our own `x-ta-` headers and Cloudflare's), inject the sealed secret by the sealed auth template, cut the request at `FORWARD_DEADLINE_MS`, a day (`EGRESS_DEADLINE_S`: the sandbox's own continuous-runtime ceiling), and pass the provider's answer through, cutting no answer for its silence: a silent answer is its engine's to give up on (each engine's own client ends a stream that sends nothing for 300 s or less), and the caller's cancel SHALL end the provider's request.

#### Scenario: A sandbox client sends a placeholder key
- **WHEN** a request carries a placeholder in `authorization`, `x-api-key` and `?key=`
- **THEN** the provider receives only the sealed secret, in the sealed header, and none of the placeholders

#### Scenario: A provider goes silent mid-answer
- **WHEN** the provider's answer has sent nothing for an hour, and then its caller cancels it
- **THEN** the gateway cut nothing while it was silent, and the caller's cancel aborts the provider's request

### Requirement: A sandbox's model call is metered with pi-ai's own adapter
On the `EgressGateway` entrypoint, `forward` SHALL meter the call: pi-ai's adapter for the sealed API, run once against a fetch of the gateway's own that reaches no network, SHALL say which method and path its model call is, where the model id goes and which body field caps the output. A call on any other path SHALL be refused `unmetered`; the call's model SHALL be read where pi-ai names it and found in the catalog (else `unknown_model`); it SHALL hold slots of the isolate's pool (`GATEWAY_POOL_SLOTS`, 16 of one 4 MiB unit; `slots.ts`) as its body arrives, ⌈`TEXT_COST`·read / 4 MiB⌉ taken before the bytes that need them are kept, and, once the body is in and before its parse, its weight's (`BYTE_COST` or, for an ASCII body, `ASCII_BYTE_COST` a byte, plus `TOKEN_COST` a structural token counted as the bytes stream in; measured in workerd over body shapes and asserted after each phase by the memory leg), a call weighing more than the pool refused `egress_too_large`; each wait for a slot the pool's own (`slots.ts`'s one admission rule: owners in turn, wait-die for a call that holds units and must grow, an owner at half the pool or more taking no more while another owner waits), refused `busy` (503, `Retry-After`) past it; each unit's worth of body SHALL arrive within the pool's wait of that unit's grant, its clock running only while the call waits on its sender (refused `invalid_request`), the reserve within the pool's wait (`busy`), and the upload within `heldDeadlineMs`; the slots let go when its upload's last write is taken or at any earlier end, at its caller's stop (`aborted`, its read and its reserve stopped), never across the provider's wait for its first token; a body SHALL be refused `egress_too_large` as soon as its weight so far passes what one call may hold, a `\u` escape weighing as non-ASCII; its body SHALL be exactly its declared `Content-Length` (within `BODY_MAX`, the egress door's `EGRESS_BODY_MAX`), decoded chunk by chunk and parsed once, and within the length its reservation was made for; every end before its fetch SHALL be `failed`, since nothing was sent; a key at a level the rules read that is another spelling of a read key (ASCII case, snake or camel) or not ASCII at all, or, where the path names the model, a body key that names one, SHALL be refused `egress_aliased_key`, since ProtoJSON and case-blind parsers (Go's folds Unicode case) take it for the read key; every output-cap alias its API reads SHALL be clamped to the reservation's `max_output_tokens`; a call asking several choices SHALL be refused `egress_multiple_choices` and one enabling a server-side tool the provider bills beyond tokens `egress_unmetered_tool`, by each API's data; and a streamed call SHALL ask for its usage report as pi-ai's own call does. the model and tier SHALL be read from that one parse and reserved through the egress door's `reserve` only once every body refusal has passed, a refused reserve answered `egress_reserve_refused` and nothing sent; one setting an option priced beyond tokens that pi-ai does not price SHALL be refused `egress_unpriced_option`; the output caps SHALL be clamped in place, and the call sent as the bytes of that one parse, encoded once, through a fixed-length stream, so the provider gets its `Content-Length`; an option the rules default when absent (`default_when_absent`) SHALL be sent at that value, the one pi-ai prices, and an answer that reports a served tier pi-ai does not price SHALL be `unknown` (one where its API's rules give an absent multiplier, reporting no tier, settles at it); its SSE SHALL be decoded as the SSE specification does; and a provider beta header (`*-beta`) SHALL be stripped from a metered call unless its API's rules list it. The answer SHALL be teed through the same adapter, so pi-ai's own parser reads its usage, priced at the vendor's own bill when its answer carries one that pi-ai reads (`usage.cost.totalFromProvider`), whatever served it, and otherwise at pi-ai's own total for the model the call named, or a dearer served model's price scaled by the same ratio; a served model the catalog does not list SHALL be settled at the named model's price, its id kept on the receipt, only when it is that model's dated or versioned snapshot, the named model is priced and the call listed no fallbacks, and `unknown` otherwise; a router's call (its reservation's `routed`: a model its vendor bills as the model it routes each call to, which may route to a priced model) SHALL be priced at the served model alone, and `unknown` unless the answer names another model the catalog prices above nothing; an answer with no usage SHALL be `unknown`; and the outcome (`succeeded` with tokens and cost, `failed`, or `unknown`) SHALL come back on a second stream once the answer ended. A stop stream the caller closes SHALL abort the provider's request. With no `reserve`, `forward` SHALL send unmetered a read (`GET`, `HEAD`) of a sealed endpoint, or a call its API's rules cite as free (`free`: each keyed by its provider and API, with the provider's page that says so and the day it was read, and never the source's metered model call), and refuse any other `unmetered`.

#### Scenario: A stream pi-ai cannot read to its end
- **WHEN** the answer is cut, aborted, or not the adapter's stream format
- **THEN** the outcome is `unknown`, and the owner keeps the whole reservation

### Requirement: Forwarded answers never carry a secret
`forward` SHALL ask the provider for an unencoded answer and SHALL redact every secret the credential holds, as written and JSON-escaped, from the answer's headers and body.

#### Scenario: A provider echoes the key back
- **WHEN** the provider's answer quotes the access token, the refresh token or a client secret, in a header or in the body split across chunks
- **THEN** the sandbox receives `[redacted]` in its place

### Requirement: OAuth connections are exchanged, refreshed and revoked inside the gateway
`exchange` SHALL run the authorization-code grant with PKCE against the source record's token endpoint and seal the tokens at once; `refresh` SHALL use the refresh-token grant (RFC 6749 §6) at the sealed token endpoint, never following a redirect, and reseal at the next version; `revokeAtProvider` SHALL revoke by RFC 7009 at the sealed revocation endpoint. A rotated refresh token SHALL replace the old one, and one the server does not rotate SHALL be kept.

#### Scenario: A refused grant
- **WHEN** the token endpoint answers `invalid_grant` with a description quoting the code or token
- **THEN** the call fails as `oauth_invalid_grant` and nothing from the answer is shown

### Requirement: A key-exchange source's code buys a sealed API key
For a key-exchange source (the Python's `pkce_user_key_v1`, OpenRouter's), `exchange` SHALL post the code and verifier once, as JSON, to the sealed exchange endpoint, and seal the key it answers as an API key with no expiry.

#### Scenario: A key exchange that is refused or redirected
- **WHEN** a key-exchange endpoint answers other than `200` with a key, or redirects
- **THEN** the call fails with a fixed code, the redirect is never followed, and nothing is sealed

### Requirement: The doors reach only the connect steps and egress forwarding
The gateway SHALL serve the doors Worker through two named entrypoints: `ConnectGateway`, which offers only `seal`, `exchange` and `verify`, and `EgressGateway`, which offers only `forward`, metered, for the egress door, once the owner's object admitted the call, reserving it through the door's `reserve`. Refresh, the reseals (a connection's and a doorbell subscription's) and provider revocation SHALL stay on the entrypoint only the owner Worker binds, which SHALL offer no seal, exchange, verify or forward of its own, so no entrypoint forwards a call unmetered, and whose `fetch` serves no model call (`/v1/messages` went with the in-object loop, ADR-034).

#### Scenario: The doors call a model
- **WHEN** either doors binding is asked for `fetch`, `refresh`, `reseal`, `resealDoorbell` or `revokeAtProvider`, or the connect binding for `forward`
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
`verify` SHALL answer a working credential with its egress scope (the sealed base URL and endpoints and pi-ai's metered call, no secret, which the owner's object keeps to refuse an egress call to an endpoint not sealed or not metered before any forward) and the models its source serves (pi-ai's catalog for a built-in provider, the declared list for a custom endpoint), each its whole pi-ai record but for what is the gateway's own (provider, API, base URL, headers, compatibility and sampling parameters), since the owner object holds no catalog of its own and lists, prices, compacts and offers thinking levels by them.

#### Scenario: A key is checked at connect time
- **WHEN** the binder verifies a sealed Anthropic key that the provider accepts
- **THEN** the answer lists pi-ai's Anthropic models with their whole catalog records, in catalog order

### Requirement: A declared endpoint's models are whole records
A declared endpoint's models SHALL be pi-ai records of the same fields, each checked whole when it is sealed and again when it is opened, within the owner object's own bounds (ids and names of 200 characters, prices of $1M a million tokens, token counts of 100M), so a record sealed here commits there.

#### Scenario: A declared model names a field pi-ai's record does not have
- **WHEN** a custom source declares a model with a compatibility block, a thinking level pi-ai does not have, a price tier whose threshold is missing or not a whole count of tokens, or a price or token count past the owner's bounds
- **THEN** the source is refused `invalid_source`
