## Context

Bindings have configured/serving status, revision fencing, and owner identity. The roster and addressed-agent resolver share `is_conversable`. Interactive turns have cooperative interruption; automation agent execution needs the same lifecycle check.

## Goals / Non-Goals

Provide reversible retirement through existing tools with owner isolation and clean cancellation. No per-feature editor, deletion, or automatic replay of stopped work.

## Decisions

- Add a durable `retired` boolean column, default false, and the last retirement revision, rather than delete bindings or rebuild the status CHECK. Preserve status/configuration and all linked data; account deletion continues to enumerate all bindings. A turn captures the retirement revision at resolution so even a quick retire/restore in another process stops the older turn.
- Add `retire`/`restore` graph operations with binding ID and positive expected revision. Check creator ownership and existing universe access, serialize with binding admission, and use atomic revision comparison. Refuse main, serving bindings, current/legacy platform definitions, provider-bearing bindings even while disconnected, and non-agent roles.
- Filter public binding lists and addressability; direct binding reads retain retirement state and revision for restore. Internal enumeration retains its all-binding default so bootstrap and ambiguity checks still see retired records.
- Interrupt running work for the exact owner/command-center/agent with reason `agent retired`; recheck durable lifecycle at execution boundaries to cover admission races and background execution. Restore enables new work without restarting cancelled work.
- Keep discoverability in the on-demand handbook and prove the existing switcher through Playwright imported within the test.
- Serialize custom-agent activity validation and insertion under shared binding admission, so retirement's exclusive activity fence cannot miss an insertion that already passed validation. Ordinary graph automations have no binding identity; scheduled activities with an agent ID use the same fence as interactive activities.

## Risks / Trade-offs

An already dispatched external tool cannot be undone: allow it to settle, then stop at the existing safe boundary. Retirement must survive process restart; durable checks complement in-process wakeups. Migration is additive and preserves all records.
