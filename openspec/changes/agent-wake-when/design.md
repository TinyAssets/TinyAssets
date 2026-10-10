## Context

Automations already persist interval/once/event workflow runs with owner admission. Run terminal events have an outbox; request answers (#4555) have protected deliveries and a continuation worker with a kernel lock. Owner-message wakes already exist. Those primitives do not express a saved follow-up for any named chat agent or durable release/connection/probe conditions.

## Goals / Non-Goals

Resume a named agent with its saved note, bounded by registration lifetime, repetitions and probe attempts, under its owner's normal budget. No new model tools, provider branches, global goals engine or workflow editor.

## Decisions

- Add wake rows in the existing protected request database. Reuse the continuation worker lock and owner_turn entrypoint rather than synthesizing workflow definitions for chat agents. Conditions read durable state each sweep, repairing the missed-event/restart window without a second event bus.
- `ta wake:register`, `wake:list`, `wake:cancel` derive owner/home/agent from the bound launch, never JSON. App list/cancel derives the home from the signed-in owner and requires the existing interactive owner session for cancellation.
- A registration includes a note, optional due time/interval, optional condition, expiry and finite max_fires. Time with a condition means not-before AND condition. A periodic match resets the next due time from completion, coalescing downtime. Probe polling uses finite attempts and exponential backoff; exhausted registrations remain visible.
- Request conditions require recorded asking owner and agent; run conditions require the recorded cause principal in the same home. Connections use the owner's broker catalogue. Release matches use a deploy-published ancestor/PR manifest in release_state, never an untrusted workspace file or runtime GitHub credential.
- Probe commands run through the existing bound BoxProvider execution path under their owner, with a stable attempt ID and finite timeout; successful exit only triggers a model turn. No daemon shell execution.
- Commit the firing state before a turn and acknowledge only success. Restart can repeat non-effect computation after an uncertain turn; existing effect ledgers govern external actions. Cancellation prevents any later dispatch; an already dispatched turn follows normal interrupt controls.

## Risks / Trade-offs

- Polling has sweep latency, not exact-second timing. Durable source reads avoid event-loss races.
- A retired or inaccessible agent is refused, never silently replaced or routed to another owner.
- No successful turn acknowledgement across a crash may repeat computation; no exactly-once effect claim.

## Migration Plan

Additive table in protected request storage and release manifest fields. Older release receipts cannot prove ancestor/PR containment and remain pending. Existing automations and answered-request deliveries keep their behavior.

## Open Questions

None for this slice.
