## 1. Device sign-in
- [x] 1.1 Implement opaque PKCE-bound native begin/callback/redemption and boundary tests.
- [x] 1.2 Wire Android Custom Tabs, iOS authentication session and Electron warm/cold return.
## 2. Private browser and packaging
- [x] 2.1 Improve private input semantics and phone keyboard, touch, rotation and safe areas; verify passkey limits.
- [x] 2.2 Implement DNS-pinned WebSocket relay if compatible with the existing boundary and verify it.
- [x] 2.3 Match launch colours and produce desktop/mobile build artifacts.
## 3. Delivery
- [ ] 3.1 Run required checks, rendered browser/device proof and one cross-family floor review.
- [x] 3.2 Sync specs, commit explicit paths, push and open a non-draft PR; record exact founder upload work.

WebAuthn forwarding is unavailable for arbitrary foreign sites: no related-origin/associated-domain authorization. OTP hints and input semantics are implemented; foreign-site password-manager forwarding is not implemented because the app cannot claim the foreign origin. WebSocket relay is incompatible with the current bounded synchronous HTTP RPC: no long-lived channel, upgrade handling, subprotocol/cookie handshake propagation, or asynchronous lifetime/cancellation. No direct egress bypass added.

Desktop artifacts and unsigned mobile compilation are complete. Signed store artifacts require main and the protected app-store environment. Task 3.1 remains open for post-deployment physical-device/live OAuth and app-agent acceptance; local checks and the required cross-family review are complete.
