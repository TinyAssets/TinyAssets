## Why

Programmatic agents without MCP/OAuth clients cannot use TinyAssets. The founder requests a public REST transport with revocable, scoped owner-issued API keys (2026-10-10).

## What Changes

- Serve versioned reads and message/run actions at `https://tinyassets.io/api/v1`, calling the existing MCP implementation and response projection.
- Add named, hashed, owner-bound API keys issued once through the protected owner's Connected apps / API keys view. Enforce agent and independent read/message/control/costly levels, immediate revocation and per-key limits.
- Carry key authority through the existing outside-client saved-work machinery. Keys cannot approve sensitive actions or manage credentials/grants.
- Publish OpenAPI and allowlist REST at the canonical Worker. MCP and its OAuth remain unchanged; no webhooks or client-credentials OAuth.

## Capabilities

### New Capabilities
- `rest-api-keys`: public REST parity, protected key lifecycle and scoped authority.

### Modified Capabilities
None. Reuses outside-agent-grants (folded into one-extension-unit) and inline-connect-and-approve; does not widen MCP.

## Impact

Owner: Codex. Branch/worktree: `feat/rest-api-keys` / `wf-restapi`. One PR implementing programmatic access. Affects transport mounting, protected app UI, platform authority storage and Cloudflare routing. Acceptance covers lifecycle, scope, owner/approval refusal, parity, rate limits, Linux oracle, structural guards, plugin build, hygiene, Worker tests and public canary. No deployment is claimed by opening the PR.
