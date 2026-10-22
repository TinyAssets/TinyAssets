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
The app SHALL display earlier setup as recovery controls above the full source list and SHALL cancel a retained inline flow before starting another source.

#### Scenario: Other AI during stale setup
- **WHEN** the owner opens Other AI and chooses a different source
- **THEN** all source choices remain visible and the earlier flow cannot replace that choice.

### Requirement: Responsive connect layout
The default app column and request dialog SHALL be centered on wide screens while remaining usable on narrow screens.

#### Scenario: Wide browser
- **WHEN** a 2400 pixel browser opens setup without a saved custom placement
- **THEN** the chat and dialog use centered readable widths and the list scrolls within the viewport.
