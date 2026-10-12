## Why

Accepted messages are invisible outside the sending tab until completion, and open shells retain old code and history after deploys.

## What Changes

- Expose accepted pending conversation entries with stable identity and live state through existing fenced owner reads.
- Merge conversation updates while the app is open; reconcile pending entries with final pairs.
- Revalidate the shell and use a content version to upgrade open tabs while retaining unsent drafts.

## Capabilities

### New Capabilities

### Modified Capabilities

- `onboarding-web-app`: live cross-surface conversation and draft-preserving shell upgrades.

## Impact

Owner and connector conversation readers, canonical admission observations, app shell/version handling, and browser tests. One PR on fix/app-live-pending-and-fresh-shell; owner Codex. No execution replay or new authority.
