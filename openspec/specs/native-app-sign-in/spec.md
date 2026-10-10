# Native app sign-in

## Requirements

### Requirement: Native sign-in uses the system browser
Native shells SHALL authenticate through Android Custom Tabs, iOS ASWebAuthenticationSession or Electron system-browser navigation and return only an opaque reference bound to the initiating app's PKCE secret.

#### Scenario: Successful native return
- **WHEN** an owner completes sign-in in the system browser
- **THEN** the initiating app redeems the reference using its private verifier and establishes its ordinary app session
- **AND** the browser's approval authority is not transferred

#### Scenario: Invalid or replayed return
- **WHEN** a reference is expired, consumed or accompanied by the wrong verifier
- **THEN** redemption fails without issuing credentials

#### Scenario: Missing browser integration
- **WHEN** the native authentication browser cannot open
- **THEN** sign-in reports an actionable error and does not open OAuth in the embedded WebView

### Requirement: Device release artifacts
Native builds SHALL use the app background #0f1020 and preserve validated approval return handling.

#### Scenario: Desktop packaging
- **WHEN** the desktop workflow runs
- **THEN** it produces Windows NSIS and ZIP, macOS DMG, and Linux AppImage artifacts from the requested commit
