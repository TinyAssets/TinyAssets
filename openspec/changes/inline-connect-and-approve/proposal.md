## Why

The bubble is always-present plumbing: emergency control and conversation with every agent. At `95508ecdc0`, requests sit in a polled rail and approval needs a relayed chat turn. The founder's 2026-10-04 direction calls for Muse-like inline connection and action-bound approval that finishes the original request.

## What Changes

- **Founder decision 2026-10-04:** foreground asks open a protected approval sheet at the point of need; away/background asks enter a small Needs you inbox with push notifications. Remove the side requests panel and its history rail; retain ordinary activity receipts and consolidate connect entry points.
- Bind previews to protected normalized actions; Approve executes server-side and Edit revalidates. Once is a decision; task/site/always reuse owner rules with explicit scope and expiry. All scopes recheck the applicable policy digest at dispatch and refuse stale approval. Spend/unclassified effects require the exact-total once-only contract immediately; missing enforcement or unknown totals refuse execution even before the payment rail ships.
- Authenticate approval through protected interactive owner sessions and single-use bound tokens; an owner's bearer-holding chatbot cannot approve.
- First-connect follow-up: reuse protected server-PKCE login for normal web app sign-in, establishing app renewal and owner proof together; preserve native's separate browser proof requirement.
- Resume the saved active agent/task server-side after answers/results, with Stop/expiry invalidation and boot recovery through existing activity events until processed-ack, yielding one committed processing result per answer.
- Keep provider sign-in in a popup/system browser while the callback server uses server-held PKCE to exchange, deposit and wake without the parent app. A top-level TinyAssets hop binds state to the initiating owner browser session; the callback itself must carry that exact session and flow cookie.
- Reuse pending requests, rules, effect intents and activity events with one protected request authority, a named owner-control lock owner, explicit mutation refusal during migration pause and recovery on either side of cutover.

## Capabilities

### New Capabilities

- `request-continuations`: durable action binding, interactive owner decisions, scoped preapproval, task lifetime and cross-surface resumption.

### Modified Capabilities

- `onboarding-web-app`: protected inline cards, persistent emergency controls and server-completed popup/native sign-in.
- `live-mcp-connector-surface`: bound-action ask/read fields and an explicit boundary between automated bearer access and interactive approval; no new MCP handles.

## Impact

Future implementation touches pending-request storage/API, authenticated effectors, owner-session authentication, agent rules/turn dispatch, OAuth ingress and the app. Protected storage extensions, migration and public contracts are specified in design.md. Only the initiating agent's editable owner rules choose approval policy; cross-user isolation is the fixed policy floor. Connection grants, consent and secret custody remain enforced. Authenticating a claimed owner approval introduces no mandatory platform gate.

Owner: Codex. Branch: `spec/inline-connect-and-approve`. One intent: complete connection and approval interruptions through a protected sheet and Needs you inbox. This delivery is proposal-only: no product code, deployment or new PR. Future implementation is one lane/PR coordinated with `addressed-agent-control-provenance`, `generic-oauth-connections` and `notify-owner-of-requests`.

## Follow-ups (out of scope)

- Shared connector directory and TinyAssets provider OAuth registrations; Google/LinkedIn coverage is not promised here.
- Custom API-key/MCP-URL connectors, including D6 attach, remain separate work.
- D5 browser fallback remains a separate alternative.
- SSE event transport, snapshot/replay and working-status polling replacement belong in a separate lane; current refresh remains.
- A general `request_invalid` field-error schema and manifest-error redesign belong in a separate lane; existing validation conventions remain.

## Review disposition and proposal verification

The Claude ADAPT review's eight DISAGREE findings are incorporated in design.md and the acceptance scenarios: server callback completion, interactive approval provenance, stable applicable-policy digests, reused tables/once decisions, protected rendering, task expiry/Stop, boot wake recovery and narrowed transport/error scope. The Muse flow in finding 9 remains intact. Ten implementation boxes intentionally remain open; no implementation or live verification is claimed.

Final round-2 review: A is addressed by callback-carried initiating owner-session and flow-cookie checks, including rejection of copied authorization URLs; B by durable processed-ack, boot redelivery of admitted-but-unprocessed wakes and dedupe retention beyond replay sources; C by dispatch-time policy comparison/refusal for once; F by coordinator-owned cross-worker serialization, retryable refusal during a durable migration pause and explicit cutover/grant recovery. Design, normative scenarios and future implementation tasks carry these requirements. No further review agents are dispatched.

Final revision verification (2026-10-04): `openspec validate inline-connect-and-approve --strict` and `git diff --check` passed. `python -m ruff check .` again reported 59 existing errors in unchanged Python files; this revision changes only five Markdown artifacts in this change. No product code was edited and no sub-agents were used.
