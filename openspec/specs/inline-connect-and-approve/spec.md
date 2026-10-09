# Inline connect and approve

## Purpose

Describe the implemented S1 and S2 literal HTTP approval and service connection
slices. Remaining payload forms, standing spend grants, server-held generic
OAuth custody and broader acceptance remain in the active change. This partial
as-built specification does not assert deployment or full change completion.

## Requirements

### Requirement: Browser callback transport stays flow-specific
The public edge SHALL preserve hosted model binding cookies named
`__Host-ta-model-` followed by exactly 43 URL-safe characters, only on app routes,
with Path=/ and the existing host-only Secure, HttpOnly and SameSite rules.
Web sign-in states SHALL start with `web.`. Owner callback routing SHALL match
only `oa_` or `oa_app_` followed by exactly 43 URL-safe characters.

#### Scenario: Hosted model browser round trip
- **WHEN** the owner launches hosted model connect and returns from the provider
- **THEN** the binding cookie survives the edge, the callback deposits the code,
  and the authenticated originating app can consume it once.

#### Scenario: Web random suffix resembles owner state
- **WHEN** web sign-in randomness starts with `oa_`
- **THEN** its `web.` prefix keeps the callback in the web sign-in flow.

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

### Requirement: Approval sheet and Needs you inbox
Foreground protected approval requests SHALL open in a modal approval sheet
showing purpose and the exact protected action. The owner SHALL choose once,
task, site, always or Deny through the existing interactive owner-session path.
Background requests SHALL remain reachable through the bubble's Needs you inbox
and existing owner notification delivery. History SHALL remain read-only.
The side requests panel and above-composer pending region SHALL be absent.

#### Scenario: Request arrives while the owner types
- **WHEN** another request arrives during an open sheet
- **THEN** polling preserves the current sheet's draft and lists the new ask in the inbox.

#### Scenario: Owner changes account
- **WHEN** the owner signs out or changes account
- **THEN** cached request cards and staged credential values are cleared.

### Requirement: Revocable HTTP scope grants
Wider grants SHALL be stored in existing Rules, bound to owner, agent, connection
revision, consent, policy digest, action class, operation and exact HTTPS origin.
Task grants SHALL additionally require the live task generation and deadline.
Once SHALL create no Rule. Site and always SHALL NOT grant arbitrary operations
or other origins. Wider spend grants SHALL refuse until budget caps exist.
Only finalized issuing decisions SHALL authorize reuse. Removal SHALL leave a
tombstone that prevents recovery from resurrecting permission.

#### Scenario: Policy or task authority changes
- **WHEN** relevant policy, connection authority or task generation changes, or the task ends
- **THEN** the old grant cannot authorize a subsequent action.

#### Scenario: Crash before wider-grant finalization
- **WHEN** recovery finds an incomplete planned issuing decision
- **THEN** it invalidates that attempt, leaves its grant inert and permits protected dismissal without resending the effect.

### Requirement: Shared service connection sheet
Agent service requests and settings SHALL use the service connection sheet with
registered OAuth, protected API-key entry or generic HTTP authentication shapes.
Changing auth shape SHALL clear staged secrets. Optional account labels SHALL
allow independent connections to the same service without provider-specific code.
Existing first-power model setup remains a shared setup control, not a completed
consolidation of every model-provider flow.

#### Scenario: Agent connection answer
- **WHEN** a connection request raised in a trusted agent turn is answered
- **THEN** resolution and a sanitized durable continuation wake commit together, and the browser does not submit a second chat turn.

#### Scenario: Settings connection
- **WHEN** the owner connects from settings without an initiating agent turn
- **THEN** the platform does not invent an agent continuation.
