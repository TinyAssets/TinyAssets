## Context

Every outbound channel today is a bespoke module. The three the founder named map to
three different credential stories and three different HTTP call sites, verified against
`origin/main` @ `4b4895d4`:

| Channel | Credential source | Endpoint | Auth scheme | Uses connection ledger? |
|---|---|---|---|---|
| `github_pull_request` | vault `resolve_github_token` (vcs/github/dest), env fallback | `api.github.com` (const) | Bearer | Only on the ledger path; the live completion path resolves in-process |
| `slack` (app reply) | vault `resolve_slack_token` (social/slack/connection_id) | `chat.postMessage` (const) | Bearer `xoxb-` | No |
| `twitter_post` | **host process env** `TWITTER_*` | `api.x.com/2/tweets` (const) | OAuth 1.0a HMAC (hand-rolled) | No |

The shared parts already exist and are reused unchanged: the soul-authority gate
(`effectors/authority.resolve_soul_effect_authority`), the consent gate
(`storage/effector_consents.is_consent_active`, granted through
`api/source_channel._approve_sink`), and the receipt/journal lifecycle
(`effectors/outbound_boundary` + `storage/external_write_receipts`). The missing piece is
a general executor: one adapter, one secret resolver, one egress boundary.

## Goals / Non-Goals

**Goals**

- One general `authenticated_external_call` effector that every current and future channel
  routes through, executed inside the existing spawned credential-blind broker.
- A general HTTP `_network_request` driver + a typed multi-secret connection resolver added
  INSIDE the broker child, replacing the GitHub-only driver — not a second executor.
- A named-connection model where `github`/`slack`/`twitter`/`http` are *types*, not gates,
  resolved under authority, with `credential_ref` suppressed in every caller projection.
- A strict SSRF transport + per-connection endpoint allowlist as the egress boundary.
- Incremental migration proven by a per-channel semantic equivalence matrix, atomic per-universe
  cutover, never two credential paths live.

**Non-Goals**

- The grant-as-resource authority model, machine-readable action-cap enforcement,
  system-derived idempotency identity, derived-identity `github_pull_request` receipts,
  goal/universe inboxes, and typed-artifact edges — all owned by `outbound-boundary-layer`
  (landed code, unsynced deltas). This change CONSUMES that machinery; it does not redefine it.
- Credential custody and the vault storage model — owned by `credential-vault`; this change
  adds one general typed-bundle resolver over it.
- Any new advertised MCP handle. Connection CRUD rides existing owner surfaces.
- Money movement, price, or scheduling (`paid-market-*`, `demand-side`).

## Decisions

### D1 — One effector, dispatched from a channel-type registry

Replace the two hard-coded `if sink == ...` ladders
(`effectors/github_pr.run_effects_for_branch:2294` and `effectors/__init__.run_effects_for_branch:83`)
with a single registry-driven dispatch. A node whose declared effect is
`authenticated_external_call` (or a channel-type alias that resolves to it) is dispatched to
the one general adapter. The registry maps `connection_type -> ChannelTypeDescriptor`; it is
data, not a code ladder, and a new channel type is a descriptor, not a platform edit.

**General effector contract** (adapter-side signature; the credential-blind execution seam
is the existing spawned broker, described in D4 — the adapter never resolves a secret):

```
run_authenticated_external_call(
    *, node_id, output_keys, run_state, base_path, run_id
) -> evidence: dict         # never raises to the completion path
```

The adapter parses one `external_write_packet` from the node's declared `output_keys`
(exactly as the current effectors do), where the packet shape is:

```
{
  "sink": "authenticated_external_call",
  "connection": "<connection_id>",         # names a ledger connection; resolved under authority (D2)
  "destination": "<logical destination>",  # the consent/authority key (e.g. owner/repo, #channel, @handle)
  "call": {
    "method": "POST",                       # from the connection type's allowed method set
    "endpoint": "<template key>",           # bound against the connection's endpoint templates
    "path_params": { ... },                 # substituted into the template, each value validated
    "query": { ... },
    "headers": { ... },                     # NON-secret only; caller Host/Authorization/Cookie/proxy headers rejected
    "body": { ... }                          # JSON or form, per the connection type
  },
  "idempotency": { ... }                    # passthrough to the shared receipt lifecycle
}
```

Gate order preserves the load-bearing property that **no secret is touched before consent**
(matching Twitter's current consent-before-credential order at `twitter_post.py:587`; GitHub
today does credential-before-consent, so the two live effectors already disagree — this
order reconciles them safely because the early step touches only the connection descriptor,
never a secret):

1. **Soul authority** — `resolve_soul_effect_authority(universe_dir, sink, destination)`;
   `DENIED` dry-runs, `UNDECLARED` falls through (transitional, unchanged).
2. **Connection descriptor + grant resolution (no secret)** — resolve the connection under
   the authority-checked seam (D2): authenticated actor + server-known universe + active
   grant. This yields only the descriptor the adapter may see — `connection_type`,
   `auth_scheme`, `allowed_endpoints`, and a `grant_id` handle — with `credential_ref` and
   any secret SUPPRESSED. Absent/revoked/ambiguous grant ⇒ dry-run
   `reason=connection_not_resolved`. No secret is resolved here.
3. **Consent** — `is_consent_active(universe_dir, sink=connection_type, destination=...)`.
   Missing ⇒ dry-run `missing_consent`.
4. **Egress admission** — bind the endpoint template against the connection's
   `allowed_endpoints`; the strict SSRF transport (D3) re-validates independently at connect
   time inside the broker child. A NEW gate the caller-supplied-endpoint path requires.
5. **Idempotency + fire in the broker** — `outbound_boundary.execute_replay_safe_effect(...)`
   (or `execute_capped_action` once caps apply), whose invoked call is
   `ScopedConnectionProxy.request(verb, request)`. The secret is resolved and the auth scheme
   applied ONLY inside the broker child (D4/D5), after consent+egress. The proxy returns a
   declassified response; success/held/reconcile evidence uses the existing shapes.

### D2 — Named connections resolve under authority; `credential_ref` is never exposed; secrets are a typed bundle

A connection stored in the ledger carries these fields, extending the existing
`storage/outbound_connections.ConnectionResource` (`{connection_id, owner_user_id,
connection_class, scopes, provider, destination, credential_ref, revoked_at}`, `:23`):

```
Connection {                       # STORAGE shape (never returned verbatim to a caller)
  connection_id:     str
  owner_user_id:     str           # per-universe isolation is inherent here
  connection_type:   str           # "github" | "slack" | "twitter" | "http" | future   (added)
  auth_scheme:       str           # "bearer" | "header" | "basic" | "oauth1a" | "none"  (added)
  allowed_endpoints: [Endpoint]    # egress allowlist: {host, path_template, methods}    (added)
  credential_ref:    str           # vault reference — SUPPRESSED in every caller projection
  scopes, destination, revoked_at  # existing
}

ConnectionView {                   # the ONLY shape returned to adapters / CRUD / list / evidence
  connection_id, connection_type, auth_scheme, allowed_endpoints, destination, revoked_at
}                                  # NO credential_ref, NO secret
```

**Authority-checked resolution, never `connection_id` alone.** `get_connection(connection_id)`
(`:922`) returns the raw `ConnectionResource` including `credential_ref` with **no authority
check** — it must not be the resolution seam for an effect. The adapter resolves through the
authority-checked seam `ConnectionLedger.resolve_exact_scoped_proxy(universe_id, grant_id,
connection_id)` (`:1136`) — or `resolve_scoped_proxy(universe_id, connection_class)` (`:1084`)
where the grant is unambiguous — which requires `require_authenticated_principal_id()`, an
active grant owned by that principal, and the server-known universe, and returns a
`ScopedConnectionProxy` bound to exactly one grant. Absent/revoked/ambiguous ⇒ fail closed.
The credential is resolved only inside the broker child (D4), keyed off the grant's
`credential_ref`, which the adapter never sees.

**Secrets are a typed, per-type bundle — not a single "secret".** A vague single-value
resolver invites whole-record exposure. The broker-side resolver returns a
`ConnectionSecretBundle` whose shape is fixed by `connection_type`: `slack` ⇒
`{bot_token}` (and, for the Socket-Mode source side, a *separate* `app_token` — the two are
non-interchangeable, `credential_vault.py:1238`); `twitter` ⇒ the four OAuth 1.0a values
`{api_key, api_secret, access_token, access_token_secret}` (`twitter_post.py:49`); `github`
⇒ `{token}`; `http` ⇒ `{token}` or `{username, password}` per auth scheme. Response
declassification (D4) scans for *every* value in the bundle, not one string.

**Registry + CRUD.** The design **reuses `ConnectionLedger`** (revocation + per-universe
grant semantics already exist), not a second store. `github`/`slack`/`twitter`/`http` are
`ChannelTypeDescriptor` presets (auth scheme, endpoint templates, request/response mapping)
so a user creating a "post to my Slack" connection supplies a token + workspace, not an HTTP
contract; a raw `http` connection lets a user declare their own endpoints — the "add others"
half of the founder's principle, and why the endpoint allowlist is mandatory. Create / list /
revoke is an owner action through the existing `write_graph target=source_channel`-style
surface (no new advertised handle), and every projection returns `ConnectionView`, never the
storage dataclass.

### D3 — Strict SSRF transport + per-connection endpoint allowlist (new; the allowlist alone is insufficient)

The task brief says "reuse `outbound_boundary.py`" for egress/SSRF — but that module does
**not** filter URLs; it owns the receipt/journal lifecycle. **No URL-egress filter exists
anywhere in the codebase today** (verified: `daemon_server.is_private` is about universe
ACLs, not IPs; each effector's endpoint is a frozen constant, and `slack_transport.py:96` /
`outbound_connections.py:661` call plain `urlopen`, which also honors ambient env proxies).
A general caller-supplied endpoint introduces a new SSRF surface. A per-connection allowlist
is necessary but **not sufficient** — the transport itself must be hardened. The full
contract, enforced inside the broker child, is:

1. **URL parse + canonicalize.** Accept exactly ONE absolute `https://` URL. Reject: any
   userinfo (`user:pass@`), fragments, ASCII control chars / whitespace, backslashes,
   percent-encoded or otherwise malformed host, non-default/unexpected ports, `.`/`..`
   dot-segments, and double-encoding. The bound host+path MUST match one connection
   `Endpoint {host, path_template, methods}` exactly (host equality, template match, each
   substituted param validated against a per-param pattern). Non-`https` is refused (a single
   explicit test-fixture escape aside).
2. **Address classification.** Resolve the host and reject any address that is **not**
   `ipaddress.ip_address(addr).is_global` — this subsumes loopback, link-local
   (`169.254.0.0/16`, incl. cloud metadata `169.254.169.254`), private (RFC-1918), ULA,
   unspecified, reserved, shared (CGNAT `100.64/10`), and multicast, and also
   **IPv4-mapped IPv6** (`::ffff:a.b.c.d`) and unusual IP literals (decimal/octal/hex forms).
   Validate **every** A/AAAA result, not just the first.
3. **Pin + TOCTOU defense.** Select one validated public address and PIN it for the actual
   connection (custom `create_connection` / resolver) so the socket connects to the vetted
   address, while preserving TLS SNI + certificate hostname verification against the original
   hostname. A preflight-resolve-then-plain-`urlopen` is DNS-rebinding/TOCTOU-vulnerable and
   is prohibited; validate the address actually connected to.
4. **Redirects DISABLED by default.** No auto-follow. If a connection ever opts into
   redirects, re-run this entire check per hop and NEVER forward `Authorization`/cookies
   cross-origin.
5. **Ambient proxies DISABLED.** Build the opener with `urllib.request.ProxyHandler({})` so
   env proxies cannot exfiltrate the request or defeat address pinning.
6. **Bounds.** Enforce connect/read timeouts, a maximum response body size (streamed, capped),
   a maximum header count/size, a redirect-hop cap, and a decompression bound (zip-bomb guard).
7. **No caller-controlled sensitive headers.** `Host`, `Authorization`, `Cookie`, and any
   proxy header supplied in the packet's `headers` are rejected; auth headers come only from
   the `auth_scheme` handler (D5).

This supersedes the "rot-prone denylist" WebFetch confine for the effector egress path: an
allowlist keyed to the user's own declared endpoints over an `is_global` transport is
inherently per-universe and does not rot the way a global denylist does.

### D4 — The execution seam IS the existing spawned credential-blind broker (no in-process path)

There is **no in-process secret-resolution path.** In-process resolution is not structurally
credential-blind: Python introspection, logging, tracebacks, and exception chaining all leak
the value — `slack_transport.py:89` already documents an `Authorization`-header leak through
`URLError.__context__`. So the seam is the **existing** spawned broker, which is real landed
code, and this change EXTENDS it before any channel migrates:

- `ConnectionLedger.resolve_exact_scoped_proxy` (`:1136`) starts a spawned worker
  (`_run_proxy_worker`, `:159`) and returns a `ScopedConnectionProxy` (`:286`). The adapter
  calls `proxy.request(verb, request)`; the request/response cross the process boundary as
  redacted JSON, and the credential never does.
- Inside the child, `CredentialBlindBroker.dispatch` (`:325`) resolves the credential
  (`self._resolve_credential(resource.credential_ref)`), calls `_network_request(...)`, and —
  critically — scans the returned object with `_contains_secret(response, credential)` (`:369`)
  before returning, raising a sanitized `ProxyRequestError` if any secret is present. Adapter-
  facing errors are already reduced to fixed strings (`_adapter_safe_proxy_error`, `:111`).

This change adds, all inside the broker child:

- A **general HTTP `_network_request` driver** to replace/augment the GitHub-only
  `_ProductionGitHubNetworkDriver` (`:587`), applying the D3 strict transport and the D5 auth
  scheme. It is selected by `connection_type` in `_TrustedNetworkDriver` (`:677`).
- A **general typed-bundle credential resolver** alongside `_ProductionVaultCredentialResolver`
  (`:429`) in `_TrustedCredentialResolver` (`:482`), returning the `ConnectionSecretBundle`
  (D2) for the connection type.
- **Response declassification over the whole bundle:** `_contains_secret` (`:726`) is applied
  to *every* value in the bundle, not a single credential string, so no bundle member can ride
  a response back to the caller.

Flipping the live completion path onto the broker is this change's own work (not deferred):
`github_pr`'s live completion path today resolves in-process, so migrating it means routing it
through the proxy. Cap enforcement and derived-identity receipts remain
`outbound-boundary-layer`'s to own on top of this seam.

### D5 — Auth schemes are declarative handlers **inside the broker child**, keyed off `auth_scheme`

The three current header formats become `auth_scheme` handlers that run inside the broker
child (never adapter-side): `bearer` (`Authorization: Bearer <token>` — GitHub, Slack bot),
`oauth1a` (the HMAC-SHA1 signer in `twitter_post._oauth_header:337`, lifted verbatim into the
handler), and `header`/`basic` for arbitrary `http` connections. The handler reads the typed
`ConnectionSecretBundle` (D2) and constructs the signed request entirely within the child, so
the auth material is applied where the credential already legitimately lives. New channel
types add a handler only if they need a scheme not already present.

### D6 — Migration proves a per-channel semantic equivalence matrix, not byte parity

"Byte parity" cannot capture gate order, receipt compatibility, or exception behavior, and it
would rubber-stamp a wire-identical call that silently reordered a gate. Per Hard Rule
(differential testing) and the memory *"subagents build to their mental model, not reality"*,
each channel keeps its original effector **verbatim** in the test suite as the oracle and must
pass a **semantic equivalence matrix** before its module is deleted. The matrix columns:

- **Normalized wire request** — endpoint, method, body, and non-auth headers identical; auth
  material normalized (OAuth nonce/timestamp) but structurally equivalent.
- **Refusal / gate ORDER** — the order in which each gate can dry-run/refuse. Record the
  intentional reconciliation: the general order touches no secret before consent (D1), which
  differs from GitHub's current credential-before-consent order and matches Twitter's
  consent-before-credential order (`twitter_post.py:587`). This is a deliberate delta, logged,
  not an accident.
- **Receipt / evidence compatibility** — same `external_write_receipts` lifecycle keys and the
  same evidence field shapes existing consumers read.
- **Exception behavior** — never raises into the completion path; the same failure classes; no
  secret in any error (broker sanitization).
- **Intentional deltas** — every difference from the oracle named explicitly (e.g. Twitter
  credential source moving from env to vault).
- **Flags** — named default, scope, readiness criteria, and rollback for each channel.
- **Concurrency** — behavior under concurrent calls (no credential/receipt cross-leak).

Order, each behind a per-universe atomic selection (D-migration):

1. **`slack` first** — Slice 3's background follow-up (`app_outbound_adapter` +
   `build_slack_transport`) depends on it, and Slack is the simplest (single Bearer POST).
   The injected `Transport` resolves the named `slack` connection through the broker and
   dispatches `authenticated_external_call` (POST `chat.postMessage`). `app_outbound_receipts`
   idempotency (keyed on authorization digest) is a *different* concern and is unchanged.
2. **`github_pull_request`** — highest-risk (multi-step: blobs → tree → commit → ref → PR).
   The PR materialization stays a github-typed request handler behind the general connection;
   only credential resolution + dispatch + egress unify. Leaves the
   `outbound-boundary-layer`-owned derived-identity/reconciliation requirements untouched.
3. **`twitter_post`** — moves credentials from host env to a vault `twitter` connection via
   the atomic cutover below, OAuth 1.0a via the `oauth1a` handler.

**Backfill + atomic cutover, never dual live.** Slack and GitHub already have exact vault
records keyed by connection id (`credential_vault.py:1201` for Slack; the vcs/github record
for GitHub), so migration first BACKFILLS a ledger connection from that record, then the
universe atomically selects legacy-effector-OR-broker (a single per-universe readiness flag,
not a dual-read). A disabled or new path that fails MUST fail closed — never fall through to
another credential source, and never borrow ambient env. Twitter's cutover is stricter (D-twitter
below) because it has no vault record today.

**Twitter cutover (closes the cross-universe env hole).** Do NOT dual-read vault-then-env — a
dual-read keeps the ambient-env hole open and violates "never two credential paths live". Instead:
(a) provision + **verify** the vault `twitter` connection for the universe first; (b) flip that
universe atomically to the broker path; (c) preserve destination-derived account binding
(`twitter_post.py:507`) so the posted account still derives from the authorized destination, never
a payload handle; (d) prove live rollback. The legacy `TWITTER_*` env resolution
(`twitter_post._resolve_credentials:190`) is removed the moment a universe is on the broker path;
the new path never reads process env.

## Risks / Trade-offs

- **[Risk] Dependency on / collision with `outbound-boundary-layer`.** Both target
  `external-effect-adapters`, and this change builds ON that change's landed broker/proxy. →
  This change adds a general-adapter + connection-registry + strict-SSRF-transport requirement
  and a Twitter-credential requirement; it does NOT touch the
  `github_pull_request`/`windows_desktop` requirements that `outbound-boundary-layer` renames+
  modifies. The general adapter is the seam those modified derived-identity receipts sit on top
  of. The final `external-effect-adapters` sync must combine both — confirmed with Codex/founder.
- **[Risk] New SSRF surface — the allowlist alone is not enough.** A general caller-supplied
  endpoint is strictly more dangerous than frozen constants. → The strict transport (D3) —
  canonical single HTTPS URL, `is_global`-only with all A/AAAA validated, address pinning with
  preserved TLS verification, redirects and ambient proxies disabled, response bounds — plus a
  mandatory per-connection allowlist (empty ⇒ no call), all enforced in the broker child.
  Fail-closed on any ambiguity.
- **[Risk] In-process resolution would leak the secret.** → There is no in-process path; the
  seam is the spawned broker whose `_contains_secret` scan (extended over the whole typed
  bundle) declassifies every response, and whose adapter-facing errors are fixed strings.
- **[Risk] `connection_id`-only resolution would expose `credential_ref` / bypass authority.**
  → Resolution goes through `resolve_exact_scoped_proxy`/`resolve_scoped_proxy` (authenticated
  actor + grant + server-known universe); `get_connection` is never the effect seam; every
  projection returns `ConnectionView` (no `credential_ref`).
- **[Risk] GitHub's multi-step PR flow does not fit a single call.** → `github` stays a typed
  request handler (a sequence of allowed calls under one connection) behind the general
  connection; the generalization is credential+dispatch+egress, not the PR algorithm.
- **[Risk] Equivalence proof misses gate order or exception shape.** → The semantic
  equivalence matrix (D6) has explicit columns for gate order, receipt/evidence compatibility,
  exception behavior, and concurrency — not just the wire request.
- **[Risk] Twitter cutover could preserve the env hole.** → No dual-read; provision+verify the
  vault connection first, then atomic per-universe flip to the broker; env resolution removed
  the moment a universe is on the broker path; live rollback proven.

## Migration Plan

1. **Extend the broker first, all dark:** general HTTP `_network_request` driver + typed-bundle
   credential resolver + whole-bundle `_contains_secret` declassification inside the child; the
   strict SSRF transport (D3); the `ChannelTypeDescriptor` registry; `ConnectionView` projection
   + authority-checked resolution; the general adapter. No channel migrated.
2. Backfill a `slack` ledger connection; flip Slack atomically to the broker path; prove the D6
   matrix; `ui-test`. Re-express Slice 3's `build_slack_transport` as an instance.
3. Backfill a `github` ledger connection; flip GitHub atomically; prove the D6 matrix.
4. Provision+verify a `twitter` connection; flip atomically; remove `TWITTER_*` env on that
   universe; prove live rollback and destination-derived account binding.
5. Replace both hard-coded dispatch ladders with the registry; delete each bespoke effector only
   after its matrix passes. Keep the per-service vault resolvers as thin wrappers over the general
   typed resolver until every caller is migrated, then collapse.
6. Sync deltas in one coordinated `external-effect-adapters` sync with `outbound-boundary-layer`.

Rollback is per channel: flip the universe's readiness flag back to the legacy effector and
revert. No step may leave a channel with two live credential paths or a fall-through to another
credential source.

## Open Questions

- Confirm the coordinated `external-effect-adapters` sync sequencing with `outbound-boundary-layer`
  (its derived-identity receipts sit on this general adapter) — Codex + founder.
- Where do users author `allowed_endpoints` for a raw `http` connection, and who reviews a new
  channel-type descriptor before it becomes a shareable commons artifact?
- Should `connection_type` presets live in code (trusted) or be user-authored data (remixable)?
  The founder wants "add others"; a fully user-authored auth scheme is a larger trust surface than
  a user-authored endpoint list over a fixed set of `auth_scheme` handlers.
- What is the address-pinning mechanism on the deployed stack (custom `HTTPSConnection` / resolver)
  that both pins the vetted IP and preserves TLS SNI + hostname verification?
