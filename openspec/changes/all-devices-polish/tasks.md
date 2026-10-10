## 1. Device sign-in
- [x] 1.1 Implement opaque PKCE-bound native begin/callback/redemption and boundary tests.
- [x] 1.2 Wire Android Custom Tabs, iOS authentication session and Electron warm/cold return.
## 2. Private browser and packaging
- [x] 2.1 Improve private input semantics and phone keyboard, touch, rotation and safe areas; verify passkey limits.
- [x] 2.2 Implement DNS-pinned WebSocket relay if compatible with the existing boundary and verify it.
- [ ] 2.3 Match launch colours and produce desktop/mobile build artifacts.
## 3. Delivery
- [ ] 3.1 Run required checks, rendered browser/device proof and one cross-family floor review.
- [ ] 3.2 Sync specs, commit explicit paths, push and open a non-draft PR; record exact founder upload work.

WebAuthn forwarding is unavailable for arbitrary foreign sites: no related-origin/associated-domain authorization. Semantic Autofill hints are implemented; native password-manager selection still needs device acceptance. WebSocket relay is incompatible with the current bounded synchronous HTTP RPC: no long-lived channel, upgrade handling, subprotocol/cookie handshake propagation, or asynchronous lifetime/cancellation. No direct egress bypass added.
