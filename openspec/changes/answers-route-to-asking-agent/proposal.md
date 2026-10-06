## Why

#4494: answers to a sub-agent's requests are relayed into main's chat. Routing must belong to the stored request rather than the answering device's selected chat.

## What Changes

- Capture the authenticated asking agent and execution provenance for every request and notification.
- Deliver owner answers and replies through one durable server path to that agent; fence owner changes and fall back visibly for removed agents.
- Remove client-selected answer relays and prove browser, desktop and phone behavior.

## Capabilities

### New Capabilities
- `request-answer-routing`: owner-fenced, durable delivery to the asking conversation.

### Modified Capabilities

## Impact

Existing protected pending-request storage, answer handlers, continuation worker and app cards. No new on-disk store. Owner: Codex; branch: fix/answers-route-to-asking-agent; one draft PR for #4494.
