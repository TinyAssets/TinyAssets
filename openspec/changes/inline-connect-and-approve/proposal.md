## Why

The bubble is always-present plumbing: emergency control and conversation with every agent. At `95508ecdc0`, requests sit in a polled rail and an approval requires a relayed chat turn; the founder's 2026-10-04 direction calls for Muse-like inline connection and approval that finish the original request.

## What Changes

- Push request cards and live status into the bubble thread; demote the rail to history and consolidate connect entry points into one card.
- Bind approvals to normalized, hashed actions; Approve executes server-side, Edit revalidates, and once / this task / always writes an owner rule.
- Resume the initiating agent server-side after an answer from any surface; retain failed work with Try again / alternative / skip.
- Open provider sign-in in a popup or in-app browser, preserving the thread, credential custody and existing consent.
- Return field-specific request schema errors, without format guessing.

## Capabilities

### New Capabilities

- `request-continuations`: durable action binding, scoped owner decisions and cross-surface resumption on existing pending requests.

### Modified Capabilities

- `onboarding-web-app`: inline cards, persistent emergency controls, pushed status and sign-in presentation.
- `live-mcp-connector-surface`: explicit approval request/answer fields and actionable validation errors; no new MCP handles.

## Impact

Implementation will touch pending-request storage/API, authenticated effectors, agent rules/turn dispatch, OAuth ingress and the app. Protected storage additions and public wire contracts are specified in design.md. Approval policy remains the initiating agent's editable owner rules; cross-user isolation is the sole fixed platform policy invariant. Connection grants, consent and secret custody remain enforced.

Owner: Codex. Branch: `spec/inline-connect-and-approve`. One intent: complete connection and approval interruptions in the bubble. This delivery is proposal-only: no product code, deployment or PR. A future implementation is one lane/PR, coordinated with `addressed-agent-control-provenance`, `generic-oauth-connections` and `notify-owner-of-requests`; it does not reimplement their foundations.

## Follow-ups (out of scope)

- Shared connector directory: provider OAuth metadata belongs in commons data and needs TinyAssets-registered OAuth apps per provider, a founder business action.
- Custom connectors: API-key or MCP-URL creation, including D6 MCP attach, is a separate change.
- Browser fallback: D5 supplies the alternative when direct connection is unavailable; it is not implemented here.

Proposal verification (2026-10-04): `openspec validate inline-connect-and-approve --strict` passed; delivery admission is ALLOWED; 10 implementation boxes remain open.
