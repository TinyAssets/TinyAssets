## Context
Pending storage already deduplicates only pending asks; explicit suppressions are separate. Generic OAuth binds owner consent at begin, allowing a cookie-less completion once.

## Goals / Non-Goals
Recover cleared asks and protect consent across action additions and logout. Preserve the existing OAuth begin/complete contract and static prompt budgets.

## Decisions
- Add general recovery guidance to the rail and on-demand requests chapter rather than the resident prompt. Re-ask when the need recurs; never automatically undo an explicit mute.
- Maintain explicit consent/non-consent sets, test them against validator branches, and gate every action outside the non-consent set. Unknown kinds fail closed even before classification catches up.
- System-created requests join that inventory: start_activity and approve_action require consent; notify is non-consent and cannot be answered. Activity proposals use the protected owner answer path, including decline and clear, consistently with other effectful asks.
- Revoke generic and hosted PKCE rows by owner and cancel inline rows, erasing sealed callback data. Preserve other owners' flows.
- Retain cookie-less completion for an owner-approved flow; test replay and foreign handles at the HTTP endpoint.

## Risks / Trade-offs
Flow stores use separate transactions: cancel flow authority before revoking sessions and propagate errors. Already redeemed external requests are outside outstanding-flow cancellation.

## Migration Plan
No schema migration. Ship code and mirror together; rollback restores previous handlers.

## Open Questions
None. Draft PR delivery only; deployment and live proof remain after merge.

## Reask lane follow-up
Main already contains #4504's shared lifecycle and #4477's as-built consent spec.
The remaining history action uses the existing chat relay, addressed to the
original asking agent. It asks for a fresh ask if needed; it does not replay an
old effect, mutate a resolved row, or lift a mute. The same UI handles every
request kind. Chromium exercises the real request API through a test transport
bridge; a later agent-observed failure re-raises through the existing ask API.
