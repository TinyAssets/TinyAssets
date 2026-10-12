## Why

Deploy interruptions stranded the founder twice (#4479): no frame, bubble mode,
and disabled AppUI. Reload recovered it. Recovery must work outside that state.

## What Changes

- Independent recovery controls, bounded frame/page retry, and private draft restore.
- Treat failed identity reads as unavailable, not as a reason to tear down the UI.
- Redirect stale module hashes to the current allowlisted module without caching the redirect.

## Capabilities

### New Capabilities
- `command-center-recovery`: Recovery from interrupted app and frame startup.

### Modified Capabilities
None.

## Impact

Onboarding shell, frame boot reporting, static module route, browser tests.
Owner: Codex; branch: fix/command-center-crash-recovery; supersedes #4481.
