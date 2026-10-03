# Free source pooling

## Purpose

How a command center keeps answering on free model sources: daily quota is classified and cooled per source, chat and workflows pool across the owner's own accepted sources, and the connect route offers verified free source cards.

## Requirements

### Requirement: Daily quota evidence survives source cooldown
The system SHALL classify explicit daily quota separately from transient throttling, cool that source until the reported reset or a conservative day if unknown, and tell its owner the daily limit, reported reset when available, and options to connect another free source or add provider credit using its real provider URL.

#### Scenario: Daily refusal and subsequent turn
- **WHEN** a source returns explicit daily exhaustion and a later turn encounters its cooldown
- **THEN** both notices retain daily classification and neither promises recovery in a few minutes

#### Scenario: Per-minute refusal
- **WHEN** a source returns a per-minute 429 without daily exhaustion evidence
- **THEN** it retains transient capacity behavior

### Requirement: Pooling preserves owned authority
Chat and workflows SHALL continue the existing eligible model order across accepted owner-bound sources after daily exhaustion without using another owner's source, broadening grants, or increasing monetary ceilings.

#### Scenario: Two sources
- **WHEN** the first source is daily exhausted and the second has an eligible model
- **THEN** the second answers and no sibling of the exhausted source is called

#### Scenario: Other owner
- **WHEN** another owner's source is healthy
- **THEN** it is never discovered or invoked on behalf of the exhausted owner

### Requirement: Discoverable source cards
The existing connect route SHALL offer Google AI Studio, Groq, Cerebras and Mistral using one-secret api_key_http/openai_chat cards with verified endpoints and key-page links, truthful free/trial disclosures and model discovery or declared models.

#### Scenario: Single connected source
- **WHEN** an owner has one model source
- **THEN** an optional suggestion leads to the same connect cards as Change model / Connect another model source
