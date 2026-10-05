## Context

Pending requests already persist inbox entries and send owner-bound web, desktop and mobile push. Workflow code nodes have an authenticated `invoke_mcp_action` bridge; agent nodes share the engine tools.

## Goals / Non-Goals

Give every owner agent and workflow step an informational notify primitive. No new transport, recipient selector, MCP handle, per-turn cap or platform behavior policy.

## Decisions

Use `write_graph target=pending_request operation=notify` and the existing code-node bridge action `notify`. Both call the same owner-gated API. Code nodes use their trusted run universe and existing bound run identity, never payload identity. Notification payloads contain title, body, optional item reference and attachment reference. The originating agent is server-derived; optional `link_to_thread` resolves an agent within the same authenticated owner and command center. The chat link cannot select another owner.

Reuse pending_requests with action.type=notify and empty fields/items. Pending means unread/undismissed for this kind; projection identifies it as informational and requiring no answer. The UI shows text, references, Open chat and Dismiss, never approval/answer controls. The answer API refuses notification answers; existing withdrawal dismisses it. Identical content and references deduplicate while outstanding, scoped to originating agent. Device delivery additionally retains its existing ledger dedupe.

Push payloads retain the request ID route, opening the durable notification card. The card opens the source agent's chat and optional item. The generic composer labels notifications as updates instead of asks. Notification creation returns delivery outcomes rather than claiming a phone received a push when no registered transport sent it.

Keep capability prose in user-editable starter content and served handbook chapters. Existing user files are never overwritten.

## Risks / Trade-offs

Push is best effort: durable inbox delivery survives unavailable devices. Attachment references are data, not executable URLs or permission grants. Existing request storage remains agent-writable and is not an authorization source. Rollback code leaves readable records; no database migration is needed.
