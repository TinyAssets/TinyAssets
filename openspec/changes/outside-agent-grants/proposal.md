## Why

An owner should be able to authorize an outside agent to message chosen TinyAssets agents and see/control their command center without handing every OAuth client the owner's entire authority. The [preserved research](../../../docs/design-notes/2026-10-05-outside-agents-both-ways.md) (C1-C4, G1-G4/G6) identifies missing client attribution, delegation and revocation on the existing public MCP surface.

Authoritative founder direction, 2026-10-05:
> if a user connects their Muse agent to TinyAssets then the Muse agent should be able to send messages to the agents and see and control your command center. If they have a ChatGPT dot similar or any other agent they want to authorize like maybe their own OpenClaw agent they already have. We should also be able to connect to their Muse or other agents and talk to them if connected and authorized.

## What Changes

- Attach verified OAuth client identity and CIMD/DCR display metadata to each request principal; preserve it in turn/run/activity provenance and an owner-readable audit of which outside app did what.
- **BREAKING:** replace implicit full-account outside-client authority with owner-editable per-client grants: allowed agents and `read`, `message`, `control`, `costly` (spend, publish, external posts). New clients snapshot an owner-editable starter (initial suggestion: read + message to `main`, message-only). Existing primary connectors retain equivalent authority through explicit migration grants or visible owner choice before cutover; never silently reduce their access. The configured first-party web/phone/desktop app is exempt from outside grants.
- Enforce current grants daemon-side at every existing handle; apply owner-selected message-only/message-and-act to downstream dispatch, preserving revocable origin; refuse with the missing level/agent grant. Retain owner isolation and existing resource authority.
- Cover every bearer HTTP surface by shared admission or explicit outside-client refusal; see [route inventory](bearer-surfaces.md). Name OutsideClientAuthority as the shared grant/fence store, and require a new outside-only kill switch with a rollback that retains admission. Trusted owner/universe automations and queued runs continue.
- Add Connected apps in the app, with client identity, grants, last-used, edit and immediate revoke/token invalidation. Extend the existing access readback; no new MCP handle.
- Route sensitive actions through [inline-connect-and-approve](../inline-connect-and-approve/proposal.md), including originating client provenance. Bearer clients can never approve; reuse its sheet, policy and continuation machinery. Its working protected owner session and grant widening/reconnect recovery are hard prerequisites for enforcement.
- Require two live clients on one account to prove independent grants, missing-grant refusal and next-call revoke, plus first-party app operation and protected grant widening after cutover, independent of Muse availability. Additionally prove the founder can connect Muse to `https://tinyassets.io/mcp`, including AuthKit acceptance of `https://agent.meta.ai/api/hatch/oauth/callback` through CIMD/DCR. This is an open acceptance task, not a claim that Muse works today.

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

Based on `origin/main` at `aab7b2d1f7035a8cf5294757620c51aaab61c8c7`. Strict OpenSpec validation passed; all planning artifacts were complete and the original 12 future implementation tasks remained open (prior-round evidence). Delivery admission returned ALLOWED. The research body is byte-for-byte identical to the supplied file after the added header; authored relative links resolve and the staged diff passes whitespace checks. `python -m ruff check .` reports 55 existing errors in unchanged Python files; this delivery changes only Markdown and OpenSpec metadata. No product tests, deployment or live Muse proof were performed for this proposal.

One Claude cross-family floor/correctness review returned ADAPT. **AGREE** with its first-connect finding: home-keyed grants must not block a new user's first message. Design and normative scenarios now bind the default grant during the existing private-home bootstrap, retain non-provisioning no-home status and check revocation before either path. **AGREE** with its authorizing-owner clarification: client inventory/audit belongs only to the authorizing user, even with shared universe access. No lane collision was found. Review confirmed bearer approval refusal, immediate revoke/generation fencing, grant-level semantics and the follow-up boundary; the subsequent round-2 review below supersedes this prior disposition where it found narrowing or missing admission.

## Round 2 review disposition

The supplied `review-outside.md` was read in full. **AGREE** with all seven required changes: configured first-party exemption (design decision 1); owner-editable starter and no silent primary-connector regression (decision 2 / migration); message-only versus message-and-act (decision 3); every bearer surface inventoried and admitted/refused (`bearer-surfaces.md`); named shared store plus new outside-only kill switch and viable rollback (decision 2 / migration); protected owner controls as an enforcement prerequisite (migration); mandatory two-client and first-party live proof independent of Muse (acceptance / tasks 3.3-3.4).

**AGREE** with reconnect lockout, bearer replay disclosure and broad-task concerns: protected owner re-consent binds a new credential family without reviving old tokens; bearer theft semantics are explicit; old tasks 2.1 and 2.4 are split into read, mutation, HTTP, local fence and reconnect tasks. There are 18 future tasks across sections of 4, 9 and 5, each within the owner's round-2 limit of 12 per section. Identity binding, cross-user isolation, private inventory/audit, bearer approval refusal and adjacent-change reuse remain intact. No product code or canonical as-built specs were edited. This records the supplied review's dispositions, not a new reviewer approval or implementation acceptance.

Round-2 verification: `openspec validate outside-agent-grants --strict` passed. Relative Markdown links, all 32 app route citations and other bearer-source citations were checked against this checkout; task sections contain 4/9/5 unchecked items. `git diff --check` passed. `python -m ruff check .` still reports the same 55 errors in unchanged Python files. Only the six Markdown paths in this change are staged; no implementation tests, deployment or live proof is claimed in this docs-only round.
