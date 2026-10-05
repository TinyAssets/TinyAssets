## ADDED Requirements

### Requirement: Every outside call carries verified client attribution
The daemon SHALL bind verified issuer/client id and registered CIMD/DCR metadata (name, logo and redirect host, or explicit unavailable values) to the principal on every authenticated public MCP request, preserving the existing owner/home identity independently of client identity. Display metadata SHALL NOT grant authority. Missing or conflicting verified client identity SHALL refuse before owner data or effects.

#### Scenario: Same owner connects two different apps
- **WHEN** the same owner authorizes Muse and OpenClaw through AuthKit CIMD/DCR
- **THEN** both resolve to the same owner/home with distinct verified client identities and independent grants
- **AND** neither registration requires brand-specific code

#### Scenario: Caller spoofs client metadata
- **WHEN** a call claims another client id, name or redirect in its payload or untrusted headers
- **THEN** attribution comes only from the verified OAuth binding and registered metadata
- **AND** missing verified identity cannot fall back to full-account authority

### Requirement: Owners delegate explicit agent and action grants
The daemon SHALL store owner-editable grants keyed by owner, universe, issuer and client id, containing allowed agent ids or explicit wildcard, independent `read`, `message`, `control`, `costly` levels, optional expiry, status and revision. First connect SHALL default to `read` + `message` for `main` only. `costly` SHALL cover spend, publish and external posts in addition to the operation's required ordinary level; it SHALL NOT imply other levels, owner approval or resource authority. Cross-user isolation SHALL remain the only immutable platform behavioral invariant; all client actions remain strictly within the authorizing owner's authority and editable grants.

Grant resolution SHALL follow authenticated home resolution. For a new user, the existing first-converse private-home bootstrap SHALL verify client identity and its revocation fence, create the default grant with the home in the same admission step and check that grant before delivering the message. Early no-home status SHALL require verified active client identity and its revocation fence, expose only the caller's no-home state and never provision. Existing or revoked grants SHALL NOT be reset by bootstrap.

#### Scenario: A new user's first client message
- **WHEN** a newly authenticated user without a home connects a verified active client and calls `converse` without a graph selector
- **THEN** existing home resolution creates the private home and main-only read/message grant before admitting the message
- **AND** a preceding status read reports no home without provisioning, and a revoked client cannot trigger bootstrap

#### Scenario: First connection has a useful narrow default
- **WHEN** a newly verified client connects, including concurrent first calls
- **THEN** one grant allows main-scoped reads and messages to main
- **AND** control, costly actions and addressing any other agent refuse with the missing grant

#### Scenario: Owner widens or narrows a client
- **WHEN** the protected owner app saves a revision-matching edit selecting researcher and control
- **THEN** subsequent calls use exactly that updated scope while other clients remain unchanged
- **AND** stale edits conflict instead of overwriting newer grants

#### Scenario: Bearer attempts self-elevation
- **WHEN** a bearer client requests grant widening through any alias, rule write or nested agent
- **THEN** it cannot apply the grant change or approve its own request, even with control and costly
- **AND** the protected owner approval/edit path is identified

#### Scenario: Foreign owner resource
- **WHEN** a fully granted client supplies another owner's agent, universe, page or run identifier
- **THEN** existing owner isolation refuses without revealing that owner's data or performing effects

### Requirement: Connected apps expose current authority and audit
The protected owner app SHALL list connected clients with verified id, registered display metadata, effective grants, status, expiry and last-used; it SHALL support owner edits and revoke. Owner-readable durable audit SHALL identify which client attempted each action, with owner/universe, addressed scope, handle/operation, request or work reference, grant revision, timestamp and outcome. Last-used SHALL reflect authenticated attempts, including grant denials. Credentials and raw content SHALL NOT be copied into audit records.

Owner SHALL mean the authorizing user; Connected apps and client audit SHALL be readable only by that user, independently of any shared universe ACL.

#### Scenario: Another user has shared universe access
- **WHEN** another principal with access to the universe requests the authorizing user's client inventory or client audit
- **THEN** no private client inventory or audit is returned

#### Scenario: Owner inspects a denied and successful action
- **WHEN** a client reads allowed content and then attempts an ungranted write
- **THEN** Connected apps shows its latest authenticated use and the audit attributes both outcomes to that client
- **AND** audit contains no raw token, prompt or response

#### Scenario: Client reads access without seeing other apps
- **WHEN** a granted outside client reads `read_graph target=access`
- **THEN** it receives its own effective client grant and otherwise authorized access data
- **AND** other clients' private inventory is absent while the protected owner app can show the complete inventory

### Requirement: Revoke invalidates credentials and dispatch immediately
Connected apps revoke SHALL atomically establish a durable owner/client fence across that owner's universes, invalidate existing access/refresh-token use at TinyAssets and prevent further admission on every daemon worker, including open sessions, queued work, resumed work and pending approvals. AuthKit provider revocation SHALL be attempted where supported without delaying or weakening local invalidation. Reconnect SHALL require fresh interactive authorization bound to a new generation, never reactivate pre-revoke credentials. A revoked tombstone SHALL NOT be replaced by first-connect defaults.

#### Scenario: Cached and refreshed credentials cannot bypass revoke
- **WHEN** the owner revokes an app and it calls through another worker or existing MCP session using an unexpired or refreshed credential
- **THEN** the daemon refuses before data or effects despite JWT validity
- **AND** other owners and independently authorized clients remain unaffected

#### Scenario: Revoke races with queued or approved work
- **WHEN** a client-originated effect reaches admission after the durable revoke boundary
- **THEN** it is refused even if queued or interactively approved earlier
- **AND** an effect already committed before that boundary has an honest in-flight/executed receipt rather than a false rollback claim

#### Scenario: Owner reconnects after revoke
- **WHEN** fresh interactive authorization establishes a provably new generation
- **THEN** the owner-authorized grant can become active for that generation only
- **AND** old tokens remain invalid; if generation cannot be proven, reconnect reports unavailable and the fence remains

### Requirement: Outside origin follows downstream work
Turns, runs, nested/scheduled work and pending actions SHALL retain verified outside-client origin alongside existing addressed-agent provenance. The daemon SHALL recheck the current client grant at each downstream data/tool/effect admission and resume, preventing a message from borrowing a resident agent's broader authority.

#### Scenario: Main is asked to bypass the calling app's grant
- **WHEN** a main-only read/message client asks main to read researcher data, launch a paid job or publish a page
- **THEN** downstream admission refuses the missing agent, control or costly grant before the prohibited access/effect
- **AND** it does not replace the originating client with unrestricted owner authority

#### Scenario: Grant narrows during a run
- **WHEN** the owner removes an agent or level before the next downstream dispatch
- **THEN** that dispatch uses the narrowed grant and refuses the now-ungranted operation

### Requirement: Sensitive actions reuse protected owner approval
Sensitive outside-client actions SHALL use the bound-action approval sheet and owner-editable policy of `inline-connect-and-approve`, including its standing decisions, with verified client and addressed-agent provenance visible to the owner and rechecked at dispatch. Bearer clients SHALL never approve. An effect approval SHALL NOT itself widen a client grant; grant edits remain protected owner actions.

#### Scenario: Client has costly but no matching owner decision
- **WHEN** an otherwise granted client requests a sensitive action requiring owner approval
- **THEN** the existing approval sheet presents that action with the originating client and addressed agent
- **AND** only the protected interactive owner approval path can dispatch it

#### Scenario: Client reads an approval link and tries to approve
- **WHEN** the bearer client supplies the pending action id, revision/hash or claimed interactive provenance
- **THEN** approval and dispatching retry are refused under inline-connect-and-approve
- **AND** the returned link directs the owner to the protected sheet

#### Scenario: Grant changes after preview
- **WHEN** an owner attempts to approve an action whose client grant was narrowed or revoked after preview
- **THEN** live grant/provenance revalidation prevents stale execution
