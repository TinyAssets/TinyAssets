## Context
Pending storage already deduplicates only pending asks; explicit suppressions are separate. Generic OAuth binds owner consent at begin, allowing a cookie-less completion once.

## Goals / Non-Goals
Recover cleared asks and protect consent across action additions and logout. Preserve the existing OAuth begin/complete contract and static prompt budgets.

## Decisions
- Add general recovery guidance to the rail and on-demand requests chapter rather than the resident prompt. Re-ask when the need recurs; never automatically undo an explicit mute.
- Maintain explicit consent/non-consent sets, test them against validator branches, and gate every action outside the non-consent set. Unknown kinds fail closed even before classification catches up.
- Revoke generic and hosted PKCE rows by owner and cancel inline rows, erasing sealed callback data. Preserve other owners' flows.
- Retain cookie-less completion for an owner-approved flow; test replay and foreign handles at the HTTP endpoint.

## Risks / Trade-offs
Flow stores use separate transactions: cancel flow authority before revoking sessions and propagate errors. Already redeemed external requests are outside outstanding-flow cancellation.

## Migration Plan
No schema migration. Ship code and mirror together; rollback restores previous handlers.

## Open Questions
None. Draft PR delivery only; deployment and live proof remain after merge.
