## Why

An owner should be able to authorize an outside agent to message chosen TinyAssets agents and see/control their command center without handing every OAuth client the owner's entire authority. The [preserved research](../../../docs/design-notes/2026-10-05-outside-agents-both-ways.md) (C1-C4, G1-G4/G6) identifies missing client attribution, delegation and revocation on the existing public MCP surface.

Authoritative founder direction, 2026-10-05:
> if a user connects their Muse agent to TinyAssets then the Muse agent should be able to send messages to the agents and see and control your command center. If they have a ChatGPT dot similar or any other agent they want to authorize like maybe their own OpenClaw agent they already have. We should also be able to connect to their Muse or other agents and talk to them if connected and authorized.

## What Changes

- Attach verified OAuth client identity and CIMD/DCR display metadata to each request principal; preserve it in turn/run/activity provenance and an owner-readable audit of which outside app did what.
- **BREAKING:** replace implicit full-account outside-client authority with owner-editable per-client grants: allowed agents and `read`, `message`, `control`, `costly` (spend, publish, external posts). First connect receives read + message to `main` only; the owner may widen or narrow. Apply uniformly to existing clients at cutover unless an owner explicitly authorizes wider grants.
- Enforce current grants daemon-side at every existing handle and downstream dispatch; refuse with the missing level/agent grant. Retain owner isolation and existing resource authority.
- Add Connected apps in the app, with client identity, grants, last-used, edit and immediate revoke/token invalidation. Extend the existing access readback; no new MCP handle.
- Route sensitive actions through [inline-connect-and-approve](../inline-connect-and-approve/proposal.md), including originating client provenance. Bearer clients can never approve; reuse its sheet, policy and continuation machinery.
- Prove the founder can connect Muse to `https://tinyassets.io/mcp`, including AuthKit acceptance of `https://agent.meta.ai/api/hatch/oauth/callback` through CIMD/DCR. This is an open acceptance task, not a claim that Muse works today.

## Capabilities

### New Capabilities

- `outside-agent-grants`: client attribution, owner-editable delegation, Connected apps and revocation.

### Modified Capabilities

- `live-mcp-connector-surface`: require client grants at all existing handles, preserve owner identity across hosts, extend access readback and bind outside-client provenance to approvals.

## Impact

Docs/specification only in this delivery. Future implementation touches AuthKit principal binding, daemon admission/storage/provenance, the existing access reader and owner app. WorkOS AuthKit remains the authorization server with CIMD and DCR; TinyAssets remains the resource server at `https://tinyassets.io/mcp`. Muse, ChatGPT, OpenClaw and every other client use the same shape, with no per-agent-brand code. Cross-user isolation is the only immutable platform behavioral invariant; client authority is strictly the authorizing owner's authority within owner-editable grants.

Owner: Codex. Branch: `spec/outside-agent-grants`, based on `origin/main`. One intent: bound outside-client delegation on the existing MCP surface. Commit and push this proposal only; no PR, implementation or deployment in this delivery. Implementation remains one coherent later lane.

Extend/reference existing work: [addressed-agent-control-provenance](../addressed-agent-control-provenance/design.md) owns addressed-agent propagation; [agent-access-controls](../agent-access-controls/design.md) owns access readback/channel revoke; [inline-connect-and-approve](../inline-connect-and-approve/design.md) owns protected approvals. Their requirements are not reimplemented here. Audit found all three existing changes; this change adds the independent OAuth-client authority axis.

## Separate later changes

- `read_graph target=live` and a per-client outbox for talk-back to MCP-only hosts (research C3, D5).
- A2A client/server using these same grants (research B, D7).
- Outbound MCP attach through the existing dependency chain [per-role-uid-split](../per-role-uid-split/proposal.md) → [broker-streaming-contract](../broker-streaming-contract/proposal.md) → [connect-anything-ladder](../connect-anything-ladder/proposal.md) (research C5, D6).

These follow-ups are recorded here without new change directories, protocol designs or implementation tasks.

## Proposal verification

Based on `origin/main` at `aab7b2d1f7035a8cf5294757620c51aaab61c8c7`. Strict OpenSpec validation passed; all planning artifacts are complete and the 12 future implementation tasks remain open. Delivery admission returned ALLOWED. The research body is byte-for-byte identical to the supplied file after the added header; authored relative links resolve and the staged diff passes whitespace checks. `python -m ruff check .` reports 55 existing errors in unchanged Python files; this delivery changes only Markdown and OpenSpec metadata. No product tests, deployment or live Muse proof were performed for this proposal.

One Claude cross-family floor/correctness review returned ADAPT. **AGREE** with its first-connect finding: home-keyed grants must not block a new user's first message. Design and normative scenarios now bind the default grant during the existing private-home bootstrap, retain non-provisioning no-home status and check revocation before either path. **AGREE** with its authorizing-owner clarification: client inventory/audit belongs only to the authorizing user, even with shared universe access. No lane collision was found. Review confirmed bearer approval refusal, immediate revoke/generation fencing, grant-level semantics and the follow-up boundary; no second review round was requested.
