## 1. Owner-box app

- [x] Store the app document and compare-and-swap room under the owner's root.
- [x] Publish only the slug, owner and display name through the broker.
- [x] Route a signed-in player to that box with a verified caller, and drop a forged caller.
- [x] Refuse a visitor's private pages and other non-app doors.

## 2. Proof

- [x] Two players conflict on one etag, and a player record stays with its caller.
- [x] The shared router, broker and waker do not reference the room store.
