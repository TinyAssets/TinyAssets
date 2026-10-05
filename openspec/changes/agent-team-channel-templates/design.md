## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Rebuilding multi-agent command centers needs reusable spawn/message/task-board primitives and channel routing packaged as data rather than hand-built service code. Extend in-platform-agent-systems, command-center-agent-templates, channel-agnostic-inbound/outbound and full-channel-access; existing scheduler and budgets remain owners. resident-agent-processes supplies gateways, harness-control supplies hooks/events, package and recipient-update lanes supply publishing/copy/update. No platform LLM or provider-specific compute/integration path.

## Decisions

Use existing roster and agent-system launch records for ta spawn: bind owner/center, parent run, child role/model binding, task ID, allowed capabilities, budget and cancellation relationship. Support single, parallel, chain, nested and background delegation using one primitive with explicit dependency records. Authority is an intersection of current parent delegation and child-local grants, never a copied YAML assertion. Children select their own owner-connected model and return bounded summaries or owner-scoped artifact references; uncertain effects remain receipts, not success prose.

Ta message targets a real agent/run mailbox with authenticated sender and recipient bindings, idempotent message ID, reply correlation and delivery/ack state. Per-owner agent messaging needs the current declared grant; cross-user routing requires the existing recipient permission path, never an assumed same-owner shortcut. A message is task content, not approval or permission. Unknown/stopped recipients remain visibly undelivered. Define Stop propagation at spawn: attached children stop with parent; explicitly detached background work has its own visible owner lifecycle. Revocation always wins.

Task board is an ordinary owner-scoped data collection: stable task ID, assignee, parent/dependencies, state, expected revision, result refs and budget status. Claim/update uses optimistic concurrency so two workers cannot silently overwrite each other; history and event stream expose changes. Reuse budgets for per-agent pause at caps. Do not make the board a second execution authority.

Channel bundles compose receiver + connection + outbound op + routing data (topic/thread/mention to roster role), including Telegram/Discord/Slack/WhatsApp/email examples as community templates, with no named-service branches in platform code. Import leaves credential, forum/channel, receiver and model slots unbound until the recipient connects their own accounts; exported samples omit author messages/private data. Validate inbound authenticity through the existing receiver and dedupe before routing. Gateway and outbound effects obey current connection and approval rules.

Publish versioned bundles through command-center-packages. Follow-up command-center-recipient-updates provides recipient-selected manual or automatic updates (automatic off by default) for agents, extensions and channel templates as well as UI, with changelog, three-way conflict handling preserving local edits and Undo; harness-control exact-bundle permissions still apply, including author/capability-ceiling auto-update grants. Existing attribution preserves original and remix lineage/credit through publication/copy/update; creator-revenue-share owns monetary allocation. These are acceptance dependencies, not a second updater or ledger built in this change.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

T2/T3/T6/T7/T8/T10 in command-center-harness-control/design.md are the integrated bar: mixed-model team, forum routing, ticket/budget company, voice/subagents, debate and coding workers. Every test uses general primitives, published second-account copy and cloud-only 24/7 rendered proof. T7 revenue and T8 two-way paid/subscribed signals depend on existing revenue/cross-user lanes and cannot be declared complete from this change alone.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.
