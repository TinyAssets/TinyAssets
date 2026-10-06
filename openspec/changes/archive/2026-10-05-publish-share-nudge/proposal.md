## Why

After publishing, the owner's agent cannot reliably see which listing succeeded
or offer to share its public screen. Founder request, 2026-10-05.

## What Changes

- Return and retain an owner-scoped publication completion with listing, change,
  version, optional link and public-snapshot preview path.
- Seed an editable share-after-publish skill through the connect-skill pattern.

## Capabilities

### New Capabilities
- `publish-completion`: publication receipts and editable sharing guidance.

### Modified Capabilities
None.

## Impact

Publish approvals, definition publishing, existing preview renderer, starter
skills, handbook and tests. Owner: Codex; branch: feat/publish-share-nudge.
Delivery is commit and push only, with no PR, per founder instruction.
