## ADDED Requirements

### Requirement: Pending requests expose bound actions without bearer approval authority
The existing pending-request ask/read operations SHALL expose the versioned action, revision/hash, protected draft, derived phase/result, expiry and safe provenance in design.md without adding MCP handles. Ordinary answers SHALL use the shared completion service. Caller-supplied provenance SHALL NOT choose the executing owner or agent. Approval and any retry that dispatches a bound effect SHALL require the separate interactive owner session and single-use action-bound token, unavailable to bearer-only MCP or automated clients.

#### Scenario: Owner chatbot has the owner's bearer and action hash
- **WHEN** a chatbot connector, CLI or runtime agent reads the current revision/hash and submits approve or a dispatching retry
- **THEN** bearer-only approval is rejected with direction to the protected inline owner card, even for the owner's own chatbot
- **AND** it cannot mint an interactive session/token, spoof an interactive surface or use an answer/rule-write alias to bypass this check

#### Scenario: Approval from another owner device
- **WHEN** the owner signs in interactively on another device, previews the protected action and submits the matching session-bound token
- **THEN** the shared server service executes the same bound action and returns its result without a conversational retry

#### Scenario: Existing item or credential answer
- **WHEN** an existing values/item/dismiss answer is submitted for an ordinary request
- **THEN** existing consent and secret-deposit behavior remains and a sanitized continuation outcome is recorded
- **AND** that ordinary answer cannot authorize a bound external action

#### Scenario: Malformed bound action
- **WHEN** an agent submits invalid action fields
- **THEN** existing validation conventions reject the request without creating approval authority or echoing secrets
