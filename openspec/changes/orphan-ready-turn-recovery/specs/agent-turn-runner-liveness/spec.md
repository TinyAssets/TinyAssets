## ADDED Requirements
### Requirement: Working means a runner exists
The system SHALL report a working turn only with a live per-turn runner claim, and SHALL settle a runner exit before its first round.
#### Scenario: Activity fails before inference
- **WHEN** an activity fails before round one
- **THEN** its turn is abandoned and does not block chat
### Requirement: Recover orphaned turns
The system SHALL settle orphan rows on boot and owner-scoped Stop, preserving uncertain effects and addressed-agent isolation.
#### Scenario: Legacy ready row
- **WHEN** Stop targets the owner and agent of a ready row without a runner
- **THEN** the row is settled and interrupted is at least one
### Requirement: Visible activity failure
The system SHALL record failed activity outcomes in chat and next-turn context and explicitly refuse unsupported activity starts.
#### Scenario: Native executor without tool fencing
- **WHEN** the selected activity executor cannot safely yield
- **THEN** start returns a failure instead of claiming background work started
