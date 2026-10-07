# universe-intelligence (delta)

## ADDED Requirements

### Requirement: The founder-tier reply prompt instructs proactive brain persistence

When assembling the served reply-turn system prompt for the FOUNDER tier, the
universe SHALL be instructed to durably record the facts its founder teaches it
using its governed brain-write tool the moment it learns them, and to record
answers to its open self-model questions rather than only asking — without asking
permission to remember and without inventing facts (the honesty/safety floor
still governs what may be written). This instruction SHALL NOT appear for
non-founder tiers, and persistence remains founder-scoped (the brain-write path
is founder-allowlisted). Recording a governed section clears the corresponding
open question via the existing learned-status mechanism, so the universe stops
re-asking what it has been told.

#### Scenario: Founder teaches a durable fact

- **GIVEN** a founder-tier conversation turn
- **WHEN** the founder tells the universe a durable fact about who it is, who its
  founder is, where it came from, or its form/projects/repositories
- **THEN** the reply prompt has instructed the universe to persist that fact with
  its brain-write tool that turn, rather than only stating it in chat or asking
  permission to remember

#### Scenario: A visitor is not shown brain-write mechanics

- **GIVEN** a non-founder (lower-tier) conversation turn
- **WHEN** the reply prompt is assembled
- **THEN** it contains no brain-write instruction, so this change introduces no
  proactive `write_brain` persistence for the visitor (the separate post-hoc
  extractor is out of scope for this change)
