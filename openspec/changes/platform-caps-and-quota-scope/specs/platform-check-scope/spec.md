## ADDED Requirements

### Requirement: Usable request-card links
The request validator SHALL accept safe HTTPS links through 8192 characters without shortening them and SHALL retain scheme and safety validation.

#### Scenario: Tap-to-post link
- **WHEN** an owner request includes a safe 430-character link
- **THEN** validation preserves the complete link

### Requirement: Cooldowns are owner scoped
Provider cooldowns and reasons SHALL be isolated by owner and provider, including deferred cooldowns after a turn or run.

#### Scenario: Shared executor name
- **WHEN** owner A cools claude-code
- **THEN** owner B can still attempt claude-code
- **AND** A remains in cooldown

### Requirement: Actionable inference accounting refusal
Missing HTTP inference accounting SHALL fail closed with a fixed actionable diagnostic across broker transports, without exposing credentials or suggesting that owner consent bypasses accounting.

#### Scenario: Unaccounted direct inference POST
- **WHEN** a model connection receives an inference POST without its parent usage reference
- **THEN** no request is dispatched and the caller is directed to the accounted model invocation path
