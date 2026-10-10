## Decisions

Founder continuation (2026-10-10, #4583) replaces manual completion: the single
origin-labelled **Sign in** action opens directly in owner takeover. A supplied
read-only authenticated-page selector is checked on the connected origin before
sealing, and again in a fresh cell before reuse. Merely leaving a password form
is not proof of login. Completion resolves the existing durable request/wake;
no additional confirmation or chat message. Unknown write outcomes never replay.
Connection identity is owner/home/site/account label; ambiguous accounts are
returned for selection. Expiry returns the same connection's pending request.

Protected filling binds an opaque, one-use field handle to the exact document,
frame and origin shown to the owner. Navigation or document replacement revokes
that handle. Credentials travel only through the interactive owner endpoint,
never through ta. The sheet shows account and origin and says **session
remembered; password not stored**. The protected password field provides private
input; it cannot request another site's saved credentials or passkeys using
TinyAssets' origin. WebAuthn requires
the site's RP ID/origin; iOS associated-domain autofill requires site cooperation.
Universal native autofill/passkey forwarding is an explicit unbuilt acceptance
item, not something a screenshot stream provides. Local-browser attach remains
out of scope; the cloud path needs zero owner hosts online.

1. **Placement.** One fixed browser cell in the live bounded launcher runs as the permanent owner UID/GID with private PID/mount/network namespaces, no owner tree or host sockets and ephemeral profiles. Chromium sandbox stays enabled. Only the daemon holds its channel. No host computer is needed.
2. **Interaction.** `ta browser` requests the official HTTPS login URL resolved by an editable agent skill, without a platform registry. Connect opens a protected first-party sheet; image frames and pointer/keyboard events drive the actual browser, including redirects/popups. Each request requires the current interactive owner cookie, exact origin, owner/home and capture session. Bearer, custom UI and agent calls cannot fetch frames or send login input. Native shells use this same protected web surface. Hardware passkey forwarding is not claimed by the MVP.
3. **Custody.** Versioned broker browser-vault rows are keyed by owner/home/random connection ID. Metadata holds origin, status and revision. AES-GCM seals cookies/local storage/IndexedDB with owner/home/ID/revision associated data; the key stays in the broker tree. Plaintext exists only in trusted daemon/cell memory, never agent files, transcripts, exception strings or traces. No raw state, CDP, evaluate, network inspection or profile export capability exists.
4. **Use.** Verified authenticated state closes capture and seals state automatically. Later `ta browser` calls start fresh cells with saved state. Structured navigate/click/fill/press/read return bounded rendered text marked untrusted. Login/challenge pages and password/OTP fields require owner takeover; known session values are suppressed. Unknown action outcomes are never automatically replayed. Existing `ta` permissions and activity Stop apply. Detected site/network blocks are terminal blocked states until the owner explicitly retries.
5. **Network.** The cell has no network route. Browser HTTP requests cross a bounded trusted relay that validates each destination, pins public DNS answers, and denies private/link-local/metadata IPs, URL credentials, unsupported schemes/ports. Redirects are checked independently. Login allows public identity-provider origins; agent navigation stays at the connected origin. Disable service workers/downloads. WebSocket-only sites are an explicit MVP limitation. No production private-network bypass.
6. **Lifecycle.** Serialize capture/action/commit/revoke per connection. Revisions fence stale completion. Revocation tombstones the row and erases ciphertext before returning. Captures expire after ten minutes and stop on invalid owner session or cancellation. Restart loses captures, not saved sessions. Authentication expiry returns reconnect on the same card. CAPTCHA/MFA remain human work. Account deletion erases browser custody through broker account erasure.

## Risks / Trade-offs

Sites can block remote browsers. Device-bound passkeys, native authenticator forwarding, smooth mobile typing and WebSocket sites require further work. Report these limitations honestly. Revocation removes local access, not the upstream account or completed actions.

## Verification

Real Chromium in the production image: password and distinct-origin redirect login through frames/input, fresh-cell later `ta` action, revoke, foreign owner, capture observation refusal, secret suppression and egress denial. Run touched tests, Linux oracle, Ruff, structural guards, plugin build and hygiene; one cross-family floor review after PR creation.

Review disposition (Claude, 2026-10-10): F1/F2 DISAGREE_EVIDENCE — both
`agent` and `owner_action`, and the HTTP `perform`, hold the same existing
`owner_control.control(home)` kernel lock throughout each cell exchange/action.
Concurrent readers cannot interleave; revoke cannot return success while an
older action holds that lock (it instead returns a retryable busy failure).
F3/F4 AGREE — revalidate retained state after an abandoned capture and publish
only fully written, fsynced vault keys with an atomic no-replace link. Native
autofill remains an explicit gap. Generated browser-proof trigger additions
only include marked test files; they do not weaken or skip any gate.
