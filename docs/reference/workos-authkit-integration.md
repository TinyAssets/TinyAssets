# WorkOS AuthKit ↔ MCP Resource Server — implementation recipe

Pointer-loaded reference (ADR-002) for the founder-identity build. Design + decision
live in `2026-06-26-founder-and-universe-identity.md (archived in git)` §3.0; this doc
is the *how* for slice 1. Synthesized from WorkOS's own 2026 docs (sources at bottom).

> **Status:** research synthesis (Claude, 2026-06-26); **live values + AS metadata resolved
> against the TinyAssets staging tenant 2026-06-26** (see *Live staging values*). Slice-1
> implementation gets opposite-provider (Codex) review per AGENTS.md.

## Live staging values (resolved 2026-06-26, TinyAssets WorkOS tenant)
Non-secret config (the secret key is **vault-only, not here**). Production gets its own
values + its own upstream Google/GitHub OAuth apps.

| Var | Staging value |
|---|---|
| `WORKOS_CLIENT_ID` | `client_01KW15P07QYSMF9CY4XXXJN520` |
| `WORKOS_AUTHKIT_DOMAIN` | `inventive-van-62-staging.authkit.app` |
| `WORKOS_MCP_RESOURCE` | `https://tinyassets.io/mcp` (register as Resource Indicator — pending) |
| issuer (`iss`) | `https://inventive-van-62-staging.authkit.app` |
| `jwks_uri` | `https://inventive-van-62-staging.authkit.app/oauth2/jwks` |
| authorize / token | `…/oauth2/authorize` · `…/oauth2/token` |
| PKCE | `S256` (required) |
| `scopes_supported` | `openid profile email offline_access` |
| environment | `environment_01KW15NZV1H9T2Z1J12E9FNJG1` (Staging) |

**Validate against `iss` + `jwks_uri` from the AuthKit domain — NOT the legacy
`https://api.workos.com/sso/jwks/<client_id>` (it also 200s but is the SSO product's JWKS).**

**Dashboard state (verified live 2026-06-26):** social providers **Google / GitHub /
Microsoft = Enabled**; Email+Password enabled; Passkeys / Magic Auth disabled.
**Remaining config:** register the **Resource Indicator** (`https://tinyassets.io/mcp`) for
`aud` binding, and allowlist the connector **redirect URI** when wiring the claude.ai OAuth flow.

## Architecture
**AuthKit = the OAuth 2.1 Authorization Server. Our MCP server = the Resource Server.**
The RS has exactly two auth jobs: (1) publish Protected Resource Metadata, (2) validate
the incoming bearer JWT. AuthKit owns login, consent, token issuance, and client
registration. We host **no** authorization-server endpoints.

## Token validation — PyJWT + JWKS, NOT the sealed-session SDK
- The workos-python session helpers (`load_sealed_session().authenticate()`) are the
  **web-app client** pattern (cookies/sealed sessions) — wrong for an RS validating an
  incoming `Authorization: Bearer`. Misusing them throws `InvalidToken`. **Don't.**
- For the RS: **PyJWT + `PyJWKClient`** (handles `kid` matching + key rotation + caching).
  Read the JWKS URL from AS metadata (`.well-known/oauth-authorization-server`)
  so it isn't hardcoded; it is the AuthKit-domain endpoint below.
- **JWKS URL (RESOLVED, see VERIFY-flag 3):** `https://<AUTHKIT_DOMAIN>/oauth2/jwks`
  (e.g. `https://inventive-van-62-staging.authkit.app/oauth2/jwks`). Do **NOT**
  use the legacy SSO endpoint `https://api.workos.com/sso/jwks/<WORKOS_CLIENT_ID>`
  or `user_management.get_jwks_url(...)` — that 200s but is the SSO JWKS, the
  wrong key set for AuthKit access tokens.
- Pin `algorithms=["RS256"]` (algorithm-substitution defense). Validate `iss` **and** `aud`.

## Token claims (AuthKit Sessions doc)
| Claim | Use |
|---|---|
| `sub` | **stable WorkOS user id → our `founder` key.** Docs say use `sub`, not email (email is mutable/unverified). |
| `sid` | session id (sign-out) |
| `iss` | `https://api.workos.com/` **or** your AuthKit domain — VERIFY (flag 1) |
| `org_id` / `role` / `permissions` | only present when an org is selected at sign-in; social-login users w/o org won't have them |
| `exp` / `iat` | standard |
- **Email is NOT in the access token by default.** Either add it as a **custom claim**
  (Dashboard JWT/sessions config — avoids a per-request API call) or look it up server-side
  with `user_management.get_user(sub).email` (needs the API key). Prefer the custom claim.

## MCP discovery / OAuth 2.1 RS plumbing
- **PRM (RFC 9728)** — our server serves `/.well-known/oauth-protected-resource`:
  ```json
  { "resource": "https://tinyassets.io/mcp",
    "authorization_servers": ["https://<authkit_domain>"],
    "bearer_methods_supported": ["header"] }
  ```
- **AS metadata (RFC 8414)** — AuthKit hosts it at
  `https://<authkit_domain>/.well-known/oauth-authorization-server` (advertises
  `authorization_endpoint`, `token_endpoint`, `introspection_endpoint`, `issuer`, `jwks_uri`).
- **Audience binding (RFC 8707)** — register our MCP URL as a **Resource Indicator** in the
  Dashboard; tokens then carry `aud` = that URL. Validate `aud == WORKOS_MCP_RESOURCE`.
- **Client identity** — AuthKit-for-MCP defaults to **CIMD** (2025-11-25 spec) with **DCR**
  for legacy clients. Enable under Dashboard → Connect → Configuration. We manage neither.
- Canonical connector-side guidance: the WorkOS **"Model Context Protocol – AuthKit"** doc.

## The write-gate lives in the dispatcher, not by HTTP verb
All MCP tool calls are POST, so "is this a write?" can't be inferred from the HTTP method.
The middleware only **parses + validates the token → principal**; the **enforcement**
(does this action's `required_scope` match the principal's grants?) happens in the action
dispatcher, keyed by the **scope taxonomy** (`required_scope(action)` — built separately:
reads→read, ordinary writes→write, `create_universe`→costly). A missing or
invalid bearer binds no principal, so the transport challenges the request
before any read or write dispatches. There is no anonymous access mode.

## Minimal middleware (token-parse only; enforcement in dispatcher)
```python
import os, jwt
from jwt import PyJWKClient

_AUTHKIT  = os.environ["WORKOS_AUTHKIT_DOMAIN"]         # e.g. inventive-van-62-staging.authkit.app
_jwks = PyJWKClient(f"https://{_AUTHKIT}/oauth2/jwks")  # AuthKit AS JWKS — NOT api.workos.com/sso/jwks
_RESOURCE = os.environ["WORKOS_MCP_RESOURCE"]           # https://tinyassets.io/mcp
_ISSUER   = f"https://{_AUTHKIT}"                       # VERIFY (flag 1) vs AS metadata

def validate_bearer(token: str) -> dict:
    key = _jwks.get_signing_key_from_jwt(token)
    c = jwt.decode(token, key.key, algorithms=["RS256"],
                   audience=_RESOURCE, issuer=_ISSUER,
                   options={"require": ["exp", "sub"]})
    return {"founder": c["sub"], "email": c.get("email"),
            "scopes": c.get("permissions", []), "role": c.get("role"),
            "org_id": c.get("org_id")}
```
On a protected action with no/invalid principal, return **401** with
`WWW-Authenticate: Bearer error="invalid_token", resource_metadata="<PRM_URL>"` so the
client can discover AuthKit.

## Env / secrets (RS needs very little)
| Var | Value / where | Needed for |
|---|---|---|
| `WORKOS_CLIENT_ID` | `client_…` (Dashboard → API Keys) | JWKS URL + app id |
| `WORKOS_AUTHKIT_DOMAIN` | e.g. `your-app.authkit.app` or custom `auth.tinyassets.io` | `iss` + PRM + AS discovery |
| `WORKOS_MCP_RESOURCE` | `https://tinyassets.io/mcp` (= registered Resource Indicator) | `aud` + PRM `resource` |
| `WORKOS_API_KEY` | `sk_…` (Dashboard → API Keys) | **only** if doing `get_user()` email lookups; keep out of RS if email is a custom claim |
`WORKOS_REDIRECT_URI` / `WORKOS_COOKIE_PASSWORD` are web-app-client concerns — the RS does
not need them. Vault these via `scripts/load_secrets.sh` (add keys to `secrets_keys.txt`).

## Dashboard config checklist
- [ ] Connect → Configuration: CIMD on (DCR stays on for legacy).
- [ ] Register **Resource Indicator** = our MCP URL.
- [ ] Authentication → OAuth providers: enable **Google** + **GitHub**.
  - **Staging: WorkOS provides default Google + GitHub creds — NO own OAuth apps needed** (WorkOS branding on consent; staging-only).
  - **Production: create our own Google Cloud OAuth client + GitHub OAuth App**, paste Client ID/Secret, set WorkOS's redirect URI as the authorized callback.
- [ ] (Optional) add `email` as a custom access-token claim.
- [ ] Serve PRM at `/.well-known/oauth-protected-resource`.
- [ ] **VERIFY** against live AS metadata: exact `issuer` + `jwks_uri`, then validate accordingly.

## VERIFY-flags — status (checked vs live staging metadata 2026-06-26)
1. **Exact `iss`** — ✅ **RESOLVED:** `https://inventive-van-62-staging.authkit.app` (the AuthKit domain, not `api.workos.com/`). Read from AS metadata.
2. **`email` in token** — ⚠️ **STILL VERIFY** by decoding a real access token. `scopes_supported` includes `email`/`profile`, so email *should* appear when requested — but the authoritative claim list omitted it, so confirm on a real token.
3. **JWKS source** — ✅ **RESOLVED:** use `…authkit.app/oauth2/jwks` (from AS metadata). The legacy `api.workos.com/sso/jwks/<client_id>` also 200s but is the SSO JWKS — don't use it.
4. **`aud` literal format** — ⏳ **PENDING:** register `https://tinyassets.io/mcp` as a Resource Indicator, then decode a token to confirm the exact `aud` string.

## Sources
WorkOS docs: AuthKit MCP, AuthKit Sessions, Session tokens/JWKS API ref, Connect/OAuth,
Social Login, Google/GitHub OAuth integrations; WorkOS blog: JWT-in-Python, JWT-validation
guide, FastAPI+AuthKit, "add OAuth to your MCP server", "MCP authorization in 5 OAuth specs";
workos-python SDK + issue #493 (RS token validation).
