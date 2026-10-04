## Why

The bubble is always-present plumbing: emergency control and conversation with every agent. At `95508ecdc0`, requests sit in a polled rail and approval needs a relayed chat turn. The founder's 2026-10-04 direction calls for Muse-like inline connection and action-bound approval that finishes the original request.

## What Changes

- Put request cards in the bubble thread; demote the rail to history and consolidate connect entry points, retaining existing refresh transport.
- Bind previews to protected normalized actions; Approve executes server-side and Edit revalidates. Once is a decision; task/always reuse owner rules with explicit scope and expiry.
- Authenticate approval through protected interactive owner sessions and single-use bound tokens; an owner's bearer-holding chatbot cannot approve.
- Resume the saved active agent/task server-side after answers/results, with Stop/expiry invalidation and boot recovery through existing activity events.
- Keep provider sign-in in a popup/system browser while the callback server uses server-held PKCE to exchange, deposit and wake without the parent app.
- Reuse pending requests, rules, effect intents and activity events with one protected request authority and explicit migration/crash behavior.

## Capabilities

### New Capabilities

- `request-continuations`: durable action binding, interactive owner decisions, scoped preapproval, task lifetime and cross-surface resumption.

### Modified Capabilities

- `onboarding-web-app`: protected inline cards, persistent emergency controls and server-completed popup/native sign-in.
- `live-mcp-connector-surface`: bound-action ask/read fields and an explicit boundary between automated bearer access and interactive approval; no new MCP handles.

## Impact

Future implementation touches pending-request storage/API, authenticated effectors, owner-session authentication, agent rules/turn dispatch, OAuth ingress and the app. Protected storage extensions, migration and public contracts are specified in design.md. Only the initiating agent's editable owner rules choose approval policy; cross-user isolation is the fixed policy floor. Connection grants, consent and secret custody remain enforced. Authenticating a claimed owner approval introduces no mandatory platform gate.

Owner: Codex. Branch: `spec/inline-connect-and-approve`. One intent: complete connection and approval interruptions in the bubble. This delivery is proposal-only: no product code, deployment or new PR. Future implementation is one lane/PR coordinated with `addressed-agent-control-provenance`, `generic-oauth-connections` and `notify-owner-of-requests`.

## Follow-ups (out of scope)

- Shared connector directory and TinyAssets provider OAuth registrations; Google/LinkedIn coverage is not promised here.
- Custom API-key/MCP-URL connectors, including D6 attach, remain separate work.
- D5 browser fallback remains a separate alternative.
- SSE event transport, snapshot/replay and working-status polling replacement belong in a separate lane; current refresh remains.
- A general `request_invalid` field-error schema and manifest-error redesign belong in a separate lane; existing validation conventions remain.

## Review disposition and proposal verification

The Claude ADAPT review's eight DISAGREE findings are incorporated in design.md and the acceptance scenarios: server callback completion, interactive approval provenance, stable applicable-policy digests, reused tables/once decisions, protected rendering, task expiry/Stop, boot wake recovery and narrowed transport/error scope. The Muse flow in finding 9 remains intact. Ten implementation boxes intentionally remain open; no implementation or live verification is claimed.

Revision verification (2026-10-04): `openspec validate inline-connect-and-approve --strict` and `git diff --check` passed. `python -m ruff check .` reported 59 existing errors in unchanged Python files; this revision changes only six Markdown artifacts in this change. No product code was edited and no sub-agents were used.
