## ADDED Requirements

### Requirement: Visual discovery requires no model
The owner SHALL be able to browse descriptions and swipeable visual previews without a configured model. Preview SHALL create no consent request or installation and SHALL have no access to owner data or mutation capabilities.

#### Scenario: Unpowered owner previews a public design
- **WHEN** an owner with no available model browses and previews a shared center
- **THEN** the actual public UI is rendered in an isolated preview with labeled preview state and its description
- **AND** no model call, message, consent request, owner read or mutation occurs

#### Scenario: Publisher code attempts a capability
- **WHEN** preview code attempts to send a message, run a workflow, mutate state or access private data
- **THEN** that action is refused and the trusted owner surface remains isolated

### Requirement: Copy consent and result are immediately visible
Copy SHALL display its exact pinned owner consent immediately. Deterministic installation results SHALL NOT automatically send an agent message. The result SHALL identify the copied screen and paused automations and offer explicit opening without replacing other recipient content.

#### Scenario: Owner copies without inference
- **WHEN** an unpowered owner explicitly chooses Copy and accepts its consent
- **THEN** supported public components are copied through existing owner-bound checks without invoking a model
- **AND** the owner can open the copied screen while existing private edits and paused automation state remain intact

#### Scenario: Owner leaves preview or changes accounts
- **WHEN** the owner cancels preview or changes account or home during a pending operation
- **THEN** no implicit adoption occurs and stale preview or copy results cannot act in the new session

### Requirement: Navigation persists inside main-agent chat
Trusted Browse and Switch controls SHALL remain discoverable inside the main-agent chat controls after design adoption, reload and chat collapse. The blank center SHALL explain that the owner can talk to the agent to build or change the space, or browse shared designs.

#### Scenario: Adopted design cannot hide navigation
- **WHEN** a custom design is active and the owner shrinks or reopens chat
- **THEN** trusted browsing and own-screen switching remain available independently of that design
