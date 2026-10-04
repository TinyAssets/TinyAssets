## ADDED Requirements

### Requirement: Pending requests expose explicit bound-action fields
The existing pending-request ask, answer and read operations SHALL expose the versioned action, revision/hash, draft, phase and scoped-decision contract in design.md without adding MCP handles. Answers SHALL use the shared server-side completion path; caller-supplied provenance SHALL NOT choose the executing owner or agent, and an agent runtime SHALL NOT approve its own request.

#### Scenario: Read and approve from a different owner surface
- **WHEN** the authenticated owner reads an approval and submits its current revision/hash and scope through the existing answer operation
- **THEN** the same bound execution/result is returned as for the inline card, with no conversational retry

#### Scenario: Existing item or credential answer
- **WHEN** an existing values/item/dismiss answer is submitted for an ordinary request
- **THEN** its existing consent and secret-deposit behavior is preserved and a sanitized continuation outcome is recorded

### Requirement: Invalid request shapes name the expected fields
Request validation SHALL return `request_invalid` with `errors` entries naming `path`, `expected` and `message`, plus a minimal non-secret example. Validation SHALL distinguish action discriminators and manifest shapes without guessing formats or echoing secret inputs.

#### Scenario: Malformed action or connection manifest
- **WHEN** the agent omits a required field or supplies the wrong shape
- **THEN** the error names that field's full path and expected type/allowed fields so the agent can correct it without guessing
- **AND** no request, approval rule or connection is created
