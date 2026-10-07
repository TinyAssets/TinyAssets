# BYO LLM provider connect

## ADDED Requirements

### Requirement: Vault custody issues the serving credential reference

The credential-vault subsystem SHALL adopt exactly one usable universe-scoped
`llm_subscription` record only when the authenticated caller matches the
server-recorded principal that deposited or connected that credential. It
SHALL issue an opaque reference with a monotonic generation and a digest bound
to both the vault record and actual credential material. Provider-binding,
API, and router code SHALL NOT store, return, or accept caller-asserted
credential material, credential identifiers, generations, or ownership.

#### Scenario: Existing Codex subscription is adopted

- **WHEN** the authenticated binding creator requests provider `codex` and the
  canonical universe vault contains exactly one usable Codex subscription
- **THEN** the vault custody owner issues or reuses one opaque reference bound
  to that owner, universe, service, current record digest, and generation
- **AND** no secret or credential-reference digest is returned by the operation

#### Scenario: ACL collaborator owns the agent but not the credential

- **WHEN** a write-ACL collaborator creates an agent in another principal's
  universe and attempts to bind that principal's deposited credential
- **THEN** custody adoption and provider-authority minting are denied
- **AND** ACL permission or payload fields cannot substitute for depositor ownership

#### Scenario: Custody evidence is not usable

- **WHEN** the matching vault record is missing, duplicated, unreadable,
  path-escaping, symlinked, auth-unusable, ownerless, owned by another
  principal, or its path-backed credential bytes change after adoption
- **THEN** the operation or served call holds before provider access

#### Scenario: Path-backed credential rotates during an admitted launch

- **WHEN** admission has copied and validated the current credential bytes into
  a per-launch snapshot and another process replaces the source `auth.json`
- **THEN** the admitted launch continues only from its immutable snapshot
- **AND** the next admission rejects the stale custody generation until the
  rotated credential is adopted and rebound
- **AND** every launch snapshot is removed best-effort after success or failure

### Requirement: Serving-provider bind is one atomic server-derived operation

`write_graph target=agent_binding operation=bind_serving_provider` SHALL accept
exactly a provider selection plus the target binding and revision supplied by
the operation envelope. The server SHALL derive the authenticated owner,
universe, credential reference, `converse` operation, `writer` role, budgets,
expiry, assignment generation, and binding digest. It SHALL reuse
`ProviderWorkBinding`, wire the exact `agent_binding.provider_ref`, and publish
ready only when the assignment, provider binding, custody reference, and agent
revision agree.

#### Scenario: Creator connects Codex

- **WHEN** the authenticated creator submits the exact Codex operation against
  the current private agent-binding revision
- **THEN** one active `ProviderWorkBinding` permits only `converse` and `writer`
- **AND** the agent's provider reference and requester-local assignment point
  at that exact binding generation and digest

#### Scenario: Generic binding payload asserts a provider reference

- **WHEN** create/update binding payload contains `provider_ref`
- **THEN** validation rejects it without changing the binding

#### Scenario: Collaborator attempts to connect another creator's binding

- **WHEN** an authenticated ACL collaborator who is not the binding creator
  requests bind or serving state
- **THEN** provider authority is denied before custody or binding mutation

### Requirement: Serving state is a first-class reversible server write

`write_graph target=agent_binding operation=set_serving` SHALL accept exactly
`enabled: boolean`. Enabling SHALL require the fresh ready assignment, active
`converse`/`writer` provider binding, matching agent provider reference, and
current vault custody. Disabling SHALL remain available to the creator even
when provider authority is stale. The serving switch SHALL preserve the agent
configuration revision so an otherwise-current signed Slack route is not
invalidated by the server-owned serving state transition.

#### Scenario: Enable without connected authority

- **WHEN** the creator enables a configured binding with no current ready
  serving-provider authority
- **THEN** the write fails with connect-provider guidance and remains configured

#### Scenario: Disable serving

- **WHEN** the creator disables an enabled binding at its current revision
- **THEN** the binding becomes configured and subsequent turns hold before
  provider access

### Requirement: Slack worker enrollment follows serving state dynamically

The long-running Slack worker SHALL reconcile the union of retained static
host paths and server-held current serving enrollments. It SHALL add a
universe socket after serving becomes current and cancel the dynamic socket
after serving is withdrawn without requiring process restart. Enrollment SHALL
not itself authorize a provider call.

#### Scenario: Serving changes while worker is running

- **WHEN** a current universe transitions configured to serving and later back
  to configured
- **THEN** the worker enrolls and withdraws that universe on reconciliation
- **AND** every founder Slack turn still passes the signed-ingress request
  capability and fresh router sink before provider access

### Requirement: Slice-1 acceptance is founder-only

Only the authenticated owner/founder request principal SHALL be eligible to
spend through a Slice-1 serving binding. Channel routing, ACL collaboration,
an external visitor identity, or an unsealed founder claim SHALL not grant
provider authority. Visitor spend requires a later explicit bounded grant.

#### Scenario: Slack visitor reaches a routed channel

- **WHEN** a routed Slack sender has no current sealed founder mapping
- **THEN** no provider request capability is minted and no provider is called
