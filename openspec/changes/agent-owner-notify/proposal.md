## Why

Agents currently imitate notifications with unanswered requests, and scheduled work cannot reliably explain how it reaches its owner. Provide an informational notification through the existing request and device surfaces.

## What Changes

- Add `notify` to the existing `write_graph target=pending_request` surface for main, custom and workflow agents.
- Reuse request storage, Needs-you, owner device delivery and deduplication; notifications require no answer.
- Supply short editable capability guidance covering the persistent box, files and scheduled notifications.

## Capabilities

### New Capabilities
- `agent-owner-notify`: owner-only informational delivery and accurate agent guidance.

### Modified Capabilities
None.

## Impact

Request API and storage projection, engine dispatcher, notification composer, app inbox, starter content and served handbook. No new MCP handle or transport.
