# Outside agents both ways - preserved research, 2026-10-05

# External agents ↔ TinyAssets: inbound control, outbound talk

Research + read-only audit, 2026-10-04. Repo evidence pinned to `origin/main` @ `aab7b2d1f7` (via `git show origin/main:<path>`; primary checkout untouched).
Founder requirement (2026-10-05): an owner-authorized outside agent (Muse, ChatGPT, Claude, OpenClaw, Hermes...) can message chosen TinyAssets agents and see/control the command center; TinyAssets agents can talk to the owner's outside agents when connected and authorized.

Evidence labels: **[DOC]** vendor docs, **[3P]** third-party integrator writing about its own integration, **[REPO]** our code/spec, **[INF]** inference.

Prior in-repo work this builds on: `docs/design-notes/2026-10-04-muse-connection-methods.md` (Muse connection catalogue; row 3 "Remote MCP custom connector... Missing" on the *outbound* side) and `docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md:171,199` (A2A messaging listed as a capability gap, `ta message`).

---

## PART A — How outside agents consume services (inbound to us)

**The convergent fact: every major personal-agent host now consumes a remote Streamable-HTTP MCP server with OAuth 2.1 + PKCE, registering itself via DCR or CIMD.** One MCP endpoint plus a standards-compliant authorization server covers Muse, ChatGPT, Claude and OpenClaw with no per-host code. TinyAssets already has this endpoint.

| Host | Transport | Client registration / auth | Callback | What the user sees | MCP features used | Limits |
|---|---|---|---|---|---|---|
| **Meta Muse** (custom connector) | Remote Streamable HTTP MCP URL, or raw REST API plus OpenAPI/docs [3P] | OAuth + PKCE with DCR (RFC 7591), RFC 8414 discovery [3P: imajin #2250, unsubject PR #78]; API key / bearer through a "Secrets tab" credential prompt; a PAT is a common fallback when OAuth is fiddly [3P] | `https://agent.meta.ai/api/hatch/oauth/callback` (also `https://muse.ai/connect/oauth-callback`, per our design note) | No settings form. The user asks in chat ("create a custom connector, URL..., auth OAuth"). Muse writes and tests an MCP client on its VM and saves it as a reusable skill on every surface (app, web, Mac, WhatsApp). The AS consent page follows. **Sentinel** gates actions out-of-band, with one-time/session/task/time-bounded/perpetual grants [DOC Meta security blog] | Tools (tool listing plus calls "on behalf of the human") [3P]. No evidence of resources, prompts, or elicitation use | Public HTTPS only (localhost only via the Tailscale connector); US and a few regions; custom connectors not reviewed by Meta and count against usage [3P/DOC]. One aggregator says consumer Muse has "no MCP." That conflicts with several integrators who connected MCP URLs, so treat it as stale |
| **ChatGPT** (Apps & Connectors / Apps SDK; Responses API remote MCP) | Remote MCP (Streamable HTTP) | OAuth 2.1 auth-code + PKCE S256 **mandatory**. CIMD is preferred (`client_id_metadata_document_supported: true`, `none` or `private_key_jwt`), then DCR, then a predefined client. **No API keys, no client-credentials, no customer mTLS** [DOC developers.openai.com/plugins/build/auth] | `https://chatgpt.com/connector_platform_oauth_redirect` when the AS returns RFC 9207 `iss` | Linking UI is triggered only when protected-resource metadata, per-tool `securitySchemes` and `_meta["mcp/www_authenticate"]` all line up. Scopes come from PRM `scopes_supported`. Re-auth for extra scopes uses `id_token_hint`. A profile tool (`_meta["openai/profile"]`) supports multi-account | Tools, Apps SDK UI widgets, `securitySchemes` per tool (`noauth`/`oauth2` + scopes) | The server must verify iss/aud/exp/scope on every call; enterprise domain restriction needs OIDC `email` with `email_verified` |
| **Claude** (claude.ai / Desktop / mobile / Cowork / Claude Code connectors) | Remote MCP | `oauth_dcr` and `oauth_cimd` on by default. CIMD is used only if the AS advertises `client_id_metadata_document_supported: true` **and** `none`; otherwise DCR. Also `static_headers` (beta), `none` plus lazy per-tool auth [DOC claude.com/docs/connectors/building/authentication] | `https://claude.ai/api/mcp/auth_callback`; Claude Code uses loopback on any port | Scopes from the `WWW-Authenticate` `scope` parameter, else PRM `scopes_supported`. `offline_access` is appended. Refresh happens proactively (5 min) and on 401. Public-client refresh rotation is required | Tools, prompts, resources (our server already serves the prompt catalog to Claude) | 10 s discovery/registration timeout, 30 s refresh timeout |
| **OpenClaw** | MCP client registry for remote HTTP/SSE servers, with `openclaw mcp login/logout` OAuth flow [DOC docs.openclaw.ai/cli/mcp]; also an "MCP Client Skill" for any HTTP MCP server | OAuth (login command) or token | n/a (CLI/gateway-local) | Owner-configured in gateway config; no hosted consent UI | Tools projected into agent runs as commands | Self-hosted; whatever the user's gateway can reach |
| **Hermes Agent** (Nous) | MCP client (connect external MCP servers); A2A client tools (`a2a_discover`, `a2a_call`, `a2a_orchestrate`) [DOC hermes-agent docs] | MCP per server config; A2A peers by URL plus bearer | n/a | Owner config | Tools; A2A tasks | A2A toolset off by default |
| **Google A2A v1.0** (LF; IBM ACP merged into it 2025-08-29) | JSON-RPC 2.0 / HTTP plus SSE streaming, push-notification webhooks | AgentCard at `/.well-known/agent-card.json` declares `securitySchemes` (OpenAPI-style: OAuth2 incl. client-credentials, bearer, API key, OIDC, mTLS) | — | Defined by the implementer | Tasks (`SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`), contextId threads, artifacts | Peer-to-peer between whole agents; each keeps its own tools and memory |
| **AGNTCY** (LF, Cisco-donated) | Infrastructure, not a wire protocol: directory, identity, SLIM messaging; OASF accepts A2A cards and MCP server descriptions | — | — | — | — | Discovery and identity layer only |

**MCP 2025-11-25 primitives that matter here:** CIMD as the recommended registration (DCR becomes MAY); incremental scope consent through a `WWW-Authenticate` step-up; **URL-mode elicitation** (send the user to an out-of-band URL for sensitive steps — exactly our approval sheet); experimental **Tasks** ("call now, fetch later" plus task notifications), which fit long agent turns.

---

## PART B — Can outside agents be consumed (outbound from us)?

| Agent | Callable endpoint another agent can use? | Realistic path for TinyAssets |
|---|---|---|
| **Meta Muse** | **No** documented inbound API, MCP server or A2A card for third parties to message a user's Muse [3P aiagentslibrary; Stacktree: "no information about A2A"]. The Meta Model API (`api.meta.ai/v1`, OpenAI/Anthropic-compatible) is a *model* endpoint, not the user's Muse agent | (1) **Reverse the direction:** Muse connects to *our* MCP and polls or reads an owner-to-Muse mailbox we expose as a tool (`read_graph target=conversation`/inbox). This is the only fully supported path today. (2) **Muse's own email address**, announced 2026-09-23 and not shipped, plus the AgentMail workaround. An outbound email effector would reach it [DOC Connect post, 3P]. (3) WhatsApp: Muse is chat-able in WhatsApp, but there is no bot-to-user API for us to drive. Not viable |
| **ChatGPT** (consumer) | **No** inbound agent endpoint. **Workspace Agents** (Business/Enterprise only) have `POST https://api.chatgpt.com/v1/workspace_agents/{agtch_...}/trigger`, auth by a workspace-agent access token minted in ChatGPT Admin. It is async 202 with no response body or webhook; results go to the agent's configured destination [DOC OpenAI cookbook]. The OpenAI Agents API (beta 2026-09-10) runs *our* agent on their harness; it does not reach the user's ChatGPT | Same reverse pattern (ChatGPT polls our MCP). For Business/Enterprise owners, a generic outbound HTTP connection to the trigger endpoint with a pasted bearer works on our existing `connect_http` shape. No per-vendor code |
| **Claude** (claude.ai) | No inbound agent endpoint | Reverse pattern only |
| **OpenClaw** | **Yes.** `openclaw mcp serve` exposes the user's gateway conversations as MCP tools (`conversations_list`, `messages_read`, `messages_send`, `events_poll/wait`, `permissions_list_open/respond`), over stdio or SSE/HTTP with `--token`/`--password` [DOC]. The gateway also exposes webhooks `POST /hooks/agent` (start an isolated agent turn, 202) and `POST /hooks/wake` | Today: an outbound bearer HTTP connection to `/hooks/agent`. After MCP attach: a remote MCP attachment to `openclaw mcp serve` (HTTP plus token). The gateway must be publicly reachable, or reached through the planned companion/private-network attach |
| **Hermes** | **Yes, A2A server.** AgentCard at `/.well-known/agent-card.json`, JSON-RPC `POST /` (`SendMessage`, streaming, tasks), bearer via `A2A_BEARER_TOKEN` / per-peer `A2A_PEER_TOKENS`, HMAC-signed push webhooks, loop guard `A2A_MAX_PINGPONG_TURNS` (default 5) [DOC]. Hermes can also run as an MCP server | Today: an outbound bearer HTTP connection that POSTs JSON-RPC `SendMessage` (non-streaming). Properly: a generic A2A client attachment (card discovery, then the declared security scheme) |
| Any A2A agent (LangGraph/LangSmith, ADK, CrewAI...) | AgentCard plus JSON-RPC | The generic A2A client, same as Hermes |

**Conclusion:** the big consumer hosts (Muse, ChatGPT, Claude) are **MCP clients only**. Talking *to* them means they hold the connection to us and we deliver into a mailbox they read, or later push. Self-hosted agents (OpenClaw, Hermes, A2A frameworks) expose callable MCP/A2A/webhook endpoints, and generic MCP-attach plus A2A-client connection types reach them with no per-agent code.

---

## PART C — TinyAssets today (cite `origin/main` @ `aab7b2d1f7`)

### C1. Inbound surface and auth: works for any MCP host now
- **Public endpoint:** Streamable-HTTP FastMCP server at `https://tinyassets.io/mcp`. `openspec/specs/live-mcp-connector-surface/spec.md:9` (Remote Streamable-HTTP MCP Endpoint), prompt catalog `control_station`/`meet_universe`/... at `:13-18`. Canonical handles: `tinyassets/universe_server.py:654` read_graph, `:944` write_graph, `:1833` run_graph, `:2012` read_page, `:2082` write_page, `:2959` converse; `tinyassets/mcp_server.py:123` get_status.
- **OAuth:** WorkOS AuthKit is the Authorization Server and we are a pure Resource Server (`tinyassets/auth/workos_provider.py:1-13`, `:265-271`: DCR/CIMD is AuthKit's job). `docs/reference/workos-authkit-integration.md:79,126` says AuthKit-for-MCP defaults to **CIMD with DCR kept on**, which matches Claude's, ChatGPT's and Muse's selection logic. Every handle requires a bearer, and a 401 carries the `WWW-Authenticate` resource-metadata challenge plus `_meta["mcp/www_authenticate"]` (spec `:403-424`). Tools advertise OAuth-only `securitySchemes` (`openid profile email offline_access`) (spec `:426-440`). Identity is stable across hosts: the same account through ChatGPT, Claude or a local agent resolves to one principal (spec `:442-457`).
- **So Muse, ChatGPT, Claude and OpenClaw can likely connect today.** The live-proven hosts are Claude and ChatGPT (memory/spec). **Muse is unverified.** Risk: Muse's DCR redirect `https://agent.meta.ai/api/hatch/oauth/callback` must be accepted by AuthKit's DCR redirect policy, and other servers had to allow-list it explicitly (unsubject PR #78). One live Muse connect is the cheapest proof.

### C2. Can an outside MCP client message a SPECIFIC agent? Yes, owner-only
- `converse(..., agent_id="")` at `universe_server.py:2959-3005`: `agent_id` is `"main"` (default) or one of the owner's agent ids, and each has its own thread (`:3003-3004`). The handler is fail-closed to the authenticated founder (`:3018-3023`, "public 'talk to a stranger's universe' is a later, separately-gated slice"). Spec: `converse` requires write/admin on the target universe (`:409-410`).
- Caveat: `openspec/changes/addressed-agent-control-provenance/proposal.md` (2026-10-03) shows several controls (rules/review, pending requests, journal, Stop) **still select `main`** even when another agent is addressed, so per-agent policy is not yet honoured end to end.

### C3. Can it read the command center? Mostly
Public `read_graph` targets (`tinyassets/api/graph_reads.py:349-384`): `status, graphs, goals, runs/run, branch, automations, connections, pending_requests, access, conversation, agents/agent, agent_bindings, app_ui, command_center_files/file, command_center_preview/packages/updates, run_file, receivers, delivery...`. So agents, conversations, runs, automations, files, screens (`app_ui`) and the access readback are readable.
**Missing:** a connector equivalent of the screen bridge's `readLive()` (per-agent working/idle plus live steps), which exists only inside the sandboxed screen bridge (`tinyassets/engine_mcp_server.py:2146-2161`). `read_graph` has no `live` target, and there is no server-to-client notification stream for an outside agent.

### C4. Can it control it? Yes, fully, and that is the problem
`write_graph` (branches, command_center, automation, connection, webhook, receivers...) and `run_graph` are open to any authenticated owner token. **Authorization is all-or-nothing per account:** `workos_provider.py:30-43` grants every authenticated token `("read","write","costly","submit_request","list")`, and `:230-234` adds only RBAC `permissions`. The boundary is the per-universe ownership ACL. Consequences:
- **No per-client scopes.** A Muse connector token is identical in power to the founder's own Claude connector. The advertised scopes are OIDC only (spec `:426-430`).
- **No per-agent scoping** of what an outside client may address or modify.
- **No client identity in the principal.** `Identity.metadata` records `iss`, `org_id`, `role`, `permissions` and email, but not `client_id`/`azp` (`workos_provider.py:236-249`). Audit and provenance cannot say "this was Muse." The legacy `oauth_clients` table (`tinyassets/auth/provider.py:896-1018`) belongs to the non-prod dev OAuthProvider.
- **No owner-facing "connected apps" list or per-client revocation** found in our code. Revocation would have to happen in WorkOS (session/consent revoke), and it is unverified whether AuthKit exposes per-client grant revocation for MCP clients.
- **Approval is already shaped right but unbuilt.** `openspec/changes/inline-connect-and-approve/proposal.md:9`: "an owner's bearer-holding chatbot cannot approve". Approvals require a protected interactive owner session plus single-use bound tokens. That rule is exactly what keeps a connected Muse from self-approving. Proposal-only, ten tasks open.
- Related unbuilt authority work: `agent-access-controls` (owner readback `read_graph target=access`, which is in the target list, plus `source_channel revoke` and request withdraw; last touched 2026-09-25).

### C5. Outbound: can a TinyAssets agent talk to an outside agent today?
- **Generic HTTPS, partially.** `write_graph target=connection operation=connect_http` (`tinyassets/api/http_connection.py:1-44`): owner-deposited **bearer-only** secret, endpoint allow-list, SSRF-hardened broker, used via the `authenticated_external_call` effector with owner effector consent. It is gated by `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` (`docs/reference/environment-variables.md:193`, default `off`; allowed in `.github/workflows/apply-daemon-env.yml:82`; prod value not verified here). That is enough to POST to OpenClaw `/hooks/agent`, Hermes A2A `SendMessage` (non-streaming), or a ChatGPT workspace-agent trigger, but each one is hand-built per call, with no discovery, streaming or reply threading.
- **Inbound webhooks** exist for owner branches (`tinyassets/api/webhook_ops.py:1-4`, `/hooks/<token>`). An outside agent that cannot do MCP (or an A2A push notification) can wake a run this way.
- **MCP attach: not built.** `openspec/changes/connect-anything-ladder` (tasks 1.1-1.10 all open) plans HTTP and jailed-stdio MCP attachments with PRM/8414 discovery, PKCE, DCR, our own hosted CIMD document and a static fallback (`design.md:73`). It **depends on `broker-streaming-contract`** (`design.md:37,49`: "until that broker is available, report streaming MCP as unavailable"), and the broker **refuses to start until `per-role-uid-split`** lands (`per-role-uid-split/proposal.md`: `BrokerUidSplitRequired`; all roles run as uid 1001). The chain is uid split, then broker, then MCP attach.
- **A2A: nothing.** No A2A client, server or AgentCard anywhere in `openspec/` or `tinyassets/` (grep). It appears only as a gap in design notes.

---

## PART D — Gaps and general-shape plan

Invariants applied: cross-user isolation is the only platform floor. Everything else is owner-granted, owner-editable, revocable, and runs through the approval sheet. No per-agent or per-vendor code: Muse, ChatGPT, Claude, OpenClaw and Hermes are all "an MCP client" inbound and "an MCP server / A2A peer / HTTP endpoint" outbound.

### Gap list
| # | Gap | Severity |
|---|---|---|
| G1 | Every OAuth client token holds the owner's full power. No per-client grant, no "connected agents" list, no per-client revoke | **Blocks safe delegation**: "authorize my Muse" today means "give Muse everything" |
| G2 | No client identity on the principal, so no audit or provenance of which outside agent acted | Blocks the audit and revoke UI |
| G3 | The approval sheet (`inline-connect-and-approve`) is unbuilt. Sensitive actions from outside agents have no out-of-band owner gate | Blocks safe "control" |
| G4 | Addressed-agent controls still select `main` (`addressed-agent-control-provenance`) | Per-agent scoping is meaningless until fixed |
| G5 | No `live` read and no push/notification channel to outside agents; no owner-to-outside-agent mailbox | Blocks "see" (live) and "talk back to Muse/ChatGPT" |
| G6 | Muse connect never proven live (DCR callback allow-list) | Cheap proof missing |
| G7 | Outbound MCP attach is blocked on uid split, then the broker | Blocks talking to OpenClaw/Hermes MCP servers |
| G8 | No A2A client or server | Blocks Hermes/LangGraph/ADK peers and an "agent card" for us |

### Build plan (general shape)

**1. Prove Muse connects now (no code; G6).** Point a Muse custom connector at `https://tinyassets.io/mcp` with OAuth. If AuthKit rejects the `agent.meta.ai` redirect under DCR, that is AuthKit configuration (redirect policy / CIMD), not code. Record it as a canary plus a `ui-test`-style rendered proof. *No proposal.*

**2. Connected-agent grants: per-client delegation (G1, G2). Needs an openspec proposal (auth/scopes, storage, public surface).**
- Capture `client_id` (CIMD URL or DCR id) plus `client_name` from the AuthKit token on every request, put it on `Identity`, and stamp it on turn/run/activity provenance.
- Store an owner-owned **client grant**: `(owner, universe, client_id) -> {agents: [ids|*], can: read | message | control | costly, expiry}`. On first contact the default for a *new* client is the owner's editable starter default (suggested: read plus message `main`; control and costly ask through the approval sheet). The owner's original primary connector keeps full rights, so nothing regresses (clean cutover per memory).
- Enforce it at the existing doors: `converse(agent_id)` checks the agent is in the grant, `write_graph`/`run_graph` check `control`/`costly`, and reads filter.
- Keep OAuth scopes coarse. Optionally advertise `tinyassets.read`/`tinyassets.control` in PRM `scopes_supported` and step-up via `WWW-Authenticate scope=` (MCP incremental consent), but **the authority lives in the grant row, not the token** (matches `workos_provider.py:30-43` reasoning and Muse's "finer than OAuth scopes" Sentinel model).
- Owner surface: `read_graph target=access` gains a `clients` section, and `write_graph target=client_grant` gains `set`/`revoke`. A revoke fences the next call (the grant check fails closed) regardless of token lifetime; optionally also revoke the WorkOS session.

**3. Approval sheet as the control gate for outside agents (G3).** Already specced: land `inline-connect-and-approve` ("bearer-holding chatbot cannot approve"). For outside clients, a sensitive write returns a pending bound action. The owner approves in the app/push, and MCP **URL-mode elicitation** can carry the approval link to hosts that support it. *Existing proposal; extend its `live-mcp-connector-surface` delta to name client provenance.*

**4. Finish addressed-agent provenance (G4).** Existing proposal `addressed-agent-control-provenance`. Prerequisite for per-agent grants meaning anything.

**5. See it live plus a two-way mailbox for MCP-only hosts (G5). Needs a proposal (public MCP surface).**
- `read_graph target=live`: connector parity with the screen bridge's `readLive()`. No new handle.
- An **outbox per connected client**: our agents can address "the owner's Muse" as a destination, and items queue for that client. The client reads them with `read_graph target=conversation`/`inbox` (poll), and a `control_station` prompt line teaches hosts to check it. Later: MCP Tasks/notifications where hosts support them. This is the only supported way to "talk to" Muse, ChatGPT or Claude, since none expose an inbound endpoint.

**6. Outbound MCP attach (G7).** Existing chain: `per-role-uid-split`, then `broker-streaming-contract`, then `connect-anything-ladder` 1.x. It reaches `openclaw mcp serve`, Hermes-as-MCP and any MCP server. *Proposals exist; unblock by landing the uid split.*

**7. A2A, both directions (G8). Needs a proposal (public API surface, auth, storage).**
- *Client:* an `a2a` connection type. Fetch `/.well-known/agent-card.json`, honour its `securitySchemes` (bearer/API key via the secret-entry card, OAuth via generic-oauth-connections), then `SendMessage`/`SendStreamingMessage`/`GetTask` through the broker, with contextId mapped to a thread. Reuse the HTTP connection's SSRF and egress policy. Interim before the broker: non-streaming JSON-RPC over the existing `connect_http` bearer channel is acceptable as a stopgap (it is plain request/response).
- *Server:* an AgentCard per command center (or per owner-published agent), backed by the same `converse(agent_id)` door and the same client grants as MCP. Cross-user isolation stays the floor: only owner-granted callers. Loop guard is a ping-pong turn cap like Hermes'.
- Lower priority than 1-6: the consumer hosts don't speak A2A; Hermes/LangGraph/ADK do.

### Proposal requirements summary
| Piece | Openspec? | Why |
|---|---|---|
| 1 Muse live proof | No | Config plus verification |
| 2 Client grants / client identity / revoke | **Yes (new)** | Auth/permissions, storage, public surface |
| 3 Approval sheet | Exists (`inline-connect-and-approve`) | Extend the delta for client provenance |
| 4 Addressed-agent provenance | Exists | — |
| 5 `live` read plus client outbox | **Yes (new or fold into 2)** | Public MCP surface plus storage |
| 6 MCP attach | Exists (`connect-anything-ladder`, blocked) | — |
| 7 A2A client/server | **Yes (new)** | Public API surface, auth, storage |

Recommended order: **1, then 2, then 4, then 3, then 5, then 6, then 7**. Item 1 is free. Item 2 is the keystone that makes "authorize Muse" safe. Item 4 plus item 3 make "control" and per-agent scoping honest. Item 5 completes see and talk-back for MCP-only hosts. Items 6 and 7 are the outbound reach, and 6 waits on infrastructure already in flight.

---

## Sources
- Meta Muse: [imajin-ai #2250 (Muse as first foreign agent)](https://github.com/ima-jin/imajin-ai/issues/2250) · [unsubject/2nd-brain PR #78 (Muse OAuth/DCR callback)](https://github.com/unsubject/2nd-brain/pull/78) · [Stacktree: Muse connector platform](https://stacktr.ee/blog/muse-connector-platform) · [Sprites: Muse connectors](https://www.sprites.ai/muse/connectors) · [AI Agents Library: Does Muse support MCP? (conflicting)](https://www.aiagentslibrary.com/blog/meta-muse-mcp/) · [Meta Model API (layer3labs)](https://www.layer3labs.io/guides/meta-model-api-guide) · in-repo `docs/design-notes/2026-10-04-muse-connection-methods.md` (Meta security blog, Connect 2026, SealGate, AgentMail citations)
- ChatGPT: [OpenAI plugins/apps authentication](https://developers.openai.com/plugins/build/auth) · [Workspace agent API trigger](https://developers.openai.com/cookbook/examples/chatgpt/workspace_agents/workspace-agents-api-trigger) · [OpenAI Agents API overview](https://developers.openai.com/api/docs/guides/agents-api/overview)
- Claude: [Claude connectors authentication](https://www.claude.com/docs/connectors/building/authentication) · [Building custom connectors via remote MCP](https://support.claude.com/en/articles/11503834)
- OpenClaw: [openclaw mcp CLI](https://docs.openclaw.ai/cli/mcp) · [Config: MCP, skills, plugins](https://docs.openclaw.ai/gateway/config-extensions) · [Hookdeck OpenClaw webhooks](https://hookdeck.com/webhooks/skills/openclaw-webhooks)
- Hermes: [Hermes A2A docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/a2a) · [hermes-agent repo](https://github.com/nousresearch/hermes-agent)
- Protocols: [MCP 2025-11-25 elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation) · [WorkOS on MCP 2025-11-25](https://workos.com/blog/mcp-2025-11-25-spec-update) · [A2A agent cards](https://specification.website/spec/agent-readiness/a2a-agent-cards.md) · [Tyk A2A spec overview](https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/) · [Agent protocol stack 2026 (Zuplo)](https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026.md) · [Protocol comparison incl. ACP→A2A merge, AGNTCY (zylos)](https://zylos.ai/research/2026-03-05-multi-agent-communication-protocols-comparison) · [AGNTCY research note](https://rywalker.com/research/agntcy)
