## Why

A live OpenRouter ceremony began before 04:34:42 UTC on September18; daemon
deployment restarted at04:34:47, before consent returned at04:36. The pending
owner/home/PKCE record is process-local and disappears on restart, causing a
fresh authorized callback to fail. No authorization replay or credential read
was used to investigate. Provider protocol matches current official PKCE docs.

## What Changes

- Persist only bounded, short-lived authorization binding metadata across restart.
- Keep browser-only verifier, server-only exchanged keys, exact owner/home/preset
  checks and one terminal exchange attempt. Do not replay provider requests.
- Preserve existing routes, public signatures, free-only grant approval and model choices.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `onboarding-web-app`: hosted authorization survives daemon replacement.
- `account-deletion`: pending personal authorization metadata is erased.

## Impact

Add one small root satellite SQLite store, hosted_model_auth transport internals,
focused tests and plugin mirror. No model_connect ingress changes; the separate
manual-key proposal owns that file. Shape review gates this persistence change.
