## MODIFIED Requirements

### Requirement: Any website can be offered as a browser connection
The system SHALL offer normal website login through its single protected live owner view without developer registration or provider-specific code. The browser and view server SHALL run in the owner's box under a separate OS user and process/mount namespace from the agent. The agent's bash and tools SHALL have no access to the browser profile, capture, input or debug channel. `BoxProvider.view` SHALL extend this view to the box's browser and desktop; private input SHALL never enter agent observations.

#### Scenario: Password or redirect login
- **WHEN** the owner taps Sign in and completes normal login, including an identity-provider redirect or popup
- **THEN** only the protected view receives frames and input, and the broker seals reusable state for that owner
- **AND** a later `ta` call can act through a fresh browser using that state

#### Scenario: Agent probes the live owner view
- **WHEN** the agent uses bash or tools to open capture or input sockets, inspect private frames, or drive the browser debug channel
- **THEN** OS isolation refuses access and no private input enters agent observations
