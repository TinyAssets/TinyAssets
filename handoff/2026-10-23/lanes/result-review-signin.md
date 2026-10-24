Reading the remaining call sites (ConnectOAuth and the source-connect paths) to see whether moving them from same-WebView navigation to the system browser breaks their return.

Checking whether the new native/desktop "Sign in to approve" route (now a full app re-login) can switch accounts under a pending flow.

**Findings** (PR #5231, `git diff origin/main...HEAD`)

**1. Medium (confirmed): on the phone and desktop apps, "Sign in to approve" now starts a full app re-login. That login can never give the owner proof the link is meant to get, and it can switch the app to a different account.**
- `tinyassets/onboarding/app/main.js:77-79`: `openSignIn` sends any same-origin `/app/owner-sign-in` link to `beginSignIn()` when running natively or in the desktop shell.
- `main.js:10190-10197`: a document-wide capture click handler sends every such link the same way. That covers the soul, memory, inline-connect and ConnectOAuth links (`main.js:5632`, `5947`, `6856`, `7633`).
- `beginSignIn` on native/desktop runs the app-login flow (`/app/native-sign-in` → `/app/token`). That exchange calls `revoke(request.cookies.get(COOKIE))` at `onboarding/__init__.py:457-458` and never creates an `owner_sessions` cookie.
- But `interactive_approval_required` can only be cleared by that cookie. See `require()` at `owner_sessions.py:124-147` and the router admission at `owner_router.py:141-154`.
- Result: clicking the link wipes any owner proof that was there and creates none, so the user is stuck in a loop. The old `target=_blank` link at least ran the real owner sign-in.
- Account switching: the native flow uses `prompt=login`, so the user can pick account B in the system browser. `resumeNativeSignIn` (`main.js:158-160`) then installs B's token without calling `MCP.endLogin()`, so `_loginEpoch` is not bumped. Any epoch-guarded work still pending from account A carries on under B's token. Server-side owner checks (`take()`, the approval-handoff `load(ref, owner)`) stop data crossing between accounts, so this is a correctness problem rather than a security break.
- Errors from this path go to `#signin-notice` and `#btn-signin` (`main.js:118-119`), which are hidden while signed in, so failures are invisible.
- Whether the old link actually worked on Android (WebView opening `_blank` in the same window) is plausible but I didn't verify it. On desktop it was already useless, because Electron sends `_blank` to the system browser.
- Fix: on native/desktop, route `/app/owner-sign-in` to the owner-proof flow, not app login. Or, if owner proof in the WebView is deliberately out of scope there, don't show the link on those surfaces.

**2. Low: a web sign-in the user abandons is never cancelled.**
- The `this.popup.closed` → `inline_cancel` branch was removed (`main.js:~5655`), and same-tab navigation has nothing to replace it.
- If the user presses Back from the provider page, the restored card shows Connect again with Cancel hidden. The old flow stays `waiting` until it expires and Retry starts a second one.
- The message itself is kept: the in-flight record is in localStorage and `hold()` restores it at boot (`main.js:9951-9956`).

**3. Low (accepted): desktop/iOS custom schemes can be claimed by another app.**
- `completion_url` hands `tinyassets://auth?completion=<flow>` and `tinyassets-desktop://…` to any app that registers those schemes. This now applies to inline flows too (`inline_model_connect.py:179-183`).
- It carries only the flow handle. `take()` is owner-bound and the callback needs `browser_hash`, so a hijacking app can't use it. Android's `intent://` with a package name is fine.

**4. Info (existing problem, not a regression): two desktop flows can't finish.**
- `HostedModelConnect.begin` (`main.js:5839/5855`) and `ConnectOAuth.begin` (`main.js:7606/7629`) are not blocked in the desktop shell.
- `openSignIn` → `openExternal` lands the provider redirect in the system browser. The verifier lives in Electron's sessionStorage, so `takeCallback` fails there.
- Before this PR, Electron's tinyassets.io-only allow-list (`desktop-app/config.js:36`) sent the same navigation to the system browser anyway, so nothing got worse. But the PR's "desktop: system browser with app-scheme return" claim doesn't hold for these two flows.

**Checked and holding:**
- **Open redirect:** none. The global handler only takes same-origin `/app/owner-sign-in`, and the owner sign-in redirect is fixed to `/app`.
- **Return URLs:** the web inline callback's 303 always goes to the constant `"/app"`.
- **PKCE and state:** unchanged.
- **Browser and account binding:** the inline launch requires an owner session matching the row owner. The callback requires the `__Host-` browser cookie, so a second browser profile can't complete it. `take()` is owner-filtered.
- **Codes in URLs and referrers:** `Referrer-Policy: no-referrer`. The new 303 actually takes the code out of the visible URL and history, which is an improvement.
- **Other entry points:** every popup, `location.assign` and `openExternal` sign-in path now goes through `openSignIn`. What's left is billing checkout (`5344`), account deletion (`7306`), legal links (`6772`), the Play listing (`4621`) and the approval return (`8452`), none of which are sign-ins.

VERDICT: DISAGREE_EVIDENCE
