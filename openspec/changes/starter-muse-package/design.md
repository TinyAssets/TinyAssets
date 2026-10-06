## Context

PR #4514 publishes five editable skills and hooks, but no production caller installs that bundle yet. K3 adds content to the same publisher; provisioning remains the existing D10 lane's responsibility. The founder's Muse fit note was read from origin/docs/starter-agent-muse-fit.

## Goals / Non-Goals

Provide all six requested abilities as editable assets using files, ta, app_ui and scheduled agent workflows. Do not change platform runtime, resident instructions, permissions or seeding policy.

## Decisions

- Put new personal data under starter/ to avoid taking over the existing OKF goals.md. JSON is editable by the agent and rendered as text by the layout.
- Default proactivity to low. Off suppresses unsolicited goal updates and suggestions; explicitly requested reminders and monitors continue until cancelled. Low reports completion/blockers; high also reports next-step, priority and deadline changes. Never notify for formatting, timestamps or an unchanged check.
- A package Python script runs in the owner's existing bash workspace, reads current files on each run and calls notify via ta. Scheduled prompt templates invoke it; they do not embed new platform behavior. State records successful notification receipts and observed baselines. Failures remain retryable and visible.
- Ship UI as an app_ui component JSON using the existing tinyassets.call bridge. Chat uses ordinary send_message; other views read owner files. Activation is a governed app_ui write, not an automatic runtime hook.
- Images and inbox skills discover actual connected capability schemas. No provider-specific code, invented provider, stock credential or assumed consent.

## Risks / Trade-offs

- Missing seeding consumer prevents fresh-account delivery: record the dependency; do not introduce another installer. Bundle materialization tests are explicitly not production provisioning proof.
- Scheduled agent steps need the owner's connected compute. Never claim a workflow is active from files alone; require automation receipts.
- Notification and file acknowledgement are not atomic: existing notify deduplication limits retries while outstanding. A crash after delivery can retry a dismissed notification; no exactly-once claim.
- Owner edits/deletions are respected by the seed-lifecycle consumer, not by this content publisher. Publishing content performs no writes to an account.

## Migration Plan

Consume expanded starter_agent_files() through D10 after its transaction API lands. Existing accounts retain edits/deletions. Until then this is a draft package, not a deployed default. No migration or fallback installer in K3.

## Open Questions

None; outstanding prerequisite is tracked in tasks and the PR.
