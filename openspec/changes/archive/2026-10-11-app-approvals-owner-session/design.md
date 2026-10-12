## Context and decisions

Root cause: the edge includes Set-Cookie in FORBIDDEN_RESPONSE_HEADERS. begin() sets a valid __Host-ta-owner-login cookie, but the public response drops it; callback() therefore refuses at the cookie/flow lookup before token exchange. Preserve the explicit app cookie allowlist only on /app routes, with Secure, HttpOnly, expected Path, SameSite and no Domain. Keep Access and unknown cookies stripped, and preserve separate Set-Cookie fields (including expiry commas). Emit structured refusal_reason/upstream_status without credentials, state, URLs or exception text. The real-routing regression covers begin, fake PKCE IdP, callback and unrecorded dismissal.

The browser owns interactive proof. A native bearer may create a short-lived opaque navigation reference bound to its owner and request, but cannot establish proof or record a decision. Store this transport row in the existing protected owner-session database. The browser launch validates the owner cookie and obtains normal browser app renewal through server PKCE on first use. Reuse the full app and its existing request sheet rather than duplicate decision UI. Both browser bearer and protected owner cookie retain their existing checks.

The callback URI remains the registered HTTPS /app URI. Secure, HttpOnly, Path=/, no Domain, SameSite=Lax cookies accompany its top-level GET callback. The stateless frontend must proxy oa_ callbacks. A separate return cookie remembers only the opaque handoff; callback validates its owner before returning to the sheet.

Android returns through its existing package-qualified tinyassets://auth intent, including debug package selection; iOS uses the registered scheme. Electron opens the default browser and registers a narrow completion scheme handler. Only an opaque reference returns. No returned data authorizes a mutation; list refresh reads the server. Cancellation, duplicate return, cold start, expiry and account changes cannot approve anything.

## Verification

Reproduce missing WebView proof, exercise browser sign-in and reuse, deny foreign-owner/expired references, preserve pending-request decision guards and unrecorded dismiss. Run requested Python, web, Linux, structural, plugin and hygiene checks. Cross-family review covers this authority boundary. Native build requirements and any unavailable live evidence belong in the PR report.
