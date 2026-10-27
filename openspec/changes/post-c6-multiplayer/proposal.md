## Why

Several signed-in accounts need to use one app at the same time. After C6 and C12 K1, that app and its shared state belong in the app owner's box. A pre-C6 shared-database design stored the live records outside every box and edited doors C6 removed.

## What Changes

- Publish a slug as a route binding only: slug and owner, copied out so the router can select the box.
- Forward the authenticated player into that box as the verified caller. The player cannot supply the owner or the caller.
- Keep the app document under the owner's root, and room and player records in the owner's broker-held conversation store (S5a), with compare-and-swap on each write.
- Refuse a visiting player's use of the owner's private pages and other non-app surfaces.

## Capabilities

### New Capabilities

- `post-c6-multiplayer`: One published app in its owner's box, played by other accounts through a verified caller and compare-and-swap room.

### Modified Capabilities

## Impact

The owner router, owner ingress, the published route binding, and the in-box app room. No shared room table and no second copy of the app.
