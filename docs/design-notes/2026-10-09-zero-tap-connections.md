# Fewest-tap connections, without service-specific platform code

Research date: **2026-10-09**. `initial_provider: codex`. Research/recommendations, not implementation acceptance.
Baseline: `5f231a5705` on `research/zero-tap-connections`; read the two 2026-10-04 Muse notes, all four named changes, and the LinkedIn concern before researching deltas.
Evidence: **D** = official product/spec documentation; **I** = integrator documenting its own service; **R** = recommendation/inference. No authenticated Muse UI session was measured.
Freshness: every external URL below was checked on 2026-10-09. **OLD** marks publication/update before 2026-04-09; **U** means no reliable publication/update date, not “recent.” Retrieval/crawl dates are not publication dates. This is an October snapshot, not a prediction of the rest of 2026.

## Executive finding

**Build one protected Connect action, automatic discovery/registration, server-completed authorization, and automatic task continuation.** One tap *inside TinyAssets* is attainable; one tap end-to-end for every unknown service is not. Provider consent, account selection, MFA, missing client registration, and manually issued keys remain external constraints.
An unknown conforming MCP endpoint can work without service-specific client code. An arbitrary website or service name cannot promise that: finding its endpoint, obtaining accepted client identity, getting user permission, and knowing its API are four separate problems.
Muse combines standard OAuth, registered integrations, agent-written connectors and browser use. Public evidence does **not** establish a universal registration bypass or a fixed one-tap OAuth flow.
Preserve the founder's no-per-platform-registration/no-aggregator default from the October 5 correction. Existing approved registration data can remain optional; growing a hosted registry is a coverage/business choice, not a prerequisite or a standards solution.

## 1. What Muse actually demonstrates

| Question | Evidence and limit |
|---|---|
| In-chat connection | **D:** ask “Connect my Gmail.” Meta separately documents Settings → Connectors → Connect → Continue → provider login/authorization. It does not specify the in-chat card layout or its exact tap count. [M1] |
| Custom MCP | **I:** SealGate documents asking in chat, opening the returned sign-in link, signing in/approving, then a tool check and saved skill. It says Muse may ask setup questions. This is not a guaranteed one-tap flow. [M2] |
| Popup, sheet, system browser, account picker | **Unverified for Muse across surfaces.** Metric documents a browser approval page and “Approve Muse” in its phone app, including desktop-to-phone handoff. Neither this nor Meta's help proves a particular iOS/Android browser API or a universal account-picker sequence. [M3] |
| Count | Settings route has four documented navigation/authorization taps before provider-specific steps. Chat route has at least a sign-in-link activation in the integrator example, plus login/consent. Metric's one approval tap is one part of its flow, not the total. [M1–M3] |
| Expiry/revocation | **I:** Metric specifies 15-minute access tokens, rotating 30-day refresh tokens, and reconnect for certain changed grants. **D:** disconnect in Muse stops exchange; memories/history may remain. Muse's generic refresh scheduler, revoked-token card, retry rules and resume semantics are not publicly established by these sources. [M1, M3] |
| No OAuth | **D:** custom connectors can use APIs/CLIs; Meta's submission guide accepts API keys and supplied OAuth client credentials. **I:** AgentMail directs keys to a Secrets tab, followed by an egress approval. Browser login uses protected credential capture according to Meta's security design. Keys/passwords are not “nothing but taps” unless already available through secure autofill/custody. [M4–M6] |

**R: copy the interaction, not an imagined implementation.** A contextual protected card should identify the service, destination, account and use; open sign-in; show completion in the original thread; continue the saved task. Keep action permission distinct from OAuth access. Meta describes credentials outside agent-visible execution and separately enforced action approvals. [M5]
Metric's CIMD acceptance is evidence about **Metric's server**, not proof every Muse connection uses CIMD. Its static client fallback and Meta's request for integration credentials show why directory and unknown-service flows must be distinguished. [M3, M4]
SealGate's key-in-URL fallback is a vendor workaround, not a TinyAssets recommendation; never put bearer-bearing URLs in chat or reusable manifests. [M2]

## 2. Standards: what becomes generic, and what does not

| Standard/current evidence | Generic capability | Remaining condition |
|---|---|---|
| MCP **2026-07-28**, latest dated revision found [S1] | Common tool schemas/calls; current Streamable HTTP; no initialization handshake or session header in this revision | A service must expose MCP; retain explicit compatibility for older servers. A version-string change alone is insufficient. |
| RFC 9728 protected-resource metadata, April 2025 **OLD** [S2] | Resource tells client which authorization server governs it | Start from the actual endpoint, not a guessed login host. Does not find every service from its name. |
| RFC 8414, June 2018 **OLD**; OIDC Discovery 1.0 errata 2, December 2023 **OLD** [S3, S4] | Discover issuer, endpoints and capabilities | Metadata is not permission to register. OIDC identity/ID tokens do not themselves authorize API calls. |
| OAuth 2.1 draft **-16**, posted September 2, 2026; PKCE RFC 7636, September 2015 **OLD** [S5, S6] | Authorization code with S256; no client-specific implementation branch | OAuth 2.1 is still a draft. Client identity, supported scopes and acceptable token authentication remain necessary. |
| RFC 8707, February 2020 **OLD** [S7] | `resource` binds requested tokens to their intended API/MCP resource | It is audience restriction, not universal cross-service token reuse. |
| CIMD draft **-02**, July 6, 2026 [S8] | HTTPS client-ID document lets an unknown AS fetch client metadata without prior registration | AS must support/accept it; policy may still reject clients. Self-published metadata is not a trust certification. |
| RFC 7591 DCR, July 2015 **OLD** [S9] | Machine registration without user typing when open registration is accepted | Registration may require initial access credentials/software statements. MCP deprecated DCR in July 2026; retain for compatibility. [S10] |
| Device authorization RFC 8628, August 2019 **OLD** [S11] | Standard polling plus browser authorization on another surface; `verification_uri_complete` can avoid typing a code | Requires an accepted client and AS device-flow support; QR/click/consent may still require confirming the displayed code. Not the default for a phone with a browser. |
| Token exchange RFC 8693, January 2020 **OLD** [S12] | Delegation across a configured trust relationship | Does not turn any Google/Meta login into a LinkedIn token. Subject-token acceptance and policy must exist. |
| A2A Agent Cards, current v1.0.1 docs **U** [S13] | Discover agent interfaces, skills and advertised security schemes | Agent-to-agent interoperability, not client registration or arbitrary API access. Reuse the auth core after validating the card. |
| MCP enterprise-managed authorization, current extension **U** [S14] | Enterprise IdP/ID-JAG exchange can remove repeated user sign-ins | Requires organization configuration and trust at the resource AS; opt-in extension, not consumer zero-config. |
| MCP URL elicitation, introduced November 25, 2025 **OLD**, current July 2026 contract [S15] | Server sends an external login/secret-entry URL; client renders a contextual action | This authorizes the MCP server's **upstream** service, not the client's MCP transport. Form elicitation must not collect secrets. |
| WebMCP, updated October 7, 2026 [S16] | Structured browser-page tools can reuse that site's logged-in interaction | Proposed web standard; requires a browser/site implementation. Not remote MCP or a cloud API authentication replacement. |

**MCP discovery sequence [S17]:** parse a real `WWW-Authenticate` resource-metadata challenge; otherwise try endpoint-path protected metadata then root metadata. For an issuer with a path, try RFC 8414 path insertion, OIDC path insertion, then OIDC path appending. Validate issuer equality and resource identity; apply SSRF protections on every discovered target. Cache registration separately per issuer.
**Registration priority [S10]:** usable existing pre-registration → advertised CIMD → advertised DCR → explicit client configuration. CIMD is the preferred *new unknown-service* route; it does not invalidate existing registered clients. Publish one HTTPS metadata identity with exact callbacks; support public `none` and, as a generic follow-on, `private_key_jwt` rather than per-service secrets.
**Authorization [S18]:** preserve RFC 9207 issuer checks, least privilege, challenge-driven scope step-up and refresh-token rotation. Request `offline_access` only where applicable; refresh issuance is discretionary. A resource challenge's scopes need not be a subset of AS `scopes_supported`. Send `resource` in authorization and token requests. Invalid credentials and insufficient scope need different recovery states.
**Transport [S19]:** HTTP POST can return JSON or a request-scoped SSE response. The July protocol carries capabilities/version per request; cancellation closes the HTTP response stream. Do not confuse this SSE response encoding with the deprecated legacy HTTP+SSE transport. Streaming, input-required responses and protocol-specific continuation must survive the broker without replaying uncertain effects.

**Other connection types:** OpenAPI can describe operations and security fields, but cannot issue keys or create third-party OAuth registrations. API keys, bearer/custom headers, Basic, OAuth 1.0a, client-credentials/JWT grants, DPoP and mTLS are protocol families to handle as generic capabilities or report as unsupported. [S20] SSH/DB credentials and device permissions need their own standard transports/custody, usually behind an owner extension. “Supports OAuth” never means “supports every OAuth profile.” This is a coverage boundary, not an invitation to add vendor branches.

## 3. Product comparison: where their taps go

These are documented steps, not measured benchmarks. Exact account-picker, MFA and consent screens belong to the upstream provider.

| Product/source freshness | Documented shortest useful pattern | What it does when configuration is missing |
|---|---|---|
| ChatGPT apps/connectors, now described as plugins in current docs (**U**) [P1, P2] | Tool invocation can trigger OAuth authorization-code/PKCE UI. CIMD supports `none` or `private_key_jwt`; DCR remains selectable. Custom setup still includes URL/auth configuration, warning acknowledgment, creation and installation. | Predefined clients or DCR/CIMD configured by builder; no claim that an arbitrary unsupported provider becomes connectable. Current auth docs describe a transitional plural token-auth-method field (SEP-3149); negotiate capabilities rather than hard-code one method. |
| Claude custom connectors (help updated “this week”) [P3] | URL → Continue → detected auth → Continue → Add, with sign-in now or when needed. Published identity is recommended; automatic registration and own-client choices exist. | Static OAuth client and fixed request-header credentials are supported. Organization enablement and user connection are separate; remote traffic originates in Anthropic's cloud, even from Desktop. |
| Gemini custom apps (**U**) [P4] | Web Connected Apps → custom MCP URL → Next → sign-in instructions; connected apps then work on web/mobile. | Advanced credentials when DCR is unavailable. Current docs say creation is web-only and availability restricted. Do not repeat the older claim that consumer Gemini has no custom MCP. |
| Copilot Studio (updated May 28, 2026) / Microsoft 365 Copilot (**U**) [P5, P6] | Studio has dynamic discovery, dynamic registration with explicit endpoints, and manual OAuth modes. M365 plugins additionally support Entra SSO. | Registration, tenant/admin policy and manual client configuration still exist. These are different Copilot surfaces; plugin support does not prove consumer Copilot accepts any URL. |
| Cursor (**U**) / VS Code (CIMD release note November 2025 **OLD**, current guide **U**) [P7–P9] | Install link/catalog entry removes JSON authoring; authenticate when required. VS Code documents CIMD support; Cursor documents remote OAuth and static credentials. | Bring accepted client details if needed. Install/trust and sign-in are separate decisions; local stdio also needs runtime/package/credentials. VS Code's older API guide still says DCR-first: prefer its explicit CIMD release evidence, not that stale ordering. |
| Zapier (**U**) [P10] | Connect one MCP endpoint; choose/reuse connected apps; discover actions in chat. | Managed app integrations and downstream authorizations do the work. One broker login is not permission for every downstream account. |
| Composio / Pipedream (**U**) [P11, P12] | Managed OAuth clients, hosted connect links, per-user token storage/refresh; reuse connected accounts. | Custom OAuth clients/credential configuration for exceptions. They relocate service registration and adapters into the broker; they do not eliminate them. Useful UX evidence, not a proposed dependency. |

**LinkedIn boundary:** its official flow requires a registered application, allowed redirect and granted products/scopes; a fabricated client ID cannot work. The source is updated November 17, 2025 (**OLD**) and was rechecked today. [P13]
Our October 4 concern says missing registration is a **hypothesis**. Source inspection confirms our directory currently contains Google only and discovery can return `no_public_client`; that does not prove the founder's particular failure. Capture that request's resolved offer/reason and upstream error privately before closing the concern. Never silently turn a revoked OAuth connection into “paste a token.”
**Hosted registry tradeoff (R):** a trusted registry of issuer/client ID/callback/scopes/secret references keeps runtime code generic, but somebody still registers each service and maintains its approvals. Keep existing approved entries; prefer service-native MCP/CIMD for new coverage. If the founder declines registration and brokers, some APIs necessarily remain unsupported until the service supports an open client route, the owner supplies a client, or a browser workflow is usable.

## 4. Recommended ladder and honest tap budget

**Counting convention:** after the user has submitted a name/link/request; count deliberate click/tap actions, not typing the original request. Estimates assume an existing upstream account. `A` = provider account selection, `L` = login/MFA steps, `O` = OS/browser confirmation; each can add taps and sometimes typing. A connect receipt and automatic return/resume add zero taps. These are target budgets (**R**), not guarantees or competitor measurements.

| Order / route | TinyAssets taps | End-to-end minimum / ordinary case | Gate or fallback |
|---|---:|---|---|
| 0. Reuse an active, appropriately scoped account/grant | 0 | 0; +1 if the account is genuinely ambiguous | Never silently switch accounts or broaden scope. |
| 1. Resolve name/link to verified service endpoint or owner extension | 0 | +1 only for ambiguous service choice | Directory as editable data; official docs/search by the owner's powered agent. A website URL is not necessarily an MCP endpoint. |
| 2. Public/keyless remote MCP | 1 | **1** protected approve, already our live baseline | Reuse #4553; exact destination and requested use remain visible. |
| 3. Remote MCP OAuth, accepted existing client else CIMD else DCR | 1 | **2** for first grant with one provider Allow; ordinarily **2–3 + L + O** including account choice | Automatic discovery/registration adds zero taps. With retained provider consent/session, total can be 1; first-time consent cannot be promised away. |
| 4. OAuth requiring pre-registration | 1 once configured | Same **2–3 + L + O** afterward; initial setup has **no finite universal tap-only budget** | Use already-approved directory data or owner-supplied client; otherwise label client registration required. Never imply CIMD works on an AS that rejects it. |
| 5. Downstream login requested by an MCP tool | 1 per new URL handoff | Usually **2–3 + L + O**, possibly additional to row 3 | URL elicitation binds host, server and pending operation; upstream tokens stay at that server. |
| 6. Device flow, only when offered and useful | 1 to open complete verification URL | **2+ + L + O**; QR/code matching may add steps | Accepted client still required. Avoid forcing device-code transcription on the same phone. |
| 7. Standard API / key / CLI through an owner extension | 1 submit after secure entry | Key already in custody: **1** grant; key on clipboard: roughly **2–4** input/paste/submit gestures; key generation/login: unbounded | Render only necessary protected fields; infer endpoint/auth shape from trusted docs/data. A first API key is not generally tap-only. |
| 8. Stdio MCP | 1 for an already prepared revision | **1** if runtime, pinned package and grant are ready; plus OAuth/key steps otherwise | Cloud-executable server in owner's isolated environment can stay hostless. Laptop-only files/apps require an online companion; say so. No universal install-time tap count. |
| 9. Website/browser fallback | 1 takeover/connect entry | Variable login/passkey/MFA/CAPTCHA plus action consent | Owner-authenticated browser custody; WebMCP where available. No promise for blocked sites or unattended local devices. |
| Renewal | 0 on successful refresh | **0** ordinary expiry; **1** reconnect initiation + **A + L + O**, + provider consent if required | `invalid_grant`/revocation/missing refresh → one actionable reconnect card, retain task/account context; never loop or downgrade silently. |

Remove redundant “connection saved,” return-to-chat and “please continue” steps; let Connect both grant the displayed TinyAssets reach and open the external authorization. Reuse accepted registrations, browser sessions, secure autofill and valid refresh tokens. Keep provider consent/step-up, exact account selection when ambiguous, protected secret entry, and the owner's action-policy decisions. A remembered grant can remove a later prompt; it cannot authorize an undisclosed destination or changed action.
OAuth completion proves credential acquisition, not tool success: mark active only after a minimal safe protocol/tool check. Resume a suspended operation by its documented continuation contract; an unknown result after transmission is not an instruction to resend a write.

## 5. Mobile: one OAuth core, two small OS adapters

**D:** RFC 8252 (October 2017, **OLD**) requires external user-agents for native authorization; an embedded credential WebView is not the right OAuth surface. [N1]
**R:** keep the chat in Capacitor; open an issuer-visible system authentication surface from the protected Connect tap. On Android use Auth Tab where supported, Custom Tabs fallback, then system browser fallback. Auth Tab uses a result callback and can verify HTTPS callbacks with Digital Asset Links; its guide is dated January 31, 2025 (**OLD**). [N2]
On iOS use `ASWebAuthenticationSession`, with a retained session/presentation anchor and validated callback. Apple's web-authentication guide describes the system permission dialog and routing the response to the initiating session; count that potential extra OS tap. Shared browser state reduces repeat sign-in; ephemeral mode intentionally sacrifices that reuse. Apple docs are **U**. [N3]
Capacitor's Browser plugin documents `SFSafariViewController` on iOS; opening a page is not a full auth transaction or an `ASWebAuthenticationSession` implementation. Add one protocol-neutral native auth bridge, not one bridge per provider. [N4]
**Our callback design:** provider → fixed HTTPS TinyAssets callback → server exchanges/deposits/wakes → optional verified app/universal link carrying only an opaque completion reference. Let the server callback execute before a native Auth Tab intercepts the *return* URL. Keep PKCE/state, issuer, owner/session, action revision and expiry server-bound; no provider tokens or app bearer in URLs.
The system browser and WebView do not automatically share the TinyAssets owner session. Reuse `inline-connect-and-approve`'s browser-authenticated top-level hop; do not treat possession of an app bearer or launch URL as interactive approval. A missing browser owner proof can add a real first-use sign-in cost. Verify cancellation, app kill, lost parent window, account switch and background completion on both OSes. This OS-specific glue is unavoidable; it is service-neutral.

## 6. What our four changes get right, wrong or miss

Code evidence is the checkout above, not a fresh deployment assertion. Founder supplied #4553 as live; GitHub confirms merge `0ae2c9f7ab2400bbdbde1d219a8c2e41fd492b61` on October 7. [L1]

| Change | Right | Correction / missing against current evidence |
|---|---|---|
| `connect-anything-ladder` | Generic protocols, protected secret entry, resource binding, CIMD/DCR/static options; browser fallback separate | MCP lifecycle is superseded by `one-extension-unit` and remote MCP already landed in #4519/#4553. Do not create its older second attachment/storage model. Specify registration ordering, URL elicitation and latest/legacy protocol compatibility; stdio remains separate unfinished work. |
| `generic-oauth-connections` | Discovery/PKCE, issuer-response validation, atomic refresh rotation, optional registered-client data | `discovery.py` tries root resource metadata only, lacks CIMD and header challenge input, permits issuer trailing-slash equivalence, and `_covers` rejects scopes absent from AS metadata. `flow.py` and `tokens.py` omit MCP `resource`; DCR omits `application_type`. Add endpoint-path identity, issuer-keyed registration persistence and precise recoverable errors. Device/JWT grants and sender-constrained tokens are explicitly out of scope, not supported. |
| `inline-connect-and-approve` | Shared protected sheet, labelled accounts, owner proof and durable continuation; server-completed callback is specified | S1/S2 subsets are implemented; task 2.3 remains unchecked. Current `ConnectOAuth` in `app.html` still creates/stores the verifier in `sessionStorage`; don't claim parent-independent OAuth yet. Integrate URL elicitation and explain registration-needed versus reconnect. Mobile callback ownership is essential, not a later polish task. |
| `broker-streaming-contract` | Secret custody/scanning, backpressure, cancellation, operation identity, uncertain-result handling and owner fencing | `broker/server.py`, `aclient.py` and `scan.py` already exist (#4519/#4554); unchecked tasks are not proof it is unbuilt. Validate current coverage and sync after acceptance. A blanket OAuth 401 resend is not proof an arbitrary upstream write had no effect; constrain retry/reconcile by operation semantics. Preserve auth challenge headers through sanitization. |

Additional concrete gap: `tinyassets/mcp_remote.py:VERSIONS` contains only `2025-06-18` and `2025-03-26`, and the implementation initializes a session. Add a separate current protocol path; preserve safe legacy operation. Neither a successful keyless server nor optional directory Google proves unknown OAuth interoperability.
**Applies when touching:** connection discovery/token persistence, remote MCP/extension runtime, pending requests, browser custody, broker response headers/retries, or mobile login. Keep all model providers on read/write/edit/bash + `ta`; standards adapters are plumbing, service behavior lives in owner-editable extensions/skills/data. Build forward into per-owner isolation; do not add temporary duplicate authority stores.

## 7. Smallest build order / worktree landing packet

1. **Adopt: finish the existing unknown-remote-MCP route.** Fold this into `connect-anything-ladder`/`one-extension-unit`, not a new connector subsystem. First independent slice: spec the endpoint/issuer/resource and registration contract, implement path/challenge discovery + CIMD + resource propagation, then demonstrate an unlisted server. Include correct scope handling, DCR application type and accepted static-client reuse. No provider registration required for the test service.
2. **Adopt: complete the interruption once.** In `inline-connect-and-approve`, finish server-owned PKCE/callback completion, single reconnect/error card and durable wake, with Android/iOS system-auth adapters. Preserve secure manual-key entry as an explicit alternative. Original task resumes without a user “continue” message.
3. **Adapt: current protocol compatibility and URL elicitation.** Extend `mcp_remote.py` over the existing broker for July 2026 and older servers, including input-required continuation, JSON/SSE responses, challenge propagation and cancellation. This can proceed alongside slice 2 with disjoint files; serialize integration/merges. Do not wait for stdio/browser to prove remote OAuth.
4. **Adapt: name resolution and remaining protocols.** Teach the owner-editable connect skill to resolve official endpoints, consult directory data, offer ambiguity choices, and report unsupported registration honestly. Then device flow, generic client-auth methods and cloud stdio, each justified by a real connection. Build browser fallback in its existing lane. **Watch** A2A, enterprise ID-JAG and WebMCP; **defer** automatic federation and a broad broker catalog; **avoid** per-service branches and automatic public trace uploads.

Pickup: proposed next implementation branch `codex/mcp-oauth-discovery`, worktree `../wf-mcp-oauth-discovery`, from current main after reconciling active connection/isolation lanes. Research branch remains `research/zero-tap-connections`; its fold-back is a non-draft **docs PR to main**. No implementation or deployment is claimed here.
First-slice proposed write set: `tinyassets/connection_oauth/{discovery,flow,tokens}.py`, `tinyassets/auth/wellknown.py` (outbound client metadata route, separate from inbound AS metadata), `tests/test_generic_oauth_connections.py`, and `openspec/changes/connect-anything-ladder/{design.md,tasks.md,specs/connect-anything-ladder/spec.md}`. Recheck route/test ownership before claiming; separate runtime work in `tinyassets/mcp_remote.py` from card/mobile work in `tinyassets/onboarding/app.html` and `mobile/`.
Read dependencies: ADR-006/007/015 (reviewed), `one-extension-unit`, per-owner isolation cutover, `generic-oauth-connections`, `inline-connect-and-approve`, `broker-streaming-contract`, `saved-agent-connectors`, `browser-login-custody`, #4553 and the unresolved LinkedIn concern. Prior-provider context: the two October 4 notes and their founder correction; claim feed read, no additional connection-specific provider memory promoted; no new idea-feed item needed.
Verification before implementation commit/push: relevant spec validation, touched/heavy tests and Ruff; cross-family floor review for credential/authority changes; Linux oracle for process/filesystem/sandbox changes. Acceptance: unknown CIMD + DCR-only + existing static-client + registration-unavailable servers; expired/revoked tokens, account switch, lost callback parent and uncertain writes. Assert deployed SHA, public canary where applicable, one naive-user app-agent pass including mobile, then sync/archive the existing specs. Research-only checks are links/evidence, line budget and diff hygiene; no deployment needed.
Handoff is mirrored in `openspec/changes/connect-anything-ladder/research-handoff.md`; it does not mark implementation tasks complete. Open gaps: measured Muse UI/taps on each surface, actual LinkedIn failure cause, current native end-to-end traces, and production support for each newer extension. No repeatable workflow change was needed in the research skill.

## Sources (publication/update date; all accessed 2026-10-09)

- [M1] [Meta: how Muse works with Connectors](https://www.meta.com/help/artificial-intelligence/1687253048996149/) — updated “4 weeks ago” (approximately September 2026).
- [M2] [SealGate's Muse setup](https://sealgate.ai/docs/connect-clients/muse) — **U**, integrator evidence only.
- [M3] [Metric Muse connector contract](https://www.joinmetric.com/muse) — 2026-09-27.
- [M4] [Meta Muse connector guidelines](https://muse.ai/platform/docs) — **U**.
- [M5] [Meta: How We Built Safety Into Muse](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse) — 2026-09-08.
- [M6] [AgentMail: give Muse an email address](https://www.agentmail.to/blog/give-muse-email-address) — 2026-09-11, integrator evidence.
- [S1] [MCP July specification release](https://blog.modelcontextprotocol.io/posts/2026-07-28/) and [current versioning](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning) — revision 2026-07-28.
- [S2] [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html) — 2025-04 **OLD**.
- [S3] [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html) — 2018-06 **OLD**.
- [S4] [OIDC Discovery errata 2](https://openid.net/specs/openid-connect-discovery-1_0.html) — 2023-12-15 **OLD**.
- [S5] [OAuth 2.1 draft/status/history](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/) — -16 posted 2026-09-02; draft cover says September 3.
- [S6] [PKCE RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html) — 2015-09 **OLD**.
- [S7] [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html) — 2020-02 **OLD**.
- [S8] [CIMD draft -02](https://datatracker.ietf.org/doc/draft-ietf-oauth-client-id-metadata-document/) — 2026-07-06, draft, not RFC.
- [S9] [RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html) — 2015-07 **OLD**.
- [S10] [MCP client registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration) — 2026-07-28 revision.
- [S11] [Device authorization RFC 8628](https://www.rfc-editor.org/rfc/rfc8628.html) — 2019-08 **OLD**.
- [S12] [Token exchange RFC 8693](https://www.rfc-editor.org/rfc/rfc8693.html) — 2020-01 **OLD**.
- [S13] [A2A v1.0.1 specification](https://a2a-protocol.org/v1.0.1/specification/) — versioned, publication date **U**.
- [S14] [MCP enterprise-managed authorization](https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization) — **U**, opt-in extension.
- [S15] [MCP elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation) — 2026-07-28 revision; URL mode originated 2025-11-25 **OLD**.
- [S16] [Chrome WebMCP](https://developer.chrome.com/docs/ai/webmcp) — published 2026-05-18, updated 2026-10-07.
- [S17] [MCP authorization-server discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery) — 2026-07-28 revision.
- [S18] [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) — 2026-07-28 revision; its bibliography pins older OAuth/CIMD drafts, not today's latest drafts.
- [S19] [MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) — 2026-07-28 revision.
- [S20] Supporting protocol references: [OpenAPI 3.2.0](https://spec.openapis.org/oas/v3.2.0.html), 2025-09-19 **OLD** (versioned reference, not a latest-version claim); [OAuth 1.0 RFC 5849](https://www.rfc-editor.org/rfc/rfc5849.html), 2010-04 **OLD**; [JWT grants RFC 7523](https://www.rfc-editor.org/rfc/rfc7523.html), 2015-05 **OLD**; [DPoP RFC 9449](https://www.rfc-editor.org/rfc/rfc9449.html), 2023-09 **OLD**; [mTLS RFC 8705](https://www.rfc-editor.org/rfc/rfc8705.html), 2020-02 **OLD**.
- [P1] [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth) — **U**, current official docs.
- [P2] [OpenAI custom MCP setup](https://developers.openai.com/api/docs/mcp) — **U**.
- [P3] [Claude custom remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) — updated “this week,” October 2026.
- [P4] [Gemini custom apps](https://support.google.com/gemini/answer/17209137?hl=en-12) — **U**; this indexed English URL fetched successfully; the `hl=en` variant failed in the research tool.
- [P5] [Copilot Studio MCP setup](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent) — 2026-05-28.
- [P6] [Microsoft 365 plugin authentication](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api-plugin-authentication) — **U**.
- [P7] [Cursor MCP](https://prod.cursor.com/docs/mcp) — **U**.
- [P8] [VS Code 1.106 CIMD release notes](https://code.visualstudio.com/updates/v1_106) — October 2025 release, published November 2025 **OLD**.
- [P9] [VS Code current MCP guide](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) and [API guide with conflicting DCR-first wording](https://code.visualstudio.com/api/extension-guides/ai/mcp) — **U**.
- [P10] [Zapier MCP](https://zapier.com/mcp) — **U**; [quickstart](https://docs.zapier.com/mcp/quickstart) also describes manual tool setup; product/setup surfaces differ.
- [P11] [Composio managed/custom auth configuration](https://docs.composio.dev/docs/authentication/programmatic-auth-configs) — **U**.
- [P12] [Pipedream Connect managed auth](https://pipedream.com/connect) — **U**.
- [P13] [LinkedIn authorization-code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow) — 2025-11-17 **OLD**.
- [N1] [Native-app OAuth RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html) — 2017-10 **OLD**.
- [N2] [Android Auth Tab](https://developer.chrome.com/docs/android/custom-tabs/guide-auth-tab) — page says 2025-01-31 **OLD**.
- [N3] [Apple web authentication](https://developer.apple.com/documentation/authenticationservices/authenticating-a-user-through-a-web-service), [system consent](https://developer.apple.com/documentation/AuthenticationServices/ASWebAuthenticationSession?changes=latest_minor), and [ephemeral-session behavior](https://developer.apple.com/documentation/swiftui/environmentvalues/webauthenticationsession) — **U**; Apple full text was available through indexed official results; direct fetches often returned a JavaScript shell.
- [N4] [Capacitor Browser API](https://capacitorjs.com/docs/apis/browser) — **U**.
- [L1] [TinyAssets #4553](https://github.com/TinyAssets/TinyAssets/pull/4553) — merged 2026-10-07; live status supplied by founder, not reasserted by this research.

License/provenance: no external code vendored. IETF sources carry IETF Trust terms; product text remains its publishers' material. Recommendations paraphrase linked evidence; implementation reuse must check the chosen SDK/package's license and pinned version separately.
