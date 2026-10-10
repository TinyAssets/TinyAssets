## Design

Native sign-in begins with an app-held random PKCE verifier. A same-origin JSON endpoint records only its S256 challenge, client kind, random state and a ten-minute deadline in the existing owner-session database. The registered HTTPS /app callback stores the authorization code encrypted at rest and returns only an opaque reference. Redemption verifies the original app challenge and atomically consumes the code before the existing token exchange; interception, replay and foreign flows cannot establish a session. This grants ordinary app authentication, never the browser's interactive approval authority.

Android uses Capacitor Browser (Custom Tabs). iOS uses a small provider-neutral ASWebAuthenticationSession plugin. Electron hands URLs to the system browser and forwards only validated opaque returns from OS activation through a narrow preload callback. Pending flow state survives cold starts. Missing native browser support fails visibly instead of navigating the WebView.

The browser-login dependency is PR #4585, still open at start. Carry its commits as a dependency, without modifying its security contract. Password inputs receive correct HTML semantics and responsive keyboard/safe-area handling. Cross-origin passkeys and automatic credential selection cannot be claimed: RP-ID validation requires the relying site's cooperation (WebAuthn related-origin authorization, or OS associated domains). Do not forge origin or export authenticator secrets.

WebSocket support is acceptable only through the same DNS-pinned, public-HTTPS relay: no direct egress or unverified CONNECT tunnel. Preserve the existing OS isolation floor.

## Verification and release

Exercise token theft/replay/expiry, callback routing and cold return; rendered phone/desktop interaction, native compilation, Electron CDP, mobile/desktop suites, Ruff, structural guards, plugin build and hygiene. Existing signing pipelines restrict Android release signing to main; do not weaken that gate. Produce available artifacts and name required post-merge signed builds. Roll back by reverting this lane and rebuilding shells; expiring transport rows need no destructive migration.
