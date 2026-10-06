## Context

Bindings have configured/serving status, revision fencing, and owner identity. The roster and addressed-agent resolver share `is_conversable`. Interactive turns have cooperative interruption; automation agent execution needs the same lifecycle check.

## Goals / Non-Goals

Provide reversible retirement through existing tools with owner isolation and clean cancellation. No per-feature editor, deletion, or automatic replay of stopped work.

## Decisions

- Add a durable `retired` boolean column, default false, rather than delete bindings or rebuild the status CHECK. Preserve status/configuration and all linked data; account deletion continues to enumerate all bindings.
- Add `retire`/`restore` graph operations with binding ID and positive expected revision. Check creator ownership and existing universe access, serialize with binding admission, and use atomic revision comparison. Refuse main, serving bindings and non-agent roles.
- Filter ordinary binding lists and addressability; direct binding reads retain retirement state and revision for restore. Internal all-binding enumeration explicitly includes retired records.
- Interrupt running work for the exact owner/command-center/agent with reason `agent retired`; recheck durable lifecycle at execution boundaries to cover admission races and background execution. Restore enables new work without restarting cancelled work.
- Keep discoverability in the on-demand handbook and prove the existing switcher through Playwright imported within the test.

## Risks / Trade-offs

An already dispatched external tool cannot be undone: allow it to settle, then stop at the existing safe boundary. Retirement must survive process restart; durable checks complement in-process wakeups. Migration is additive and preserves all records.
