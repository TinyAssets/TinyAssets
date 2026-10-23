## Why

Owners need a live view of their hosted box's browser and desktop to take over passwords, 2FA and CAPTCHAs. N4 requires that view to stay within the owner's isolation boundary.

## What Changes

- Expose one live browser and desktop view through the interface package and the owner door.
- Define owner-only input and stream access, private credential entry, and a truthful unavailable state.
- Use the `BoxProvider.view` hook in ADR-022 P4; keep the shared router limited to authentication and forwarding.

## Capabilities

### New Capabilities

- `live-box-takeover-view`: Owner-only live box view and input contract.

### Modified Capabilities

None. The existing browser-login-custody contract continues to own site session custody and reuse.

## Impact

Spec-only N4 slice on `spec/n4-n8-view-and-pc-link`. Future implementation touches the owner box, its provider view hook, the owner door and the interface package. Depends on the owner-daemon-in-box target shape and interface package. No product code in this change.
