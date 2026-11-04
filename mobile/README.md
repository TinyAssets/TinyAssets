# TinyAssets mobile app (Capacitor: Android + iOS)

A native Android/iOS app that wraps the live TinyAssets web app. It is a thin,
maintainable **Capacitor** shell whose WebView loads `https://tinyassets.io/app`
(configured in `capacitor.config.json` → `server.url`). Because that page, the
`/mcp` API, and the AuthKit sign-in all live on the same origin (`tinyassets.io`),
the native shell handles the WorkOS OAuth return through `tinyassets://auth`.
Web-app changes ship instantly (no store resubmit); the store build only
changes when the native shell, icon, or config change.

> **Architecture:** app shell = Capacitor + a few native plugins (splash, status
> bar, app lifecycle). Product logic (sign-in, connect subscription, chat) is the
> web app, reused verbatim on both platforms.

## What I (the app author) can build vs. what needs you

**Built here:** the Capacitor project, config, native-plugin set, loading/offline
fallback page, Android debug/release lanes, iOS compile/release lanes, and store
runbooks. Android produces an APK/AAB; iOS produces an unsigned simulator build
on every change and a signed IPA when the Apple signing assets are provided.

**Needs you (identity / accounts / payment — I can't do these):**
- Store accounts and their agreements/payments.
- Signing identities that the account owner creates and keeps secret.
- Legal/privacy declarations and the final submissions for review.

Current launch state and exact owner-only asks live in
`docs/ops/mobile-launch-handoff.md` and `docs/host-actions.md`.

## Prerequisites (on your build machine)

- **Node.js 22+** and npm (`@capacitor/cli@8` declares `engines.node >= 22`)
- **Android Studio** (bundles the Android SDK + platform tools) — https://developer.android.com/studio
- **JDK 21** (Android Studio bundles one; or install Temurin 21). Capacitor 8
  compiles its Java at source/target 21 — JDK 17 fails with
  `error: invalid source release: 21`.
- Set `ANDROID_HOME` / accept SDK licenses (`sdkmanager --licenses`)

## First-time setup

> **Upgrading an existing checkout?** Delete `mobile/android` first. It is
> gitignored generated output, and `cap sync` *preserves* `android/variables.gradle`
> — so a project generated under Capacitor 6 silently keeps `minSdkVersion = 22`
> and compile/target 34 even after the dependency bump, which Play now rejects.
> `cap add android` refuses outright rather than overwrite, so nothing is lost by
> removing it. CI is unaffected: it always starts from a clean checkout.


```bash
cd mobile
npm install
rm -rf android               # REQUIRED if you generated it before Capacitor 8
npx cap add android          # generates the android/ native project
npx cap sync android         # copies www/ + config into the native project
python scripts/add_app_scheme.py            # deep link, native plugins, manifest
python scripts/add_app_icons.py             # launcher icon + splash
python scripts/configure_android_release.py # version + the separate debug identity
```

Run the three scripts after every `cap add android`, exactly as CI does. Skipping
`configure_android_release.py` leaves the debug build on the Play package, which
is the collision described below; `npm run build:debug` / `build:release` run it
for you.

## App icon + splash (required for release)

The committed `resources/icon.png` (1024×1024) and `resources/splash.png`
(2732×2732) are the canonical artwork. CI installs them after generating each
native project and fails if the Capacitor template changes shape. To regenerate
the Android density set after intentionally changing the source art:

```bash
python scripts/render_app_icons.py
python scripts/add_app_icons.py  # after `npx cap add android`
```

The unused `@capacitor/assets` dependency was removed because its pinned image
stack carried high/critical build-chain advisories. See `resources/README.md`.

## Build & test a debug APK (on a device/emulator)

```bash
npx cap run android          # builds + installs on a connected device/emulator
# — or —
cd android && ./gradlew assembleDebug
# → android/app/build/outputs/apk/debug/app-debug.apk  (sideload to test)
```

The debug build installs as **`io.tinyassets.app.debug`**, labelled
"TinyAssets (debug)", next to (never over) the Play app.
`scripts/configure_android_release.py` writes that suffix into the debug
buildType, and `verify_android_release.py` fails on any other `applicationIdSuffix`.
The reason: a debug APK is signed with a development key, and when it shared
`io.tinyassets.app` every Play update failed with "Can't install" on a phone
that had sideloaded it. CI publishes the same build as
`releases/download/android-latest/app-debug.apk` for developers; people install
from Google Play. Debug builds have no phone notifications: the published APK is
built without `google-services.json`, and a local file without an
`io.tinyassets.app.debug` client fails `assembleDebug` (the materialise step says so).

Verify the full loop on the device: **sign in (WorkOS)** → **connect your AI
subscription** → **chat with your command center**.

## Build a release bundle (.aab) for Google Play

1. **Generate an upload keystore** (once; keep it + the passwords safe):
   ```bash
   keytool -genkey -v -keystore tinyassets-upload.jks -keyalg RSA -keysize 2048 \
           -validity 10000 -alias upload
   ```
2. **Point Gradle at it** — create `android/keystore.properties` (do NOT commit):
   ```properties
   storeFile=../../tinyassets-upload.jks
   storePassword=********
   keyAlias=upload
   keyPassword=********
   ```
   and wire it into `android/app/build.gradle` `signingConfigs`/`buildTypes.release`
   (Android Studio → Build → Generate Signed Bundle does this for you via a wizard).
3. **Build the bundle:**
   ```bash
   cd android && ./gradlew bundleRelease
   # → android/app/build/outputs/bundle/release/app-release.aab
   ```

## Submit to Google Play

1. Play Console → **Create app** (name: *TinyAssets*, app or game: App, free).
2. Complete the required declarations (privacy policy URL, data safety, content
   rating, target audience). The app collects a sign-in identity + the AI
   credential the user deposits — declare accordingly.
3. **Create a release** (Internal testing first is fastest) → upload
   `app-release.aab` → roll out. Internal testing reaches your testers in minutes;
   production goes through review (hours–days).
4. Enrolling in **Play App Signing** (recommended default) lets Play manage the
   final signing key; your upload keystore only signs uploads.

## Build and release iOS

The generated `ios/` project is not tracked. On a Mac with Xcode:

```bash
cd mobile
npm ci --ignore-scripts --no-audit --no-fund
npx cap add ios
npx cap sync ios
python3 scripts/add_ios_scheme.py
python3 scripts/add_ios_assets.py
cd ios/App
xcodebuild -project App.xcodeproj -scheme App \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

`.github/workflows/ios-build.yml` runs that unsigned compile check on every
mobile change. `.github/workflows/ios-release.yml` is manual-only: it validates
an App Store provisioning profile against `io.tinyassets.app`, imports the Apple
Distribution identity into an ephemeral keychain, archives and exports a signed
IPA, verifies it, and publishes the IPA plus checksum/manifest as a short-lived
workflow artifact. Both release jobs use the protected `app-store` environment.
Its `upload_to_testflight` input defaults to false; when
explicitly enabled it validates and uploads the IPA to App Store Connect, but it
never submits the app for review. See `docs/ops/app-store-launch.md` for secrets
and account steps.

## Notes / follow-ups

- `appId` is `io.tinyassets.app` (change in `capacitor.config.json` before first
  publish if you want a different package name — it's permanent once published).
- The WebView allow-list is intentionally scoped to `tinyassets.io`. Provider
  authentication opens in the system browser and returns via `tinyassets://auth`.
- To harden Play review against "minimum functionality," the native plugins here
  give real device integration; a later pass can add push notifications
  (`@capacitor/push-notifications`) for run/agent updates.
- App Store screenshots must be captured at an Apple-accepted iPhone size; the
  current 1080×1920 Play captures are not valid App Store screenshot dimensions.
