## ADDED Requirements

### Requirement: Native sign-in uses the system browser
Native shells SHALL authenticate through Android Custom Tabs, iOS ASWebAuthenticationSession or Electron system-browser navigation and return an opaque reference and callback-only one-time secret bound to the initiating app's PKCE secret.

#### Scenario: Successful native return
- **WHEN** an owner completes sign-in in the system browser
- **THEN** the initiating app redeems the reference using its private verifier and callback-only secret and establishes its ordinary app session
- **AND** the browser's approval authority is not transferred

#### Scenario: Invalid or replayed return
- **WHEN** a reference is expired, consumed or accompanied by the wrong verifier or missing/wrong return secret
- **THEN** redemption fails without issuing credentials

#### Scenario: Missing browser integration
- **WHEN** the native authentication browser cannot open
- **THEN** sign-in reports an actionable error and does not open OAuth in the embedded WebView

#### Scenario: Device-code phishing
- **WHEN** a second client starts a flow and gives its authorization URL to a victim
- **THEN** knowing the reference and verifier does not permit redemption without the secret delivered only to the victim's app return
- **AND** the receiving app ignores returns whose signin is not its exact saved native_ref

#### Scenario: Bounded anonymous initiation
- **WHEN** a client IP exceeds ten starts per ten minutes or 1,000 flows are active
- **THEN** initiation refuses clearly without allocating another flow or unbounded limiter state

#### Scenario: Older Android update window
- **WHEN** an Android 1.0.6 or earlier cached page returns with app. or appdebug. state before 2026-10-24 UTC
- **THEN** a package-pinned intent returns its code and state to the initiating install, which verifies its saved state and PKCE
- **AND** after the deadline the server asks the owner to reopen/update the app

### Requirement: Device release artifacts
Native builds SHALL use the app background #0f1020 and preserve validated approval return handling.

#### Scenario: Desktop packaging
- **WHEN** the desktop workflow runs
- **THEN** it produces Windows NSIS and ZIP, macOS DMG, and Linux AppImage artifacts from the requested commit
