## Why
The founder cannot follow reply links, see inline diagrams/charts, or download exports from app chat.

## What Changes
- Render safe HTTP(S) links and data-only Mermaid/chart fences, with code fallback.
- Add owner-session file download chips backed by the existing folder authorization and safe reader.
- Teach the editable starter guidance and served tool guidance these reply formats.

## Capabilities
### New Capabilities
- `chat-reply-content`: safe rich replies and private file delivery.
### Modified Capabilities
None.

## Impact
App rendering, owner door (new authenticated POST /app/api/file), starter guidance, plugin mirror. No request-answer changes. Owner: Codex; branch: fix/chat-links-visuals-files; one draft PR.
