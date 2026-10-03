> Current release hold (2026-10-03): the installed HF source is unavailable.
> Its allowance can roll into paid usage, and the platform has no verified free-only
> boundary. The screen omits it and direct preset sign-in refuses before creating
> a request. The protocol tests below use an explicitly enabled nonbillable fake;
> they do not establish real-account billing eligibility. Re-enabling the source
> requires a separately reviewed spending boundary or bounded priced-consent design.

# Design: Hugging Face sign-in source

Scope of this note: the storage and authority shape of the new connection type
(what a refute review must check). UI copy is in the PR and the spec delta that
follows it.

## Decision: compose, do not add a connection type

A Hugging Face source is a platform-raised **`connect` ask** with a model use
and an `oauth` request, answered by the **existing generic OAuth flow**
(`connection_oauth.flow`, openspec `generic-oauth-connections`). Nothing new is
stored:

| Concern | Where it already lives |
|---|---|
| Token custody | `oauth2` bundle in the owner's credential vault (`connection_oauth.tokens.encode`), `vault://http/model-huggingface`, owner-scoped, never in a response, never exported |
| Refresh | the broker's single-flight refresh (`ConnectionTokens.current` -> `credential_refresh.refresh_credential`). Hugging Face metadata advertises `refresh_token` in `grant_types_supported` (fetched 2026-10-01). No refresh token issued -> the next 401 is a `ConnectionAuthorizationError`, and the existing reconnect card asks the owner to sign in again |
| Endpoint pinning | every endpoint comes from discovery rooted at the connection's own hosts (`resolve_offer`); the bundle is pinned to the discovered token URL (`answer_connect_with_token`) |
| Flow binding | `connection_oauth_flows`: owner + universe + request + action digest, single redemption, PKCE S256 verifier never leaves the tab |
| Model use + money floor | `uses.model` declared, `billing: "free"`; `_model_use_refusal` at ask and answer; `apply_connection_uses(owner_confirmed=True)` |
| Pool membership | an explicit `bind_model_access` confirmation (same as the key-paste source cards), unless nothing powers the universe yet, in which case `select_model_if_unpowered` serves on it with free-only caps. The confirmation is raised only when the redeemed request is platform-origin AND its action equals the canonical action rebuilt from installed data, never on destination alone (finding 3) |
| Billing honesty | `billing: "free"` is the declared list's claim, not a provider spending boundary. Hugging Face free accounts get $0.10/month and need a credit purchase for more; PRO accounts are billed past $2. The ask body says this in words, as the key-paste cards already do ("TinyAssets cannot enforce the provider's billing settings"). Residual, owed to the founder: a priced-consent representation for sources whose free allowance can roll into the user's paid credit (finding 2) |
| Expiry recovery | a failed refresh or missing refresh token fails loudly (`ConnectionAuthorizationError` -> `auth_invalid` notice). The repair is the same connect-screen button: a second sign-in re-deposits onto the same destination. Generic OAuth reconnect cards (today limited to `llm_subscription`) are a separate change (finding 5) |
| Fallback | `free-source-pooling`: an accepted owner-bound source is the next hop when another is daily-exhausted. No router change |

## The ask the platform raises

`POST /app/model-connect/source_sign_in {"preset_id": "huggingface"}`
(same-origin JSON, signed-in owner, home resolved like `deposit_key`). The
server builds the action ONLY from installed data (`free_source_presets.json`
entry with a `sign_in` block); the browser sends nothing but the preset id:

```json
{"type": "connect", "destination": "model-huggingface", "auth_scheme": "bearer",
 "endpoints": [
   {"host": "router.huggingface.co", "path_template": "/v1/chat/completions", "methods": ["POST"]}],
 "access": "exact",
 "uses": {"model": {"wire": "openai_chat", "billing": "free",
                    "models": [{"id": "openai/gpt-oss-120b", "tools": true, "context": 32768},
                               {"id": "openai/gpt-oss-20b", "tools": true, "context": 32768}]}},
 "oauth": {"scopes": ["openid", "profile", "inference-api"], "client_id": "<see below>"}}
```

It goes through `request_from_user(origin="platform")`, so every existing
validation runs, the offer is resolved before storage, and a refusal (no offer)
creates no row. The identical payload dedupes onto the same pending row, so a
second tap reuses it. The browser then runs the existing `oauth_begin` /
callback / `oauth_exchange` for that request id.

**Where discovery is rooted.** Discovery is rooted at the connection's declared
hosts. `router.huggingface.co` publishes no RFC 9728 document (404,
2026-10-01); the issuer is `https://huggingface.co`. The platform passes the
installed issuer host as a server-set keyword, `request_from_user(...,
sign_in_hosts=("huggingface.co",))`, exactly like `origin`: never read from the
payload, so an agent cannot supply it, and it is tried before the endpoint
hosts. The connection keeps ONE host (the provider's inference path requires
exactly one, `api_key_http_provider._single_host`) and no profile endpoint is
granted. (Codex refute 2026-10-01, findings 1 and 6.)

An agent-raised ask with the same payload gets no `sign_in_hosts`, so discovery
from the router host finds no offer and the fieldless ask is refused: no row,
so it cannot pre-create the platform's row under its own origin (finding 4).

## Client identity

Order: `TINYASSETS_HUGGINGFACE_OAUTH_CLIENT_ID` when set (a public OAuth app the
founder registered); otherwise a **Client ID Metadata Document**: the client id
is the https URL of a document this deployment serves at
`/app/oauth/client-metadata.json`, whose own `client_id` field is that URL (Hugging Face metadata:
`client_id_metadata_document_supported: true`). The document is constant, holds
no secret, and names exactly one redirect URI, the fixed generic callback
`<public origin>/app/model-callback/connect`, with `token_endpoint_auth_method:
"none"`. No dynamic registration is used for this source (it would mint a new
HF client per sign-in, and HF lists no `none` auth method for registration).

The route is public (no bearer), like `/app/sw.js`; it is added to the
middleware's explicit carve-out list, not swept in by prefix. It is derived from
the configured public resource, never from the request Host header.

## Authority checks a reviewer should try to break

1. The browser can choose only a preset id; endpoints, scopes, client id,
   models, billing and the discovery root are installed data.
2. The ask is the owner's own (`_owner_gate`), on their own home; another
   owner's flow handle cannot be redeemed (existing flow tests).
3. The money floor runs on a connection that already has a priced source.
4. The token never reaches a response, log or chat; the existing oauth_exchange
   response carries the receipt only.
5. The client-metadata document cannot be steered to another redirect URI.

## Not in scope

Gemini via our OAuth (bills our project, not the user) and the Gemini CLI
personal login (founder/legal question) are not built.

## Review log

- 2026-10-01 Codex gpt-6-astra refute: ADAPT. Folded: one-host connection with
  a server-set discovery root (1, 4, 6); canonical-action + origin check before
  the pool confirmation (3); CIMD carries its own `client_id` (7). Disclosed and
  left as residuals: billing roll-over (2), generic OAuth reconnect card (5).
