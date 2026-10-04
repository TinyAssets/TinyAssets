# Proposal

## Why
Owners can copy a shared center today, but Preview renders no visual and the consent is hard to find. Approval sends an unnecessary model message, while navigation disappears behind the adopted design's chat menu.

## What Changes
- Keep browsing and switching inside trusted main-chat controls, including collapsed chat.
- Show a swipeable visual catalogue with publisher descriptions and an isolated public preview that has no owner bridge.
- Separate preview from pinned copy consent, expose that consent immediately, and finish installation without an inference relay.
- Preserve existing content, owner fences, paused automations and explicit opening of the copied screen.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `command-center-discovery`: persistent navigation, visual preview and model-independent deterministic adoption.

## Impact
Command-center picker, app UI controller/chat chrome, public preview read surface, focused unit/browser tests and packaged mirrors. Agent exports and recipient version updates follow as separate reviewed changes; this patch does not edit a live published design.
