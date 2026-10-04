# Mobile launch handoff — where this stands, 2026-09-29

Written so a new session can pick this up cold. The goal is unchanged: **users able
to download the app from Google Play and the Apple App Store.**

Runbooks stay where they are — `docs/ops/google-play-launch.md` and
`docs/ops/app-store-launch.md` — and founder-only items stay in
`docs/host-actions.md`. This file is the map between them. The dated sections below
this one are history; where they disagree with this top section, this section wins.

---

## The one-line status

**Google Play: build 4 (1.0.3) was approved on 2026-09-08 and is live on the closed
(Alpha) track, but the 12-tester, 14-day clock has not started.**

> **Superseded by the app-URL move (2026-09-30).** The app moved to
> `https://tinyassets.io/app` with no redirect from `/mcp/app`, and `server.url` is
> compiled into the shell — so installed `4 (1.0.3)` opens a dead path. **Build and
> upload `5 (1.0.4)` before inviting any tester**, or the opt-in clock starts on a
> build that cannot load. `mobile/android-release.json` already carries the bump.
>
> **Do not build the `5 (1.0.4)` bundle yet.** One native update should reach Play,
> not two: the owner-notify lane is adding `@capacitor/push-notifications` + FCM
> config under `mobile/`, and that has to be in the same bundle. Sequence:
> 1. this URL change lands (web + server + the config bump) — done independently,
> 2. owner-notify's native part lands,
> 3. **then** one `Android release AAB` run builds and signs `5 (1.0.4)` with both.
>
> `versionCode` stays at **5** through both lanes — a second bump would split one
> user-visible update into two. Whoever builds last owns step 3.

As of the last record
(2026-09-09), no tester had opted in, not even the founder. **Apple: iOS 1.0, build 3,
is Rejected / Unresolved Issues under Guideline 2.1, "Information Needed"** (2026-09-10).
Because the account has little review history, Apple wants a physical-iPhone recording
plus written answers. The answers are drafted; the recording is the founder's. Apple did
not cite 4.2 or 3.1.1. Release is set to go out automatically once approved.

Rechecked 2026-09-29 on `origin/main` `4ad5deb5`, after the move to the `TinyAssets`
org. `ios-build` and `android-build` were green on 2026-09-27. The four
`ANDROID_UPLOAD_*` repository secrets and the eight `app-store` environment secrets
are present. `/app` serves the in-app **Delete my account** flow. The Upgrade (Stripe)
button stays hidden in both native shells (`NATIVE` guard in `renderPlan`), so 3.1.1
needs no in-app purchase.

## What is left, in order

Founder rows, step by step: `docs/host-actions.md` → "Store launch: three founder steps
(2026-10-04)".

**Apple**

1. Founder backs up the Apple key and completes one real Continue with Apple
   sign-in. On 2026-10-04, `python scripts/authkit_login_parity_probe.py` passed
   with `providers: apple, google`; it verifies availability, not completed login.
2. Agent verifies the real inline connect flow with the reviewer account and confirms a
   substantive reply before recording. The founder rejected an interim review key;
   the inline-connect proposal is not evidence of a working deployed flow.
3. Founder records the six-step physical-iPhone video.
4. Agent attaches the video and written answers (`app-store-submission-packet.md`,
   "Guideline 2.1 response packet") and resubmits build 3, only on the lead's explicit
   go. No rebuild is needed: sign-in is the hosted AuthKit page.
5. On approval the release goes out automatically (`AFTER_APPROVAL`). Verify the US
   product page offers the install before calling the launch done.

**Google Play**

1. Founder opts in and recruits 15–18 testers, so that 12 stay opted in.
2. 14 wall-clock days with at least 12 continuously opted in.
3. Apply for production access (Play Console dashboard). Google reviews it separately.
4. Promote build 4 (or a newer version code) to Production and roll out.

If 12 testers cannot be found, the only rule-level alternative is an **organisation**
Play developer account: the testing requirement applies only to personal accounts
created after 2023-11-13. That route needs a D-U-N-S number, a new $25 account and an app
transfer, so it is slower than 14 days unless recruiting stalls.

## Store-rule risks, checked 2026-09-29

Sources: Apple App Review Guidelines, last updated 2026-06-08; Play Help article 14151465.

- **4.8 Login Services: provider availability verified 2026-10-04.** The live
  parity probe reports Apple and Google. Portal setup is reported complete;
  the founder still needs to finish a real Apple sign-in. This availability
  check alone does not establish end-to-end login or App Review acceptance.
- **4.2 Minimum Functionality — watched, not acted on.** The shell loads the hosted
  SPA. Apple did not cite 4.2 on the first review. Answer 2.1 first, and build native
  value only if 4.2 is actually raised
  (`docs/concerns/2026-09-03-ios-web-wrapper-app-review-risk.md`).
- **3.1.1 / 3.1.3 In-App Purchase — satisfied.** No purchase UI is reachable in either
  shell. External purchase links are now allowed in the US storefront, but we don't need
  one.
- **5.1.1(v) account deletion (Apple) and Play's account-deletion policy — satisfied**
  in the app and at `https://tinyassets.io/account`.

---

## Google Play

### Done

- App created (`io.tinyassets.app`, Play app id `4975777632183072073`). Listing,
  graphics, privacy policy URL, in-app privacy link, account deletion, and the
  Ads / Government / Financial / Health declarations are all filled.
- **Content rating** submitted 2026-09-02 — Everyone / PEGI 3 / USK 0 / ClassInd L.
- **Data safety** corrected to list password + OAuth account creation, saved, and
  Actioned; it has not been sent for review.
- **Advertising ID** saved as **No** after the shipped artifact, current merged
  manifest, and locked dependencies all verified that no advertising ID is used. It
  is Actioned in Play but has not been sent for review.
- WorkOS production now supports Email + Password with its recommended strong policy.
  The founder/admin remains `jonathan.m.farnsworth@gmail.com`. The dedicated reviewer
  identity is `play-review@tinyassets.io` (`user_01M1N3BFV6N1V1C9PP1NEWCCHP`), routed
  through the TinyAssets `info@tinyassets.io` mailbox to the founder's controlled Gmail.
  WorkOS shows it Verified + Active, with no organization or connected accounts and two
  successful password sign-ins. Both sign-ins reached the same isolated empty universe.
  Play **Sign in details** is saved and Actioned with that account; the mistaken Simkal
  plus-alias reviewer was deleted after the replacement was proven.
- **A signed bundle is live on the internal testing track**: release `1 (1.0)`,
  published 2026-09-03 11:10, 3.1 MB. Corrected release `2 (1.0.1)` is also fully
  prepared from merged commit `bf432f1b2dbe`, uploaded, and accepted as API 24+ /
  target SDK 36. Play now shows it **Active** and **Available to internal testers**
  (published 2026-09-03 21:42 PT).
  Opt-in link: <https://play.google.com/apps/internaltest/4701716760893982267>
- App content now shows **10 actioned declarations** and **1 needing attention**.

### The wall, stated precisely

Google Play had three gates stacked in a fixed order. All three are now complete:

1. **Sign in details — done 2026-09-03.** The durable TinyAssets alias, isolated WorkOS
   user, two clean password sign-ins, and saved Play instruction set are all verified.
   The optional Google/partner-device feedback switch was left off. Nothing was sent
   for review.
2. **Target audience and content — done 2026-09-03.** Saved as **18 and over** and
   Actioned; not sent for review.
3. **Data safety — done 2026-09-03.** The verified data-flow answers are Actioned,
   including both password and OAuth account creation; not sent for review.

Separately, and independent of those three: a personal developer account must run a
**closed test with at least 12 testers, opted in continuously for 14 days**, before it
may even *apply* for production access. **Open testing is also locked behind production
access** — the Console says so directly, so there is no public route that skips the
closed test.

That 14-day clock is wall-clock. It is the real long pole on Play, and nothing an
agent does shortens it.

### What the next session should actually do (2026-09-03, superseded by the top section)

- Install internal release `2 (1.0.1)` from Play; do not rebuild or re-upload it.
  Signed AAB SHA-256:
  `135F006AA5072EF09EABE511800B12EF0ADDBBBDB0753859401F6C566B69EF1E`.
  Play already reports it available to internal testers; the phone can reconnect for
  the clean notification recording.
- The only App content declaration needing attention is **Foreground service
  permissions**. Play requires a public demonstration-video link; no verified redacted
  phone recording exists yet. The exact shot list is in `docs/host-actions.md`. Do not
  submit or send anything for review before that evidence exists.
- **Do not** re-derive the `gh secret set` situation. It is denied by the harness
  classifier (tested four times, including piping from a file so no value entered
  argv). Do not route around it with `gh api` — that bypasses the intent of the
  denial. It is also **not** on the critical path any more: see below.

---

## Building a bundle without CI

`mobile/container/` holds a Dockerfile and two scripts that mirror
`android-release.yml` and produce a signed `.aab` in about five minutes, needing **no
GitHub secret at all**. Read `mobile/container/README.md`. This is how the current
Play build was made.

The correction worth carrying forward: the four `ANDROID_UPLOAD_*` secrets were
written up in this repo as *the* blocker. They were not. The goal was a signed bundle
in the Console; GitHub secrets are one route to that, and a denied route is not the
same as a blocked goal. Setting them is still worth doing — it turns each future
release into one `gh workflow run` — but nothing waits on it.

---

## Apple App Store

**Checkpoint history (2026-09-09 02:32 PDT; the current state is the top section):** Apple accepted submission
`5c6e4844-2ca2-438c-8aec-a189efb0ebb2` for iOS 1.0 / build 1.0.0 (3).
App Store Connect shows **Waiting for Review**. On approval, select **Release This
Version** and verify that the United States product page offers the install.
Receipt: `docs/audits/2026-09-09-ios-app-review-submission-receipt.md`.

**Apple Developer Program membership is active.** Checked 2026-09-03, not inferred from
the purchase receipt: the signed-in portal exposes App Store Connect and Certificates,
IDs & Profiles, and shows a Team ID plus a 2027 renewal date. The Apple Developer Program
License Agreement and Apple Developer Agreement both show accepted on 2026-09-03. The
explicit App ID
`io.tinyassets.app` was registered and verified on 2026-09-03 in the signed-in Apple
Developer browser: the Identifiers list showed `TinyAssets iOS` and the exact bundle ID.
The founder accepted App Store Connect Terms of Service V100 (last updated 04 June
2018) on 2026-09-03. The TinyAssets App Store Connect record now exists (Apple ID
`6808434444`), with iOS 1.0 now **Waiting for Review**. Product metadata and
manual release are saved; the four-type privacy disclosure and live policy URL are
published. An empty `Internal` TestFlight group exists with automatic
distribution off, 0 testers, and 0 builds. An Apple Distribution certificate/private
key, matching App Store profile, and Developer-role CI upload key now exist; all six
required values are protected GitHub environment secrets. Signed IPA 1.0.0 (1) is
verified. Apple's upload rejected build 2 only because the runner defaulted to Xcode
16.4/iOS 18.5 while iOS SDK 26 is mandatory; exact reviewed revision `6ccb3d24`
selects Xcode 26.3 and has a Claude Opus **AGREE** receipt. The submission and
remaining release step are in `docs/ops/app-store-submission-packet.md`.

What is ready, stated precisely — the gap here is wider than "just enrol":

- The Capacitor iOS platform, the `tinyassets://` URL-scheme patch, and the listing
  and App-Privacy copy in `docs/ops/app-store-launch.md`. The complete Apple-specific
  metadata, privacy/age/export drafts, screenshot manifest, TestFlight copy,
  accessibility device matrix, smoke checklist, and portal sequence are in
  `docs/ops/app-store-submission-packet.md`.
- The generated `Info.plist` now includes the exact microphone purpose string for
  the incoming realtime-voice slice. Voice remains dark: a physical-iPhone run must
  prove background/stop release, and App Privacy must be re-evaluated against the
  provider retention configuration before a voice-enabled build can be submitted.
- The generated `Info.plist` declares `ITSAppUsesNonExemptEncryption = false`; the
  current shell uses only exempt OS-provided HTTPS/TLS and no custom cryptography.
- `ios-build.yml` compiles green on `macos-15` runners, including after the Capacitor 8
  upgrade. `ios-release.yml` now adds the manual, fail-closed signed archive: a verified
  IPA artifact by default and an explicit opt-in App Store Connect/TestFlight upload.
  It never submits for App Review. The build installs the committed TinyAssets icon and
  splash instead of Capacitor's placeholders.
- Required iPhone and iPad App Store screenshots were captured from Build 3's
  exact source, validated, visually inspected, and saved in App Store Connect.
- **A Mac is still not needed** — both iOS workflows run on `macos-15` CI runners.
- **App Review risk remains:** the installed shell loads the remotely served client.
  Apple's current Guideline 4.2 may treat that as a repackaged website even though the
  product has real utility and native OAuth return. The evidence and pre-submission
  decision are recorded in
  `docs/concerns/2026-09-03-ios-web-wrapper-app-review-risk.md`.
- **Privacy is published:** PR #3616 separated the published privacy v1.0 status
  from the unrelated draft terms, deployment run `34334601760` published it, and
  App Store Connect shows the four declared data types as published.

Local evidence, Windows checkout, 2026-09-03:

- `npm ci --ignore-scripts --no-audit --no-fund && npx --no-install cap add ios &&
  npx --no-install cap sync ios && python scripts/add_ios_scheme.py && python
  scripts/add_ios_assets.py` — passed
  against Capacitor 8.5.1's generated Xcode project. The generated icon's SHA-256
  matched `resources/icon.png`; all three splash hashes matched `resources/splash.png`.
- `python -m pytest -q tests/test_mobile_ios_release.py tests/test_onboarding_app.py
  -k "mobile_ios_release or android_shell or app_itself_links"` — 13 passed, 83
  deselected. The two account-deletion/native checkout guards also passed directly.
- `actionlint .github/workflows/ios-build.yml .github/workflows/ios-release.yml` —
  passed (actionlint container on Windows).
- `npm audit --audit-level=high` — passed after removing unused
  `@capacitor/assets`; three moderate `uuid` findings remain and are recorded in
  `docs/concerns/2026-09-03-capacitor-cli-uuid-advisory.md`.
- `python -m pytest -q tests/test_mobile_ios_release.py` — 15 passed after adding
  automated App Store metadata byte-limit, keyword, URL, and least-access guards.
- PR #2798 exact-head `build-ios` on GitHub's `macos-15` runner — passed 2026-09-03
  after the App ID registration update.

Membership, the explicit App ID, the App Store Connect record, signing/profile/API
credentials, and all six protected secrets are active. The release workflow produced
and verified a signed IPA. TestFlight upload is blocked only by Apple's new iOS 26 SDK
floor and the repository's required cross-family review; the focused local fix selects
Xcode 26.3 and fails closed if the SDK is not 26.x.

---

## Traps already paid for — do not rediscover these

- **Try a verification step before filing it as a founder action.** The Play contact
  phone was written up here and in `docs/host-actions.md` as *"BLOCKS EVERYTHING"*, on
  the reasoning that Google would send a code only the founder could read. It sent no
  code: verifying took one click in the Console. The row then outlived its own truth by
  a day, still telling the founder the launch was stuck behind them while the app was
  already on internal testing. Both a wrong blocker and a stale one cost more than the
  step would have.
- **When a launch state changes, the table at the *top* of a doc is what rots.** §0 of
  the Play runbook contradicted its own status checklist 350 lines below, because the
  checklist got updated and the founder-facing summary did not. Update both or neither.

- **Play requires `targetSdk` 36 for new apps since 2026-08-31.** Capacitor 6 pins 34,
  and a low target is **rejected at upload**, not warned about. We are on Capacitor 8
  (which also forces node 22 and **JDK 21** — Capacitor 8 compiles its Java at
  source/target 21). The August cutover repeats annually; re-check
  <https://developer.android.com/google/play/requirements/target-sdk> before each release.
- **`cap sync` preserves a stale `android/variables.gradle`.** A checkout that generated
  the platform under Capacitor 6 keeps minSdk 22 / SDK 34 after the dependency bump and
  builds a bundle Play rejects *while looking perfectly healthy*. Delete `mobile/android`
  before regenerating. CI never sees this because it starts clean.
- **Capacitor 8's iOS side is Swift Package Manager, not CocoaPods.** `cap sync ios`
  writes `Package.swift` and never runs `pod install`, so there is no `App.xcworkspace`.
  Build `App.xcodeproj`.
- **`~/.tinyassets/android/upload-keystore.env` has CRLF endings.** Sourcing it leaves a
  trailing `\r` on every value, and keytool then reports *"Keystore was tampered with, or
  password was incorrect"* — which blames the keystore for a line-ending bug. `tr -d '\r'`
  first. The same trap applies to `gh secret set`.
- **Git Bash mangles `origin/main:path`** into `origin\main;path`, so `git show` fails and
  a piped `grep -c` returns a confident `0`. Set `MSYS_NO_PATHCONV=1`. This produced one
  false "main is broken" alarm in the session that wrote this file.
- **Auto-merge can land a stale head.** #2784 squash-merged between two of my pushes, so
  two commits silently did not land. After any auto-merge, diff your branch against main
  and check what actually arrived.

---

## Open PRs from this work

- **#2784** — merged. Capacitor 8, targetSdk 36, JDK 21, node 22, iOS SPM fix.
- **#2786** — the live-status docs and the iOS runbook correction.
