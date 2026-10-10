## ADDED Requirements

### Requirement: Private input fits owner devices
The protected browser login view SHALL support phone and desktop viewports, touch input, keyboard resizing, rotation and safe areas, with semantic password-manager input hints and truthful passkey availability.

#### Scenario: Phone keyboard entry
- **WHEN** an owner edits private input on a phone
- **THEN** the focused field and submit/cancel controls remain reachable without exposing input to the agent

#### Scenario: Foreign-origin passkey
- **WHEN** a relying site has not authorized the TinyAssets origin
- **THEN** the app does not request or claim to forward that site's passkey
