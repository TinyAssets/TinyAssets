## MODIFIED Requirements

### Requirement: Inline connection at first send
The bubble SHALL render a neutral connection card only when a message is refused for missing usable model authority, without calling an LLM.

#### Scenario: Empty account
- **WHEN** an unpowered owner sends a message
- **THEN** the bubble retains it and opens the full list of available installed sources, with no primary provider and a generic compatible endpoint.

#### Scenario: Connected account
- **WHEN** usable authorized model access exists
- **THEN** the app does not prompt for connection; adding a source remains an explicit optional action and automatic selection retains its existing strongest-eligible ordering.

## ADDED Requirements

### Requirement: Setup never hides source choice
The app SHALL list every installed hosted OAuth or subscription source as a visible one-tap sign-in button first, followed by visible API-key sources and a compatible-endpoint choice. Selecting any source SHALL silently abandon earlier client setup without waiting for cancellation. A valid return from the selected hosted sign-in SHALL connect and close the dialog without another tap, through the existing model-access answer path. The needs-connection card SHALL always offer Connect to open this dialog.

#### Scenario: Other AI during stale setup
- **WHEN** the owner opens Connect and chooses any source, including the same provider
- **THEN** all source choices remain visible and the earlier flow cannot replace that choice.

### Requirement: Responsive connect layout
The default app column and request dialog SHALL be centered on wide screens while remaining usable on narrow screens.

#### Scenario: Wide browser
- **WHEN** a 1366, 1920, 2400 or 390 pixel browser opens setup without a saved custom placement
- **THEN** the chat and dialog use centered readable widths and the list scrolls within the viewport.
