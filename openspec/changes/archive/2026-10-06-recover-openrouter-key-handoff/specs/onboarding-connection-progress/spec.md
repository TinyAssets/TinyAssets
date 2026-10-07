## ADDED Requirements

### Requirement: Manual OpenRouter acquisition preserves free-model approval

The signed-in app SHALL offer explicit manual-key recovery for the installed
OpenRouter free-model bootstrap preset through the existing same-origin model
connection ingress, without requiring existing inference or an OAuth callback.
The operation MUST reuse the owner-scoped bootstrap and its ordinary unanswered
model-access request; depositing a key MUST NOT enable model execution, approve
access, change saved preferences, or permit paid models or paid fallback.
Manual recovery SHALL require `manual_key_entry` equal to boolean true in the
trusted installed acquisition document, never caller metadata. Absence, false,
non-boolean values and a newly installed preset without opt-in MUST refuse.
The installed endpoint and matching owner-filtered bearer discovery contract
MUST validate before home creation or credential deposit.

#### Scenario: Unpowered owner supplies their key
- **WHEN** the current live owner of an empty home explicitly submits a valid key
- **THEN** the server uses only the installed preset's endpoint and eligibility policy
- **AND** successful preparation displays the existing model-access confirmation
- **AND** access changes only after the ordinary explicit owner approval

#### Scenario: Wrong authority or changed setup
- **WHEN** the account is deleted or deleting, ownership or home changes, or setup is no longer empty before a mutation
- **THEN** acquisition refuses without resurrecting the account or replacing its connections
- **AND** a concurrent acquisition cannot silently overwrite the winning setup

#### Scenario: Caller tries to expand provider access
- **WHEN** a request includes a caller-selected endpoint, owner, model, grant or policy, or an unsupported preset
- **THEN** the server refuses it before any credential deposit or provider request
- **AND** a key with broader provider permissions cannot enable paid model selection

#### Scenario: Another preset is installed without manual recovery authority
- **WHEN** an otherwise valid installed preset lacks the explicit trusted boolean opt-in
- **THEN** manual acquisition refuses before home creation or credential deposit
- **AND** adding a request field cannot grant that capability

### Requirement: Manual key handling is bounded and non-replaying

The app SHALL clear the manual input before its first asynchronous operation,
send the key only in the bounded authenticated same-origin JSON request, and
exclude it from URLs, browser storage, chat, logs, errors and responses. The
server MUST reject malformed or oversized input without echoing it. Pending
submissions SHALL remain tied to their original login and SHALL NOT replay.

#### Scenario: Submission outcome is uncertain
- **WHEN** deposit or discovery times out or the browser loses the response
- **THEN** the app reports incomplete or unconfirmed setup rather than connected
- **AND** it offers only user-directed state recovery without retaining or resending the key

#### Scenario: Login changes while the request is pending
- **WHEN** the user signs out or changes login during the manual request
- **THEN** the old result cannot alter the new login's UI or issue a new request under that login

### Requirement: Signup handoff guidance is truthful and discoverable

The hosted setup card SHALL explain that provider signup alone does not connect
a model and SHALL prominently expose the single secure manual-key recovery
input. It MUST NOT promise automatic provider return or describe generic vault
storage as completed model setup.

#### Scenario: Signup lands on the provider workspace
- **WHEN** the provider finishes signup on its workspace instead of returning
- **THEN** TinyAssets explains how to return and continue the connection
- **AND** an authorization error remains visibly incomplete without blind retry advice
