# Agent owner notifications

## Purpose

Informational owner delivery from agents and scheduled workflow steps.

## Requirements

### Requirement: Owner informational notification
The system SHALL allow main and custom agents and workflow steps to notify only their authenticated owner through existing inbox and push delivery surfaces, with title, body, source chat/item and optional attachment reference, without an answer or notification-specific rate limit.

#### Scenario: New notification
- **WHEN** an authorized agent or workflow invokes notify
- **THEN** Needs-you contains an informational card with no answer controls and existing owner push transports are called
- **AND** the card opens the originating agent's chat and carries any supplied item and attachment references

#### Scenario: Identical outstanding notification
- **WHEN** the same agent repeats identical content and references
- **THEN** the existing card is returned and push is not repeated

#### Scenario: Cross-user attempt
- **WHEN** a caller lacks ownership or names another recipient or universe
- **THEN** notification creation and delivery are refused

### Requirement: Editable capability guidance
Starter content and served guidance SHALL describe the persistent box, git, egress network, Python/pytest, file delivery and explicit scheduled notifications accurately and briefly.

#### Scenario: Scheduled work
- **WHEN** an agent reads capability guidance
- **THEN** it learns that scheduled work uses notify explicitly and run completion alone does not promise phone delivery
- **AND** the user can edit the seeded guidance without a platform override
