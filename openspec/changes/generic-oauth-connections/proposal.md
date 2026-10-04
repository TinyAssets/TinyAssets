# Generic OAuth for any connection, preferred when offered

**Founder, 2026-09-24:** "Our generic connector should prefer OAuth when the
provider allows for what the request is trying to accomplish, as that is less
actions for the user." PLAN Providers (PR #3965) states the principle: support
comes from standard discovery or from connection data, never per-provider code;
key paste is the fallback; tokens refresh generically with no reconnect.

This is Slice 2 of the vendor-neutral migration plan. Slice 1
(`unify-connection-uses`, #3956) made an LLM just another connection behind one
`connect` request. This slice lets that request be answered by signing in.

## The problem

Every `connect` ask is a key paste. For a provider with OAuth that is the most
user actions (find the developer page, create a key, copy it, paste it) and the
weakest custody (a long-lived key). The platform already has a working PKCE
transport, but only for the one bundled first-power preset, which exchanges a
code for a long-lived key through a non-standard endpoint. It has no standard
authorization-code flow, no refresh, and no way to discover whether a provider
offers OAuth at all. A token that expires today means a reconnect (concern
2026-09-01: the subscription credential dies at first expiry).

## What changes

1. **Discovery decides the primary action.** A `connect` ask runs standard
   discovery against the connection's own host(s): RFC 9728 protected-resource
   metadata names the authorization server, then RFC 8414 or OpenID
   configuration describes it. The ask's `oauth` says only what the use needs
   (`scopes`, optionally a public `client_id`). **Endpoints and issuers are
   never supplied**: every URL a code, verifier or refresh token goes to is
   discovered from the connection's own declared host (Tier 2 review round 1:
   an agent-named token endpoint beside a real sign-in page would collect them).
   When the server covers the request
   (authorization code, PKCE S256, every requested scope, a public client), the
   resolved offer is stored on the ask and signing in is its primary action.
   Key fields become optional and fold under "Paste a key instead". Otherwise
   the ask is a key paste, and the requester is told why.
2. **One tap to sign in, back automatically.** The rail's "Sign in with
   <host>" starts authorization code + PKCE for a public client (RFC 7591
   dynamic registration when the server advertises it). The provider returns
   to one fixed callback, `/app/model-callback/connect`, the existing
   callback route generalized. The app redeems the code once. The token bundle
   is deposited through the same answer path a pasted key uses, under auth
   scheme `oauth2`, and the request is answered.
3. **Generic refresh in the broker.** An `oauth2` connection's vault string is
   a token bundle (access, refresh, expiry, token URL, client id). The
   credential-blind broker sends the current access token, refreshing before
   expiry and once on 401. Refresh is single-flight per connection across
   threads and processes, so a single-use refresh token is never spent twice.
   A rotated refresh token is written back through the vault's atomic write. A
   failed refresh is an ordinary connection failure record: stage
   `connection`, class `auth`, the token endpoint's own words as detail.
4. **One transport.** Discovery, registration, the code exchange and refresh
   all go through the SSRF-hardened outbound driver: HTTPS, public addresses,
   no redirects, bounded, allowlisted to the one URL. The first-power preset
   flow now uses the shared PKCE store, handle grammar and callback.

## Custody

Tokens reach the vault only through a completed sign-in. `oauth2` is not a
pasteable auth scheme, since its bundle names the URL a refresh token is sent
to, and a bundle pasted under any other scheme is refused at the door and at
the driver. No token crosses MCP or the app. The flow store holds only a
hashed handle and the S256 challenge. A flow, a pending request and a
connection are all bound to one owner and one universe.

## Tier and review

**Tier 2: credential custody** (new token deposit path, refresh inside the
broker, rotated-token persistence) plus a public surface (the `oauth` field on
the `connect` ask, two operations on the app sign-in ingress). A cross-family
review is owed before landing.

## Registered-client extension (2026-10-04)

The founder registered a platform Google client. A trusted provider directory
now supplies registered clients generically, before public-client discovery.
Client secrets remain in daemon custody for exchange and refresh; asks, the
browser, offers and token bundles never carry them. This extends the original
public-client scope without adding provider-specific runtime branches. Live
provider consent and deployment acceptance remain unfinished in task 12.

## Out of scope

- Device code and client credentials grants (the plan's slice 2 lists them;
  this change is authorization code only, which covers the founder's case).
- Client secrets supplied through an ask or browser. Registered confidential
  clients use the trusted directory and daemon custody described above.
- Porting the first-power preset exchange onto standard OAuth. Its provider's
  flow returns a key, not tokens. It stays data, as slice 6 renders it.
- Sender-constrained tokens (DPoP, MTLS). Refused loudly as unsupported.
- The native Android shell (sign-in there needs the in-app tab return path).
  Key paste still works.
