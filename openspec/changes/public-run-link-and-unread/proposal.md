## Why

Published designs need a public entry point into the normal owner-approved install flow. Owners also need account-synced unread indicators for replies and asks.

## What Changes

- Public listing preview and Run in your universe entry point, used by publication receipts.
- Server-stored owner read receipts, cleared only for viewed messages and asks.

## Capabilities

### New Capabilities
- `public-run-link`: public published listing preview and authenticated install handoff.
- `owner-unread`: account-scoped unread messages and pending asks.

### Modified Capabilities

None.

## Impact

Onboarding routes and app, publication completion, owner receipt storage, tests and plugin mirror. Owner: Codex, lane L8, branch feat/public-run-link-and-unread; one draft PR. Existing install approval authority remains authoritative.
