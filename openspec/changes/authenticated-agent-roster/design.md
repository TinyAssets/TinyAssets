## Context
The addressable roster is loaded from agent files. It must not launch an external OAuth/API client as an in-app agent. API keys already have a last-used receipt; MCP lacks a durable owner projection. Observability logs are not a product database or an authority source.

## Goals / Non-Goals
Show authenticated external connections automatically, preserve installed-agent addressing, and expose honest evidence to custom screens. No extra grants, onboarding step, delivery promise, secret retention, fabricated attribution, or user conversation content.

## Decisions
- Add `external_agents` beside existing `agents` in the owner-only agents read and AppUI `list_agents`/`read_live`. Preserve existing addressable-agent consumers.
- Read API key metadata only after full key/grant admission. Unused, revoked, killed, other-owner and other-center keys are excluded. API name is explicitly owner supplied.
- Record MCP authentication observations in the existing outside-authority database. A verified session/family identifies a connection; when none is available an irreversible token fingerprint distinguishes otherwise unknown connections. Neither fingerprint nor session claim is authority. No bearer or request body is stored. Known first-party clients and canaries are excluded.
- Hold the receipt-store write lock before checking the permanent account-deletion tombstone. A still-valid OAuth token cannot recreate erased receipts; an in-flight write finishes before the deletion's satellite-store sweep. Classified external identities cannot suppress observation by supplying a browser Origin header.
- Use actual registered or client-reported names with a provenance field; missing names remain `Unknown MCP client`. A client-reported name is never verified vendor attribution. Existing first-party classification remains authoritative.
- An observation shows `previously_authenticated` and a timestamp, not online status. `messaging_ready` is false because this lane has no evidence of delivery capability. Outside grant revocation/current scopes are rechecked on each read; unclassified historical OAuth observations do not assert current access or revocation knowledge.
- Use the existing heartbeat for refresh, safe text rendering, and account/home epoch checks. Empty state is exactly “Tell your agents to connect to your TinyAssets MCP or API.”

## Risks / Trade-offs
Opaque token fallback can yield multiple connections after token rotation when the issuer supplies no session identifier; label unknown identity honestly rather than merge unrelated clients. Names are bounded and secret-shaped strings rejected. Observation-store failures do not break authentication, but emit a fixed warning. Receipt reads fail visibly rather than invent an empty roster.

## Migration Plan
Additive observation table, covered by owner erasure and SQLite-writer inventory. Release uses independent source review and required checks. Live credentials, grants and subscriptions are outside this change.

## Open Questions
None blocking local implementation. OAuth issuer-side revocations without local grant evidence cannot be inferred from an old successful authentication receipt.
