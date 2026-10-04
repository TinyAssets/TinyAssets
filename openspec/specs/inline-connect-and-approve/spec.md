# Inline connect and approve

## Purpose

Describe the implemented S1 once-only literal HTTP approval slice. The remaining
payload forms, task/always grants and broader connection/continuation contracts
remain in the active change; this specification does not claim their completion.

## Requirements

### Requirement: Protected literal action preview
An eligible `ask_first` HTTP action SHALL retain its literal executable packet
and trusted owner, agent and turn provenance in protected storage. The bubble
SHALL render that protected preview. Unsupported headers, transforms, file
references and credential-shaped inputs SHALL fail closed.

#### Scenario: Edited body
- **WHEN** an owner edits an eligible action body
- **THEN** the action receives a new revision and the old approval token is invalid.

### Requirement: Interactive once approval
Executing an approval SHALL require an interactive owner-session token bound to
the action, revision and scope. Bearer-only clients SHALL NOT acquire that
authority. Once approval SHALL leave Rules unchanged and recheck current policy,
connection, grant and consent before ordinary effector dispatch.

#### Scenario: Repeated decision
- **WHEN** the same action approval is submitted again
- **THEN** the protected reservation prevents a second execution.

#### Scenario: Revoked authority
- **WHEN** the relevant owner policy changes or the task is stopped before dispatch
- **THEN** a stale once approval cannot execute the action.

### Requirement: Durable bounded continuation
Action-result and denial continuations SHALL retain durable deduplication,
attempt and acknowledgement state and resume the saved agent through recovery.
Retry backoff SHALL survive another recovery scan. Uncertain effects SHALL NOT
be blindly resent; this requirement does not promise exactly-once computation.

#### Scenario: Retry not yet due
- **WHEN** recovery scans a retry whose persisted backoff has not elapsed
- **THEN** it leaves the next-attempt time intact and does not immediately retry.

### Requirement: Read-only history and stale dismissal
Request history SHALL remain read-only. A stale card MAY be dismissed through
the bound interactive session while approval and editing remain disabled.

#### Scenario: Stale action
- **WHEN** a request becomes ineligible for approval
- **THEN** dismissing it does not dispatch its effect or restore its approval authority.
