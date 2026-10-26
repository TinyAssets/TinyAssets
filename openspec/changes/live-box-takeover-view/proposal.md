## Why

Owners need a live view of their hosted box's browser and desktop to take over passwords, 2FA and CAPTCHAs. N4 requires that view to stay within the owner's isolation boundary.

## What Changes

- Extend the existing protected browser login view to the box's browser and desktop through the interface package and owner door.
- Define owner-only input and stream access, private credential entry, and a truthful unavailable state.
- Use the `BoxProvider.view` hook in ADR-022 P4; keep the shared router limited to authentication and forwarding.

## Capabilities

### New Capabilities

- `live-box-takeover-view`: Owner-only live box view and input contract.

### Modified Capabilities

- `browser-login-custody`: its protected live owner view becomes the single `BoxProvider.view` takeover, with the browser in the owner's box and the view process inaccessible to agent bash.

## Impact

Spec-only N4 slice on `spec/n4-n8-view-and-pc-link`. Future implementation touches the owner box, its provider view hook, the owner door and the interface package. Depends on the owner-daemon-in-box target shape and interface package. No product code in this change.
