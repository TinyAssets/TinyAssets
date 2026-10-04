### Requirement: Inline connection at first send
The bubble SHALL render a provider-data connection card when a message is refused for missing usable model authority, without calling an LLM.

#### Scenario: Empty account
- **WHEN** an unpowered owner sends a message
- **THEN** the bubble retains it and offers the configured primary provider and Other AI.

### Requirement: Connection continuation
The app SHALL continue the exact retained message once after verified connection, preserving cancellation and uncertain delivery recovery.

#### Scenario: Successful connection
- **WHEN** the owner completes the primary connection
- **THEN** the card says Connected and the original message follows the existing send path without another user send.

#### Scenario: Cancellation
- **WHEN** sign-in is cancelled or fails
- **THEN** the card offers retry and the original message remains retained.

### Requirement: Connection isolation
The server SHALL hold PKCE secrets and bind each inline flow to its owner, home, and callback browser.

#### Scenario: Foreign user
- **WHEN** another user attempts to inspect or complete a flow
- **THEN** the server refuses before exchange or credential deposition.
