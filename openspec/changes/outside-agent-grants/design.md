## Context

This is a proposed authority/storage/public-surface contract, not shipped behavior. The [research](../../../docs/design-notes/2026-10-05-outside-agents-both-ways.md), preserved in full with [DOC]/[3P]/[REPO]/[INF] labels and Sources, pins its repo audit to `aab7b2d1f7`. C1-C4 locate the existing MCP/AuthKit surface and missing client authority; D2 proposes the keystone. Its external compatibility claims remain research evidence, not live acceptance. The authoritative 2026-10-05 founder instruction in proposal.md fixes the scope and first-connect default.

Existing owners of adjacent behavior:

| Contract | Reuse / extension |
|---|---|
| `openspec/specs/live-mcp-connector-surface/spec.md` | Same endpoint, handles, OAuth challenge and stable account/home across hosts; add client constraints |
| `tinyassets/auth/workos_provider.py`, `docs/reference/workos-authkit-integration.md` | WorkOS AuthKit validates identity and handles CIMD/DCR; resource server resolves client attribution |
| [addressed-agent-control-provenance](../addressed-agent-control-provenance/design.md) | Reuse validated addressed-agent snapshot and authoritative turn/run lineage; append outside-client origin |
| [agent-access-controls](../agent-access-controls/design.md) | Extend `read_graph target=access` with clients; keep existing channel consent/revoke/request withdrawal semantics |
| [inline-connect-and-approve](../inline-connect-and-approve/design.md) | Reuse protected sheet, bound actions, owner rules, single-use interactive approval, continuation and revocation checks |

## Goals / Non-Goals

Allow the owner to delegate read/message/control/costly actions to any OAuth client, inspect attribution, edit grants and revoke immediately. Cross-user isolation is the only immutable platform behavioral invariant: an outside client acts strictly as the authorizing owner within that owner's editable grants. No brand-specific adapter, platform model, second public endpoint or replacement approval system.

Talk-back (`read_graph target=live` and per-client outbox), A2A client/server, and outbound MCP attachment are separate later changes, as listed in proposal.md. This change does not claim to complete the founder's entire two-way vision.

## Decisions

### 1. Bind client identity separately from account identity

Every authenticated public MCP request binds a principal containing the existing owner/home plus an `outside_client` value: verified issuer, exact OAuth client id (CIMD URL or DCR id), registration kind, metadata name/logo/redirect host, and metadata availability/source. Resolve identity from validated AuthKit claims or an authenticated AuthKit lookup bound to this token; never infer it from User-Agent, an MCP payload, OAuth audience, an arbitrary header or a claimed brand. Do not assume `client_id` or `azp` is present until verified against live AuthKit. Contradictory/missing client identity refuses before data or effects and directs reconnect; no fallback to unrestricted owner authority.

Issuer + client id identify the app registration; owner + universe + that pair identify its grant. The same owner on two clients still has one account/home, with independent grants. CIMD/DCR metadata is display-only, sourced from the registered metadata with safe rendering and existing URL-fetch protections; changes to a name/logo/redirect do not mint identity or authority. Missing optional metadata is explicitly unavailable; display the exact id, never fabricate a name. Redirect host comes from registered redirect URIs, not a caller-provided redirect argument. Multiple hosts remain a displayed set.

Here owner means the authorizing user; existing universe ACLs remain independently required. Connected apps and the client audit are readable only by that authorizing user, never by another user merely because they share universe access.

A bearer transport is always automated authority, including a founder's usual chatbot. The protected owner session remains the existing separate owner door. This avoids the research D2 suggestion of silently grandfathering an original primary connector with full rights: no verified evidence identifies such a privileged client, and the user mandates read + message to main as the first-connect default.

### 2. Store current owner grants; tokens are not the grant store

An owner-scoped record keyed by `(owner_user_id, universe_id, issuer, client_id)` holds explicit agent ids or owner-selected `*`, a set of levels, optional expiry, active/revoked status, monotonically increasing revision/authorization generation, timestamps and last-used. A unique key makes concurrent first calls create one default (`agents=[main]`, `levels=[read,message]`). A revoked tombstone is retained; presenting an old token cannot recreate defaults.

Grant resolution follows authenticated home resolution. For a new user, verify client identity and the owner/client revocation fence first; the existing first `converse` home-creation path creates the main-only read/message grant in the same admission step, before delivering the message. This narrowly permits the existing private-home bootstrap, not arbitrary control. An early no-home status response requires verified active client identity and its revocation fence, returns only the caller's no-home state, exposes no universe content and does not provision. An existing home never uses this bootstrap to reset a grant or bypass denial.

Levels are independently editable capabilities, not cumulative numeric ranks. `costly` adds authority for spend/publish/external posts to the required ordinary operation level; it does not itself imply control, access to every agent, consent or approval. Agent ids are resolved through the existing addressed-agent authority, not display names. `*` explicitly includes future owned agents; a fixed list does not. Owner edits are revision-checked and atomic; stale edits return a conflict and current readback. Widening, reconnection and revocation use the protected owner app service. A bearer may request a change through the approval sheet but cannot apply it, even with `control` or `costly`.

The OAuth scopes and advertised OIDC security schemes remain unchanged. Per-action and per-agent authority is read from the current grant, so narrower rights and revoke do not wait for token refresh. Full-account OAuth scopes and UI-only filtering were rejected because neither enforces owner edits at dispatch.

### 3. Enforce the resolved operation and all affected resources

One daemon-side admission path checks authenticated owner/home and existing ACLs, active client generation, current grant, resolved resource/agent scope and operation levels before returning data or committing work. All public handle paths use it, including aliases, bulk operations and embedded app/page bridges. Transport discovery authenticates and checks active client identity; it grants no content authority.

| Existing handle | Required client grant |
|---|---|
| `get_status`, `read_graph`, `read_page` | `read`; return only resources within allowed agent scope |
| `converse(agent_id)` | `message` for the resolved agent, defaulting an omitted selector to `main`; its reply belongs to that admitted message |
| `write_graph`, `write_page` | `control` for all affected agents/resources; add `costly` for spend, publication or external posts |
| `run_graph` | `control` for its resolved execution scope; add `costly` for chargeable/external effects at dispatch |

An agent-scoped read filters mixed collections before serialization; explicit out-of-grant targets refuse. Shared command-center resources affecting or exposing multiple agents require coverage of every affected agent; no implicit association with main. Account-wide control requires explicit `agents=*` and `control`; sensitive effects additionally require `costly`. Owner-app Connected apps remains available through the protected owner door. The connector's `target=access` projection reports the caller's own effective client grant and only otherwise authorized access data, without other clients' private details. Global summaries, page content, snippets, search results and status conversation inclusions cannot leak excluded agents. An unresolved scope refuses clearly instead of becoming account-wide.

Sending a message does not delegate the resident agent's full authority. Persist the outside-client origin on turns, runs, nested work, scheduled work and pending actions using the existing provenance carriers. Recheck current grant at each downstream tool/effect admission and on resume. A main-only client cannot prompt main to read another agent, widen its own grant, spend or publish. A change of execution agent requires that agent to be authorized; scope never grows through delegation. Owner-authorized ordinary inference needed to answer an admitted message uses existing model/consent rules; `message` is not general authority to launch paid jobs or change budgets. Additional spend actions use `costly` and the existing approval policy.

For an authenticated owner-scoped denial return an MCP error naming `client_grant_missing`, required level and safe target/agent scope, with direction to Connected apps. For example: `Missing control grant for agent researcher; ask the owner to edit this app in Connected apps.` Resolve cross-user authorization first and retain uniform not-found/auth refusal rather than revealing a foreign agent's existence. No side effect on denial. Unknown effect classification follows the owner's existing editable policy in inline-connect-and-approve; it does not silently receive control/costly authority.

### 4. Connected apps, audit and immediate revoke

The protected app lists each connected client with exact id, name/logo/redirect hosts (or unavailable), allowed agents/levels/expiry, status and last authenticated use. Owners can edit or revoke a single client without changing another client or owner. Last-used records authenticated attempts, including grant denials, with their outcome; unauthenticated spoofed requests do not advance it. Reuse `target=access` for the constrained connector readback and existing app owner service for full inventory/edit/revoke; do not introduce a top-level tool or bearer grant-management bypass.

Record owner, universe, verified client identity/metadata snapshot, resolved addressed agent/resource, handle/operation, request/turn/run reference, grant revision, timestamp and outcome (allowed, denied, pending, executed, revoked) in durable owner-readable audit events. Grant edits/revoke record the interactive owner actor. No raw bearer, refresh token, credential, prompt or full response is copied into the audit. Preserve client origin in the existing turn/run/activity records, not a competing execution identity.

Revoke atomically marks the grant inactive, increments its authorization generation and invalidates all access/refresh-token use for that owner/client at TinyAssets, across universes sharing that owner/client authorization. Keep per-universe grant edits distinct from Connected apps' app-wide revoke. Maintain a durable owner/client revocation fence consulted on every request and effect dispatch across daemon workers; cached JWT validity, open MCP sessions and refresh cannot bypass it. AuthKit consent/session/token revocation should also run where supported, but local token invalidation does not depend on that API succeeding. Never report success before the local fence is durable and effective across workers.

A reconnect requires fresh owner-interactive authorization bound to a new generation; all pre-revoke credentials stay invalid. Implementation must prove how AuthKit exposes issuance/session identity to distinguish generations, or retain the revoked fence and report reconnect unavailable. Merely clearing the tombstone on a new-looking token is forbidden. Revocation fences queued/resumed work and pending approvals before any new admission. Serialize revoke and effect admission: an irreversible effect already committed before the revoke boundary cannot be undone; no effect admitted after it succeeds. Preserve the audit and give honest in-flight receipts.

### 5. Sensitive actions use the existing approval sheet

Use inline-connect-and-approve's existing protected bound action and owner-editable standing rules. Add verified outside client id/metadata and grant generation/revision to its provenance and owner preview alongside addressed agent and normalized action. A required missing level returns a clear grant refusal; approval of an effect alone does not widen a client grant. The owner can edit the grant separately and retry. Once the grant admits the request, sensitive actions enter the existing approval flow (including its explicitly owner-authorized standing decisions), without inventing a second approval policy here.

Bearer clients can request/read pending actions and receive a protected approval link, but can never approve, retry with dispatch authority, mint owner proof or use rule/grant-write aliases to self-approve. Owner approval rechecks the live grant and provenance before dispatch; stale/revoked requests do not execute. URL-mode elicitation may convey the existing link when supported; a normal link remains usable without assuming Muse implements elicitation. Custom-agent control depends on addressed-agent-control-provenance; sensitive dispatch depends on inline-connect-and-approve. Until those paths are available, return an honest unavailable/refusal with no bypass.

## Risks / Trade-offs

- AuthKit client claims, metadata lookup and per-client token/session revocation are unverified (research C4) → prove the binding and reconnect generation before implementation acceptance; retain a local durable invalidation fence.
- Shared command-center content can span agents → scope all contributors before returning data; avoid treating a page or status summary as globally readable under main-only grants.
- Queued work could launder an outside client's authority → persist client origin beside addressed-agent provenance and recheck at dispatch, not only at ingress.
- Existing clients lose implicit full rights → visible cutover with explicit owner widening; no brand exception or undocumented primary-client bypass.
- Muse's callback is reported by third-party integration evidence, not live TinyAssets proof → founder's rendered OAuth connection is mandatory acceptance evidence, with callback/registration result and no secrets recorded.

## Migration Plan

Future implementation first reconciles the adjacent changes and verifies AuthKit identity/generation contracts. Add durable grants, revocation fences and provenance, then activate uniform admission and Connected apps together. Existing clients receive the same main-only read/message grant at first verified use unless the owner explicitly configured wider rights; legacy credentials without verified client identity must reconnect. Do not infer grants from past powerful calls. Existing unattributed work cannot resume as an unrestricted outside client: hold for owner reconciliation. Preserve unknown historical audit identity as unknown.

Rollback must retain grant/revocation enforcement or suspend outside-client dispatch; rolling back to implicit full-account rights is not acceptable. Back up existing state and retain additive grant/audit records. No migration or deployment occurs in this docs-only delivery.

Acceptance requires focused auth/grant/revoke/provenance tests, affected heavy tests and ruff, one floor/correctness cross-family implementation review, strict OpenSpec validation, `python scripts/mcp_public_canary.py --assert-handles`, deployed SHA assertion, and a real rendered founder Muse pass. Sync/archive only after implementation and that proof; proposed requirements do not become as-built truth now.

## Open Questions

Verify the exact trusted AuthKit client claim/lookup, registered metadata access, token/session issuance binding and provider revocation API; these are implementation evidence tasks, not permission to guess identity. Verify live CIMD/DCR callback acceptance for `https://agent.meta.ai/api/hatch/oauth/callback`. If unavailable, record the precise AuthKit configuration blocker in a YAML-front-matter concern or existing host-action channel; do not invent a Muse-specific auth path.
