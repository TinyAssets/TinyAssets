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

#### Scenario: A refreshed catalogue is read by another process
- **WHEN** a native source refresh completes and another cache instance reads it
- **THEN** its models are selectable under unchanged accepted custody

#### Scenario: A powered owner confirms another model connection
- **WHEN** the owner confirms a model use on a second connection
- **THEN** the source joins accepted membership without replacing the serving root
