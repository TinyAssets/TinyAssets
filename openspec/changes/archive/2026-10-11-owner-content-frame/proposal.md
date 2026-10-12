## Why

Wiki directory creation under the daemon UID poisons storage observation and makes every production tool call fail with a misleading owner-content frame error.

## What Changes

- Publish wiki directories under their owner and safely recover empty category debris without changing isolation.
- Preserve bounded cell stderr and authenticated exit status in errors and daemon logs.
- Prove pending brain promotion and direct publication on a production backup.

## Capabilities

### Modified Capabilities
- `owner-cell-execution`: failed cell diagnostics and production-copy publication acceptance.

## Impact

Owner: Codex. Branch: fix/owner-content-frame. One infra-change PR. Launcher completion protocol, content cell, tool errors, and image acceptance. No production host changes.
