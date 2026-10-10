## ADDED Requirements

### Requirement: One interruption and remembered account custody
The inline action SHALL be labelled Sign in with its exact origin and open owner
takeover directly. Authentication SHALL require a read-only authenticated-page
check at that origin. Success SHALL seal the session and atomically resolve the
existing request with its durable continuation wake, without a second tap or
message. A valid remembered site/account session SHALL require zero connection
taps. Ambiguous accounts SHALL require selection, never arbitrary reuse.

#### Scenario: Reuse, expiry and revoke
- **WHEN** a later task uses a remembered account
- **THEN** a fresh browser verifies its authenticated page before acting
- **AND** expiry returns a one-tap login on the same connection, while revocation clears custody and fences old captures

### Requirement: Protected filling binds the destination
Credentials SHALL reach only the owner-bound browser document and origin shown
in the protected sheet. The model SHALL have no filling or observation access
during takeover. Device password-manager integration SHALL respect OS and web
origin restrictions; unsupported passkeys/autofill SHALL be reported honestly.

#### Scenario: Navigation races a credential fill
- **WHEN** the destination document, frame or origin changes after a field is displayed
- **THEN** the stale field handle is refused without sending its credential
- **AND** a detected site block stays blocked with no stealth retries

### Requirement: Any website can be offered as a browser connection
The system SHALL offer normal website login through a protected live owner view without developer registration or provider-specific code. The browser SHALL run in the owner's isolated cell with no agent-accessible profile or control channel.

#### Scenario: Password or redirect login
- **WHEN** the owner taps Sign in and completes normal login, including an identity-provider redirect or popup
- **THEN** only the protected view receives frames and input, and the broker seals reusable state for that owner
- **AND** a later `ta` call can act through a fresh browser using that state

### Requirement: Session custody is private and revocable
Browser state SHALL be encrypted in broker custody bound to owner/home/connection/revision, never exported to model, agent filesystem or logs. Agent calls SHALL expose structured actions and sanitized untrusted observations only. Network destinations SHALL be validated and DNS-pinned to public addresses.

#### Scenario: Foreign owner or credential inspection
- **WHEN** another owner names the connection, or an agent requests capture frames, credentials, evaluate, CDP or storage export
- **THEN** access is refused without returning private state

#### Scenario: Revoke races completion
- **WHEN** an owner revokes while an older capture or action is completing
- **THEN** the connection is fenced and ciphertext removed; stale completion cannot restore access

#### Scenario: Expiry or challenge
- **WHEN** authentication expires or a site needs CAPTCHA, MFA or unsupported device authentication
- **THEN** the same protected live view offers takeover and reports unsupported device flows truthfully
