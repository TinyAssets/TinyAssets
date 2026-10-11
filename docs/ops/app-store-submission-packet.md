# TinyAssets App Store submission packet

Prepared 2026-09-03 from the shipping app, Apple Developer's current form
documentation, and the live Apple Developer account. Standing founder authorization
dated 2026-09-03 covers ordinary required Apple setup through submission and release,
subject to action-time confirmations required by product policy and the explicit
exceptions in `docs/host-actions.md`.

## Account and immutable identifiers

| Field | Value / action |
|---|---|
| Membership | Active; program resources, Team ID, and renewal date visible |
| Current agreements | Apple Developer Program License Agreement and Apple Developer Agreement accepted 2026-09-03; App Store Connect Terms of Service V100 (last updated 04 June 2018) accepted by the founder 2026-09-03 |
| Platform | iOS |
| App name | `TinyAssets` |
| Explicit bundle ID | `io.tinyassets.app` |
| Bundle ID description | `TinyAssets iOS` |
| App ID registration | Complete 2026-09-03; verified in the signed-in Apple Developer Identifiers list |
| App Store Connect record | Apple ID `6808434444`; iOS 1.0, build 3, is **Rejected / Unresolved Issues** for a Guideline 2.1 information request as of 2026-09-10; no binary defect was cited |
| SKU | `tinyassets-ios` |
| User access | Full Access for the existing Account Holder; the live form disables Limited Access and no additional user is selected |
| Primary language | English (U.S.) |
| Next version | `1.0.1` (new native build containing #4594) |
| Build | Use the numeric GitHub Actions run number |
| Primary category | Productivity |
| Secondary category | None |
| Price | Free; United States (USD) base and `$0.00` schedule; 148 non-EU storefronts enabled, including the United States; no in-app purchases |
| TestFlight | Internal group `Internal`; manual distribution for Xcode builds; Build 3 attached; Account Holder added and **Invited** on 2026-09-10 |

The explicit App ID, App Store Connect record, Apple Distribution certificate/private
key, App Store provisioning profile, Developer-role CI upload key, and all six protected
CI secrets are complete.

## Product-page metadata

- **Name (30 characters maximum):** `TinyAssets`
- **Subtitle (30 characters maximum):** `Your own AI universe`
- **Promotional text (170 characters maximum):**
  `A persistent AI universe that runs real, multi-step work on your own LLM — the same universe on web, phone, and your chatbot.`
- **Keywords (100 bytes maximum; each longer than two characters):**
  `assistant,agent,automation,workflow,universe,productivity,LLM,chat,projects,research`
- **Support URL:** `https://tinyassets.io/legal#contact`
- **Marketing URL:** `https://tinyassets.io`
- **Privacy Policy URL:** `https://tinyassets.io/legal/#privacy`
- **User Privacy Choices URL:** `https://tinyassets.io/account`
- **Copyright:** `2026 TinyAssets`

The support and privacy anchors are reachable on the public site. Privacy notice
v1.0 is published and effective 2026-09-09; unrelated terms and token disclosures
remain explicitly draft. App Store Connect saved this URL and published the four
declared data types before submission.

**Description:**

> TinyAssets gives you your own AI “universe” — a persistent, personified agent
> that lives in the cloud and runs real, multi-step work toward your goals,
> around the clock, whether you’re here or not.
>
> It’s the same universe everywhere. Open the app on your phone, the web app in
> a browser, or connect it to a chatbot like Claude or ChatGPT — sign in and it’s
> one continuous conversation and one shared memory across every surface.
>
> Your universe, your compute. You bring your own AI: connect a Claude or ChatGPT
> subscription, or add any API service right in the app. The platform never
> charges you for AI — it runs on the compute you give it.
>
> What you can do:
>
> • Chat with a universe that remembers you and grows into your projects.
> • Build multi-step automations and run them for real.
> • Add the channels and services you want through a user-composable node system.
> • Inspect runs and results so you can check the evidence.
>
> TinyAssets is open and honest by design: your data stays tied to your account,
> your AI credentials go straight to a secure vault, and the platform is
> transparent about what’s live.
>
> Get started in seconds — sign in and say hello to your universe.

The factual App Review Notes are saved: this is a Capacitor shell pinned to
`https://tinyassets.io/app`, not a general-purpose web browser; the native
shell has no purchase, subscription, upgrade, advertising, unrestricted-browsing,
or voice UI; and sign-in, provider connection, conversation, text-file
attachment, Account/Privacy navigation, and account deletion are the critical
review paths. Private reviewer-account details belong only in App Store Connect,
never in this repository.

## TestFlight copy and transmission state

- **Beta App Description:**
  `TinyAssets is a persistent AI universe for real multi-step work. This beta validates the installed iPhone shell, sign-in return, conversation continuity, file attachment, and recovery behavior before App Store submission.`
- **What to Test:**
  `Test sign-in and the return to TinyAssets, reconnect after force-quit, send a substantive message, confirm the same conversation on web, attach a small text file, open Account and Privacy, and recover after briefly disabling the network. Voice, purchases, and ads are not included. Report any blank screen, dead sign-in callback, lost conversation, inaccessible control, layout overflow, or recovery failure.`
- **Feedback Email:** the Account Holder contact is saved directly in App Store
  Connect and remains outside this repository.
- **Beta App Review Information:** the dedicated disposable reviewer and Account
  Holder contact details are saved in Apple's separate beta-review record. Never
  place credentials or personal contact details in this repository.

The en-US **What to Test** text above is saved on Build 3. After founder
reauthentication, the app-level beta description and verified marketing URL were
also saved, and Build 3 was selected for App Store Version 1.0. On 2026-09-10 the
Account Holder was added to the Internal group; after the beta review details were
completed and the Build 3 relationship was refreshed, Apple's API and UI both showed
the tester as **Invited** with one tester and one build. No external group or beta
review submission was created. Receipt:
`docs/audits/2026-09-10-ios-review-recovery-access.md`.

Internal testing comes first. External testing may trigger TestFlight App Review
and is a separate submission boundary.

## Accessibility Nutrition Label plan — voluntary, not published

Apple's label is voluntary as of 2026-09-03 but is expected to become mandatory.
Do not claim support from web checks alone: Apple requires every common task to
work with the named feature on the named device. The common-task set for iPhone is
first launch, sign-in, provider connection, conversation, text-file attachment,
Account/Privacy navigation, sign-out, and recovery after network interruption.

| Feature | Current basis | Evidence required before claiming support |
|---|---|---|
| VoiceOver | The shared client uses semantic controls and ARIA status regions. | Complete every common task on the signed iPhone build with VoiceOver. |
| Voice Control | Primary actions are ordinary labeled controls. | Complete every common task using Voice Control without touch. |
| Larger Text | The layout is responsive, but iOS Dynamic Type behavior is unproven in the WebView. | Test the largest accessibility text sizes with no clipped or unreachable controls. |
| Dark Interface | The app has a dark interface. | Verify every common-task screen, system prompt, and external-auth transition. |
| Differentiate Without Color Alone | Primary states use words or icons in addition to color. | Verify errors, connection status, disabled controls, and focus states on device. |
| Sufficient Contrast | Automated site sweeps are clean, but they do not prove the signed app flow. | Run contrast checks across every common-task state on device. |
| Reduced Motion | No support claim is staged. | Audit all animation and add/verify reduced-motion behavior before claiming. |
| Captions / Audio Descriptions | The voice-dark build contains no prerecorded audiovisual content. | Mark not applicable unless audiovisual content ships; re-evaluate if it does. |

Leave all accessibility responses unclaimed until the signed-device matrix is
complete. Publishing the label is a separate founder approval and cannot be
undone for a published device response.

## App Privacy — published 2026-09-09, first release with realtime voice dark

App Store Connect is saved as **Yes, we collect data from this app**. This table
is the published disclosure for build 3; each type is linked to the user's
identity, used only for the named purpose, and not used for tracking.

| Apple data type | What the app sends or stores | Purpose |
|---|---|---|
| Contact Info → Email Address | WorkOS sign-in email | App Functionality |
| Identifiers → User ID | WorkOS account identifier | App Functionality |
| User Content → Other User Content | Conversation text and user-selected text/code/document attachments | App Functionality |
| Other Data → Other Data Types | Provider credential or connection material deposited by the user into the secure vault | App Functionality |

### Verified code basis (2026-09-03)

- **The iOS shell uses the same hosted client and data path.**
  `mobile/capacitor.config.json` fixes the bundle to
  `https://tinyassets.io/app`; it does not add a separate native data plane.
- **Email address and user ID are identity data used for app functionality.**
  `tinyassets/auth/workos_provider.py` validates the WorkOS AuthKit token and binds
  its stable `sub` user ID to the TinyAssets identity. The deployed legal disclosure
  identifies the corresponding WorkOS sign-in email and user ID.
- **Conversation text is retained as user content.**
  `tinyassets/conversation_store.py` persists `speaker` and `content` per authenticated
  universe/session. `tinyassets/onboarding/app.html` sends typed content through
  `converse` and restores the recent conversation.
- **Selected files are user content, not ambient device collection.**
  `tinyassets/onboarding/app.html` exposes an explicit file picker, accepts text/code/
  document formats, rejects binary/oversized files, reads selected content client-side,
  and embeds it verbatim in the user's message.
- **Provider connection material is deposited intentionally and retained in a vault.**
  `tinyassets/onboarding/app.html` collects a provider credential only in the labelled
  secret field of a connect card the owner chooses to answer, or through the OpenAI device
  sign-in (`tinyassets/onboarding/openai_device.py`). `tinyassets/api/pending_requests.py`
  deposits it to the private vault and never records a secret field's value in the clear;
  `tinyassets/credential_vault.py` validates and atomically persists those records.
- **No native analytics, advertising, or purchase SDK is present.**
  `mobile/package.json` contains only Capacitor core/platform/app/browser/splash/status
  dependencies. The native shell has no analytics, advertising, StoreKit, payment, or
  crash-reporting dependency; voice remains dark for Build 3.

This evidence supports the published four-row disclosure. The founder's explicit
launch authorization covered its publication after the live notice and data-flow
review were complete.

For every row: **linked to identity: Yes**; **tracking: No**; advertising,
third-party advertising, and developer advertising purposes: **No**. The app has
no advertising SDK, analytics SDK, mobile crash-reporting SDK, location, contacts,
photos, camera, health, fitness, or payment collection. The native shell accepts
text-like attachments only. Payment-card data entered on a processor-hosted web
page is not collected by this app, and the native shell exposes no checkout.

WorkOS and the infrastructure provider process data to operate the service. A
user-connected AI provider receives user content at the user's direction. The
published questionnaire did not offer a separate Account Management purpose; all
four entries use App Functionality only.

### Conditional voice delta

The voice implementation is not on `main` and must stay dark in the first build
unless it is deliberately reconciled and passes physical-iPhone proof. If a
voice-enabled build is chosen, add **User Content → Audio Data**, conservatively
marked linked to identity, for App Functionality, with tracking **No**, unless a
fresh review of the production OpenAI retention configuration establishes that
Apple's collection definition excludes the transient audio. TinyAssets retains
canonical conversation text and does not persist raw audio; the client sends raw
microphone audio directly to OpenAI for live speech. Re-approve the entire label
after that production review.

## Age rating — saved 2026-09-03

The app is not Made for Kids, has no parental controls or age-assurance feature,
and the legal minimum age is 18. The WebView is pinned to TinyAssets; therefore
**Unrestricted Web Access: No**. Private AI conversation is not broad
distribution to other users; therefore **User-Generated Content: No**,
**Messaging and Chat: No**, **Social Media: No**, and **Advertising: No** under
Apple's current definitions.

Because responses come from a user-selected generative AI provider, use the
conservative content draft below rather than claiming that open-ended output can
never contain mature material:

- Infrequent or Mild: profanity/crude humor; horror/fear; alcohol/tobacco/drug
  references; medical/treatment information; health/wellness topics;
  mature/suggestive themes; sexual content/nudity; cartoon/fantasy violence;
  realistic violence; guns/other weapons.
- None: graphic sexual content/nudity; prolonged graphic or sadistic realistic
  violence; contests; loot boxes; simulated gambling; gambling.
- Age Categories and Override: **18+**, aligning the storefront with the
  product's documented 18+ legal minimum.

The exact live questionnaire was completed and saved from verified product
behavior. Apple displayed **18+** in 173 countries or regions and Brazil,
**19+** in Korea, and **17+** for operating systems earlier than version 26
(with Apple's displayed regional exceptions). The live questionnaire represented
Health/Wellness Topics as a boolean; it was answered **Yes**. All other answers
match the conservative content profile above.

## Export compliance

The shell contains no custom, proprietary, or non-standard cryptography. It uses
operating-system WebKit/network APIs for ordinary HTTPS/TLS. The generated
`Info.plist` now declares `ITSAppUsesNonExemptEncryption = false`, the value Apple
documents for apps that use no encryption or only encryption exempt from export
documentation. No export document is expected for this binary.

If App Store Connect asks whether the app uses encryption, follow the exact live
wording: disclose the ordinary OS-provided HTTPS/TLS path and select the branch
for exempt encryption. Do not interpret a broad “uses encryption” question as a
claim that the app sends plaintext. Any future custom crypto or new native SDK
requires a fresh determination.

## Screenshot asset manifest

The capture contract is checked in at
`app-store-assets/screenshot-manifest.json`. Capture one to ten PNG or JPEG images
with no alpha channel. Prefer one portrait 6.5-inch set at an Apple-accepted size
(`1242×2688` or `1284×2778`); the live App Store Connect form presented these
under the 6.5-inch display slot on 2026-09-03 and scales them for
smaller iPhones. Use an actual iPhone or iOS Simulator build, not resized Android
captures or a browser mockup.

The five planned scenes are: signed-in universe home, a substantive conversation,
an inspectable work result, provider connection, and account/privacy controls.
Use a dedicated clean review universe; remove personal content, credentials,
notifications, and debug UI. The icon asset is already complete at
`mobile/resources/icon.png`; the release build installs and validates it.

## TestFlight and physical-device smoke checklist

Run this against the exact signed artifact before any App Review submission:

1. Record source SHA, version, build number, IPA checksum, iOS version, and iPhone model.
2. Fresh-install; confirm the icon, splash, safe areas, portrait layout, keyboard, and no blank/offline shell.
3. Tap Sign in, complete authentication in the system-browser session, and verify it returns to TinyAssets with the reviewer session. Cancel once and retry; do not expect an embedded WebView login.
4. Force-quit/relaunch and background/foreground; confirm the session recovers without duplicating a universe.
5. Connect a test provider, return to chat, send a message, and receive the same canonical conversation on web.
6. Attach an allowed text file; verify it is shown accurately and no photo-library permission is requested.
7. Open Account and the privacy link; verify the deletion path is present. Exercise deletion only with a disposable test account.
8. Confirm the native build contains no plan, upgrade, Stripe checkout, ads, tracking prompt, or unrestricted browser.
9. Disable network, reopen, restore network, and verify the shell fails clearly and recovers.
10. Inspect App Store Connect/Xcode diagnostics for crashes, hangs, signing errors, and entitlement mismatches.
11. If voice is dark, confirm no Voice control is exposed and no microphone prompt occurs.
12. If voice is enabled, separately prove disclosure, allow/deny, start/stop,
    interruption, timeout, force-quit, page change, and backgrounding on a physical
    iPhone; confirm every microphone track is released and then re-approve App Privacy.

Any critical-flow failure holds the build. A web regression rolls back by
reverting the compatible `/app` deployment. A native-shell failure requires
a higher build/version; the old binary cannot be restored over an installed new
one, so the server must remain compatible with the last released shell.

## Exact portal sequence from the active membership

1. **Complete 2026-09-03:** Developer portal → Certificates, IDs & Profiles →
   Identifiers → **+** → App IDs → App → description `TinyAssets iOS` → explicit
   bundle ID `io.tinyassets.app` → Register. The signed-in Identifiers list showed
   the resulting name and exact bundle ID.
2. **Complete 2026-09-03:** the founder accepted App Store Connect Terms of Service
   V100 (last updated 04 June 2018), then confirmed creation of the TinyAssets app
   record. Apple ID `6808434444`; current review state is recorded in item 10.
3. **Complete 2026-09-09:** product-page copy, subtitle, Productivity category,
   copyright, support/marketing URLs, and manual release are saved. The four-type
   App Privacy disclosure is published for App Functionality, linked to identity,
   and no tracking; its live privacy URL is saved. Build 3 is attached to the
   `Internal` TestFlight group, selected
   for App Store Version 1.0, and its en-US **What to Test** and app-level beta
   description are saved. The group has manual Xcode-build distribution, zero
   testers, and no invitations were sent. A free price schedule is saved for 148
   enabled non-EU storefronts, including the United States.
4. **Complete 2026-09-03:** the founder approved credential creation. A 2048-bit
   Apple Distribution certificate/private-key pair was created and verified, then
   backed up as an encrypted P12. The active `TinyAssets App Store 2026` App Store
   profile binds `io.tinyassets.app` to that certificate. Both expire 2027-09-03.
5. **Complete 2026-09-03:** all six protected `app-store` environment secrets exist.
   The App Store Connect API request and internal-use attestation were approved; the
   `TinyAssets CI Upload` team key uses Apple's Developer role, the least role documented
   for build upload. Its one-time `.p8` and the profile are locally recoverable and verified.
6. **Partially complete 2026-09-03:** protected run `33824784381` built signed IPA
   1.0.0 (1); its checksum, bundle/version/build, embedded profile CMS signature,
   distribution entitlement, and certificate match were verified. Physical-device
   smoke remains.
7. **Complete 2026-09-03:** PR #2798 landed the independently reviewed Xcode 26.3
   selection and fail-closed SDK check. Protected run `33827279907` built and
   uploaded signed 1.0.0 (3); Apple reported no upload errors, processing completed,
   and Build 3 is **Ready to Submit**. The App Store Connect API then saved the en-US
   **What to Test** text and attached Build 3 to the empty `Internal` group. No App
   Review submission, external testing submission, tester invitation, or public
   release occurred. Receipts: `docs/audits/2026-09-03-ios-testflight-upload-receipt.md`
   and `docs/audits/2026-09-03-ios-testflight-preparation-receipt.md`.
8. **Partially complete 2026-09-03:** an authenticated App Store Connect session
   saved the beta app description and marketing URL, selected Build 3 for Version
   1.0, confirmed the free price schedule, saved factual App Review Notes, saved
   the 18+ age rating, and disabled untested Apple Silicon Mac and Apple Vision
   Pro distribution. Complete App
   Privacy, screenshots, review contact/sign-in details, storefront availability,
   and DSA status from verified evidence. Content Rights remains intentionally
   unset because Apple's truthful third-party-content answer includes a regional
   rights attestation. Interrupt only where a personal/legal fact cannot be
   established truthfully. Keep release mode manual until publication.
9. **Complete 2026-09-09:** the dedicated reviewer password was rotated and verified,
   the complete reviewer contact block was saved, the privacy notice and four-type
   disclosure were published, Content Rights was completed, and US-first availability
   was saved for 148 non-EU storefronts. The United States is **Available on App
   Release**; all 27 EU storefronts are **Not Available**; app-specific DSA status is
   non-trader for this initial non-EU release.
10. **Submitted 2026-09-09 02:32 PDT:** **Add for Review** succeeded and the resulting
    one-item submission was sent to Apple. Submission ID
    `5c6e4844-2ca2-438c-8aec-a189efb0ebb2`; release mode was changed to automatic
    after approval on 2026-09-10.
11. **Rejected for information 2026-09-09 17:56 PDT; verified 2026-09-10:** Apple
    reported **Guideline 2.1 - Information Needed - New App Submission**, not a crash
    or code defect. Apple requires a latest-iOS physical-device recording plus purpose,
    audience, access, service, regional, and regulated-content answers. Keep the
    submission intact and resubmit after the complete response is saved. Receipt:
    `docs/audits/2026-09-09-ios-app-review-submission-receipt.md`.

### Guideline 2.1 response packet

Updated **2026-10-10** for the **2026-10-11** founder goal. Apple's outstanding
request concerns **1.0 / build 3**. Record and resubmit the new **1.0.1** native
build containing #4594, not build 3 with only a refreshed web page.

**Build and account readiness:** `ios-release.yml` defaults to marketing version
1.0.1; `build_number` defaults to its numeric workflow run number. Use an unused
number. `npm ci`, `cap add ios`, and `cap sync ios` resolve the lockfile's local
`@tinyassets/system-auth` plugin; its Swift package exposes `SystemAuth.open` via
`ASWebAuthenticationSession`. `add_ios_scheme.py` registers `tinyassets`, enabling
the callback that carries the protected return secret. No Universal Links
entitlement or Electron build is required for this custom-scheme return.

GitHub settings checked 2026-10-10: **app-store** allows only `main` and requires
review by the founder (`Jonnyton`). Both signing and upload jobs use it; approve
both when prompted. Signing uses `APPLE_DISTRIBUTION_CERT_P12_B64`,
`APPLE_DISTRIBUTION_CERT_PASSWORD`, and `APPLE_PROVISIONING_PROFILE_B64`; optional
TestFlight upload uses `APP_STORE_CONNECT_API_KEY_ID`,
`APP_STORE_CONNECT_API_ISSUER_ID`, and `APP_STORE_CONNECT_API_KEY_B64`.
All six secret names are present. Their validity/expiry is checked by the signed
run, not inferred from their presence. No values were read or changed.
`upload_to_testflight=true` uploads only; it does not select a version or submit
App Review. Select 1.0.1 and its processed build in App Store Connect before resubmitting.

**Before recording:** use the dedicated reviewer account from App Store Connect's
App Review Information (credentials held privately; never paste them into this
file or chat). Verify the real connection path and a substantive reply on the
new installed build. If connection is needed, use the actual inline connection
flow; do not revive an interim review-only key or assume an old reply proves it.
Keep passwords, provider credentials, callback URLs and tokens out of the video.

**Physical-device shot list:** use the latest public iOS, show the actual device/OS
and build in the accompanying response, and capture these six scenes in one session:

1. Cold-launch TinyAssets from its icon; show the native splash and signed-out screen.
2. Tap **Sign in** and show the system-browser authentication session opening.
   Choose email/password and use the dedicated reviewer credentials in masked
   fields. Show the return into TinyAssets and the signed-in reviewer workspace.
3. Show the real inline connection flow if needed, with secret inputs masked.
   Confirm a usable model source without a subscription or payment step in the app.
4. Send `Give me a three-step plan to launch a small bakery's online orders, and keep it short.`
   Show the full substantive reply. Force-quit, relaunch, and show the same conversation.
5. Attach `docs/ops/app-store-review-sample.txt`; ask
   `What is the project, target storefront, and review deadline in this file?`
   and show an answer grounded in its contents.
6. Open Account and Privacy, including the visible account-deletion path; stop
   before confirming deletion of the reviewer account.

A cloud **real iPhone** is acceptable preparation for the requested physical-device
recording; a simulator is not. For BrowserStack App Live, verify TestFlight access,
the required iOS version, session recording/download and sample-file injection in
your plan before paying. Install the new build through TestFlight Internal; use
Chrome for the session recording. Sign out of your Apple ID/TestFlight when done.
Vendor instructions: [TestFlight](https://www.browserstack.com/docs/app-live/app-source/testflight),
[recording](https://www.browserstack.com/docs/app-live/session-debugging/record-session),
[file injection](https://www.browserstack.com/docs/app-live/media/file-injection).
Do not assume the free trial supports the complete recording.

**Written response — ready to paste after replacing the bracketed facts and verifying
all statements against the installed build:**

> Thank you for reviewing TinyAssets. We are responding to the Guideline 2.1
> information request with an updated version, 1.0.1, build [BUILD NUMBER]. The
> attached video [FILENAME OR ACCESSIBLE LINK] was recorded on a physical
> [IPHONE MODEL] running [IOS VERSION] and demonstrates launch, system-browser
> sign-in and return, connection, a real response, conversation persistence,
> text-file attachment, and Account/Privacy controls.
>
> Purpose, audience and value: TinyAssets is an 18+ productivity app for adults
> who want a private AI workspace for projects across phone and web. Users connect
> their own model source, give tasks in chat, and return to their conversation and work.
>
> Access: use the dedicated email and password provided in App Review Information.
> Tap Sign in; authentication opens in the iOS system-browser session and returns
> to TinyAssets. [VERIFIED CONNECTION STEPS: describe the exact steps needed with
> this reviewer account, or state the verified connected source is already usable.]
> We verified a substantive reply with this account on [DATE / BUILD]. No reviewer
> payment is required. The video demonstrates the same account and flow.
>
> External services: WorkOS AuthKit handles account authentication. Cloudflare
> provides the public HTTPS edge, TinyAssets' hosted service stores the private
> workspace, and the user's connected model provider supplies inference. The
> submitted app exposes no purchase or paid-content flow.
>
> Content: the recorded prompts, attachments and replies belong to the reviewer's
> private workspace. TinyAssets is a general-purpose productivity tool and does
> not ship a licensed third-party media catalog or a regulated-industry service.
> Users connect services they are authorized to use.
>
> Regions: [CONFIRM CURRENT STOREFRONTS: the previously saved scope was 148 non-EU
> storefronts including the United States, excluding the 27 EU storefronts.]
> [CONFIRM: functionality is consistent across the enabled storefronts.]

Do not paste the founder's implementation notes or unfilled brackets into App Review.
The older blanket claim of no sharing between users has been removed: a private
review workspace does not establish the absence of sharing features elsewhere.

## External gate that remains

Founder approvals, reviewer sign-in/connection proof and physical-iPhone recording
remain. Existing screenshots were captured for build 3; compare them with 1.0.1
and replace any that no longer represent the app. Attach the video and completed
written response to the unresolved request, select the new version/build, and
resubmit. Confirm the saved release mode (historically automatic after approval)
and the public product page's install availability before calling launch complete.
The [single founder checklist](../host-actions.md#apple-record-the-review-video-and-resubmit)
contains the ordered actions.

Official references checked 2026-09-03: [add a new app](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/),
[app information](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information/),
[platform version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/),
[app privacy](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/),
[age ratings](https://developer.apple.com/help/app-store-connect/reference/app-information/age-ratings-values-and-definitions/),
[screenshots](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications/),
[export compliance](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance/),
[TestFlight test information](https://developer.apple.com/help/app-store-connect/test-a-beta-version/provide-test-information/),
and [Accessibility Nutrition Labels](https://developer.apple.com/help/app-store-connect/manage-app-accessibility/overview-of-accessibility-nutrition-labels/).
