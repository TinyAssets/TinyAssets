# First-run model connect

## Purpose

Connect an unpowered owner to their own model inside the chat and continue the retained message.

## Requirements

### Requirement: Inline connection at first send
The bubble SHALL render a neutral connection card only when a message is refused for missing usable model authority, without calling an LLM.

#### Scenario: Empty account
- **WHEN** an unpowered owner sends a message
- **THEN** the bubble retains it and offers the full list of available installed sources, with no primary provider and a generic compatible endpoint.

#### Scenario: Connected account
- **WHEN** usable authorized model access exists
- **THEN** the app does not prompt for connection; adding a source remains an explicit optional action and automatic selection retains its existing strongest-eligible ordering.

### Requirement: Connection continuation
The app SHALL continue the exact retained message once after verified connection, preserving cancellation and uncertain delivery recovery.

#### Scenario: Successful connection
- **WHEN** the owner completes their chosen connection
- **THEN** the card says Connected and the original message follows the existing send path without another user send.

#### Scenario: Cancellation
- **WHEN** sign-in is cancelled or fails
- **THEN** the card offers retry and the original message remains retained.

### Requirement: Connection isolation
The server SHALL hold PKCE secrets and bind each inline flow to its owner, home, and callback browser.

#### Scenario: Foreign user
- **WHEN** another user attempts to inspect or complete a flow
- **THEN** the server refuses before exchange or credential deposition.


### Requirement: Setup never hides source choice
The app SHALL display earlier setup as recovery controls above the full source list and SHALL cancel a retained inline flow before starting another source.

#### Scenario: Other AI during stale setup
- **WHEN** the owner opens Other AI and chooses a different source
- **THEN** all source choices remain visible and the earlier flow cannot replace that choice.

### Requirement: Responsive connect layout
The default app column and request dialog SHALL be centered on wide screens while remaining usable on narrow screens.

#### Scenario: Wide browser
- **WHEN** a 2400 pixel browser opens setup without a saved custom placement
- **THEN** the chat and dialog use centered readable widths and the list scrolls within the viewport.
