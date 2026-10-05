## ADDED Requirements

### Requirement: Connected model choices survive workflow and catalogue boundaries

The system SHALL preserve a workflow step's explicit model choice through owner
authority and SHALL refuse unavailable choices with their model and reason rather
than substitute an unrequested model. Advisory native refresh results SHALL
survive process replacement and remain bound to current owner custody. Confirmed
additional model connections SHALL join accepted membership with their confirmed
limits while preserving the current serving source.

#### Scenario: Cross-family workflow review
- **WHEN** a step selects an accepted model from a different family
- **THEN** its invocation uses that source and model or fails naming the choice

#### Scenario: Provider-only workflow choice
- **WHEN** a step selects a provider without an exact model ID
- **THEN** all eligible same-source models in the captured order remain candidates within the existing allowance
- **AND** exhausting that source fails without substituting another source

#### Scenario: A refreshed catalogue is read by another process
- **WHEN** a native source refresh completes and another cache instance reads it
- **THEN** its models are selectable under unchanged accepted custody

#### Scenario: A powered owner confirms another model connection
- **WHEN** the owner confirms a model use on a second connection
- **THEN** the source joins accepted membership without replacing the serving root

#### Scenario: Existing model setup cannot accept another source
- **WHEN** the powered setup lacks an accepted manifest digest or exactly one owned serving agent
- **THEN** the request stays pending with `model_source_acceptance_failed` and actual serving state
- **AND** the owner is told to confirm explicit model access for the existing source and ensure exactly one owned agent is serving before retrying
- **AND** a deposited credential alone is never reported as accepted model access
