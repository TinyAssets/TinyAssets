## ADDED Requirements

### Requirement: Every outside call carries verified client attribution
The daemon SHALL bind verified issuer/client id and registered CIMD/DCR metadata (name, logo and redirect host, or explicit unavailable values) to the principal on every authenticated outside-client bearer request across MCP and HTTP, preserving the existing owner/home identity independently of client identity. Display metadata SHALL NOT grant authority. Missing or conflicting verified client identity SHALL refuse before owner data or effects.

#### Scenario: Same owner connects two different apps
- **WHEN** the same owner authorizes Muse and OpenClaw through AuthKit CIMD/DCR
- **THEN** both resolve to the same owner/home with distinct verified client identities and independent grants
- **AND** neither registration requires brand-specific code

#### Scenario: Caller spoofs client metadata
- **WHEN** a call claims another client id, name or redirect in its payload or untrusted headers
- **THEN** attribution comes only from the verified OAuth binding and registered metadata
- **AND** missing verified identity cannot fall back to full-account authority

### Requirement: Owners delegate explicit agent and action grants
The daemon SHALL store owner-editable grants keyed by owner, universe, issuer and client id, containing allowed agent ids or explicit wildcard, independent `read`, `message`, `control`, `costly` levels, optional expiry, status and revision. New clients SHALL snapshot the owner-editable starter default once; the initial suggested default SHALL be `read` + `message` for `main` with `message-only`. Owners SHALL be able to change starter agents, levels and message mode without resetting existing grants. Each grant SHALL include owner-editable `message_mode` (`message-only` or `message-and-act`). Existing primary connectors SHALL retain their pre-cutover authority in explicit migration grants until the owner narrows them, or receive owner-selected grants before cutover, never silently regress to the starter. `costly` SHALL cover spend, publish and external posts in addition to the operation's required ordinary level; it SHALL NOT imply other levels, owner approval or resource authority. Cross-user isolation SHALL remain the only immutable platform behavioral invariant; all client actions remain strictly within the authorizing owner's authority and editable grants.

Grant resolution SHALL follow authenticated home resolution. For a new user, the existing first-converse private-home bootstrap SHALL verify client identity and its revocation fence, create the default grant with the home in the same admission step and check that grant before delivering the message. Early no-home status SHALL require verified active client identity and its revocation fence, expose only the caller's no-home state and never provision. Existing or revoked grants SHALL NOT be reset by bootstrap.

#### Scenario: A new user's first client message
- **WHEN** a newly authenticated user without a home connects a verified active client and calls `converse` without a graph selector
- **THEN** existing home resolution creates the private home and a snapshot of the owner's starter grant before admitting the message
- **AND** a preceding status read reports no home without provisioning, and a revoked client cannot trigger bootstrap

#### Scenario: First connection has a useful narrow default
- **WHEN** a newly verified client connects with the initial suggested starter unchanged, including concurrent first calls
- **THEN** one grant allows main-scoped reads and messages to main
- **AND** control, costly actions and addressing any other agent refuse with the missing grant

#### Scenario: Owner changes the starter before a new connection
- **WHEN** the protected owner app saves a starter selecting researcher, control and message-and-act before a new client connects
- **THEN** the new client snapshots those owner-selected settings instead of the initial suggestion
- **AND** existing clients retain their current grants unchanged

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
- **AND** old tokens remain invalid; if verified `auth_time` or `sid` cannot distinguish fresh authorization, protected owner re-consent binds a newly completed interactive OAuth credential family to the new generation while all old/unbound credentials stay fenced
- **AND** owner recovery is available before enforcement, with no permanent reconnect lockout or timestamp-only fence clearing

### Requirement: Outside origin follows downstream work
Turns, runs, nested/scheduled work and pending actions SHALL retain verified outside-client origin alongside existing addressed-agent provenance. The daemon SHALL recheck current generation, expiry, revocation, kill switch, original addressed agent's message permission and owner-selected message mode at each downstream data/tool/effect admission and resume. `message-only` SHALL bound downstream actions to the current client agents/levels. `message-and-act` SHALL allow the addressed resident agent to act with its own authorized powers and downstream delegation under existing owner/resource and approval policy; direct bearer calls SHALL still require their own client grant. Neither mode SHALL allow self-elevation or bearer approval.

#### Scenario: Main is asked to bypass the calling app's grant
- **WHEN** a main-only read/message client in message-only mode asks main to read researcher data, launch a paid job or publish a page
- **THEN** downstream admission refuses the missing agent, control or costly grant before the prohibited access/effect
- **AND** it does not replace the originating client with unrestricted owner authority

#### Scenario: Grant narrows during a run
- **WHEN** the owner selects message-only and removes an agent or level before the next downstream dispatch
- **THEN** that dispatch uses the current mode and narrowed grant and refuses the now-ungranted operation

#### Scenario: Owner delegates agent action through messages
- **WHEN** the owner chooses message-and-act for a client with message access to main and no direct control grant
- **THEN** main may perform owner-authorized actions in response using its own powers and existing approval policy
- **AND** the client's direct control calls still refuse and all resulting work retains revocable client origin

### Requirement: Sensitive actions reuse protected owner approval
Sensitive outside-client actions SHALL use the bound-action approval sheet and owner-editable policy of `inline-connect-and-approve`, including its standing decisions, with verified client and addressed-agent provenance visible to the owner and current grant/message-mode policy rechecked at dispatch. Message-and-act work SHALL use the delegated resident-agent authority defined above, not require the direct-call control/costly grants as well. Bearer clients SHALL never approve. An effect approval SHALL NOT itself widen a client grant; grant edits remain protected owner actions.

#### Scenario: Client has costly but no matching owner decision
- **WHEN** an otherwise granted client requests a sensitive action requiring owner approval
- **THEN** the existing approval sheet presents that action with the originating client and addressed agent
- **AND** only the protected interactive owner approval path can dispatch it

#### Scenario: Client reads an approval link and tries to approve
- **WHEN** the bearer client supplies the pending action id, revision/hash or claimed interactive provenance
- **THEN** approval and dispatching retry are refused under inline-connect-and-approve
- **AND** the returned link directs the owner to the protected sheet

#### Scenario: Grant changes after preview
- **WHEN** an owner attempts to approve an action whose current grant/message-mode policy no longer authorizes the action or whose client was revoked after preview
- **THEN** live grant/provenance revalidation prevents stale execution

### Requirement: First-party identity is exempt from outside grants
The configured TinyAssets web/phone/desktop first-party app SHALL be classified by verified issuer and exact platform-configured client id, independently of display metadata. It SHALL remain exempt from outside grants and the outside-client kill switch on both MCP and HTTP. Existing owner/ACL checks SHALL remain. Grant management and action approval SHALL still require a protected interactive owner session; first-party bearer status alone SHALL NOT supply that proof.

#### Scenario: First-party app continues after cutover
- **WHEN** enforcement is enabled or outside admission is killed
- **THEN** the verified first-party app can still read, write and run within the owner's authority without an outside grant
- **AND** its protected owner session can widen grants while an outside app spoofing its name or redirect receives no exemption

### Requirement: Every bearer surface has explicit outside admission
Every bearer-accepting HTTP route SHALL either use the same current grant/resource admission as MCP or refuse outside clients before data or effects. The route inventory in `bearer-surfaces.md` SHALL be covered by mounted-route/method tests, including all `/app/api/*`, other `/app/*`, `/mcp/pulse` and private bearer services. This slice SHALL refuse outside OAuth clients on all app HTTP routes; private service bearers SHALL NOT accept outside OAuth tokens or erase forwarded outside origin. Unclassified bearer routes SHALL fail closed for outside clients.

#### Scenario: Outside token tries owner HTTP routes
- **WHEN** an outside client's valid token calls any listed app HTTP route, including `/app/api/read`, file upload, connections, model settings, notifications or approvals
- **THEN** it is refused before returning owner data or performing effects, even if its MCP grant includes wildcard/control/costly
- **AND** the configured first-party app retains its existing authorized access

### Requirement: One durable authority store fences all workers and universes
OutsideClientAuthority SHALL store grants, owner defaults, credential-generation bindings, audit, the admission switch and owner/issuer/client revocation fences in the daemon-owned `<data>/.outside-client-authority.sqlite3` outside universe directories. All workers SHALL consult the same transactional authority service on admission, without per-worker copies or positive authorization caches. Grants SHALL retain universe keys; fences SHALL span that owner's universes. Store loss/unavailability SHALL refuse outside clients without blocking verified first-party or trusted owner/universe work.

#### Scenario: Revoke crosses universes and workers
- **WHEN** a revoke commits through one worker for an owner/client used in two universes
- **THEN** the next request or effect admission through any worker in either universe refuses that generation
- **AND** no replica, refresh, local cache or new empty database restores it

### Requirement: Owner controls and safe cutover precede enforcement
Enforcement SHALL NOT activate until the protected owner session from `inline-connect-and-approve` and Connected apps grant widening, narrowing, starter/message settings, revoke and reconnect recovery are deployed and tested. Existing primary connectors SHALL be grandfathered with equivalent authority or have visible owner-selected grants before cutover. Unresolved legacy client identity SHALL be reconciled with owner notice/reconnect before that owner's cutover. Trusted universe/owner-run automations and queued runs SHALL NOT be classified or held as outside work; actual outside-origin work SHALL retain its attribution.

#### Scenario: Protected owner controls are unavailable
- **WHEN** the owner cannot widen a grant through the protected app
- **THEN** enforcement cutover is blocked rather than shipping an inaccessible owner control

#### Scenario: Existing primary connectors and automations survive migration
- **WHEN** an owner already uses claude.ai and ChatGPT and has owner/universe automations or queued runs
- **THEN** migration preserves connector authority or obtains visible owner choices before cutover
- **AND** trusted owner/universe work continues without outside-grant holds

### Requirement: Rollback retains outside-only fail-closed admission
The NEW code SHALL implement a durable operator-controlled `outside_admission_enabled` kill switch before/at enforcement. False, unreadable or invalid switch state SHALL deny outside ingress and downstream admission, including sessions and queued work; a process override SHALL only force denial. Verified first-party app clients and trusted owner/universe work SHALL be exempt. Before/at rollback operators SHALL activate and verify the switch and retain the new admission layer/store or a tested equivalent backport. Rollback to old bearer-only code SHALL NOT be permitted.

#### Scenario: Operator disables outside dispatch before rollback
- **WHEN** the switch is false and another worker receives an outside request or resumes outside work
- **THEN** it refuses on the next admission while first-party read/write/run and protected owner controls still work
- **AND** rollback preserves fences and cannot revive previously revoked credentials

### Requirement: Live acceptance is independent of Muse availability
Implementation acceptance SHALL include a rendered live pass with two independently registered clients on one owner account (claude.ai plus ChatGPT, or OpenClaw), showing independent grants, missing-grant refusal, revoke on the next call, the other client unaffected and first-party read/write/run plus protected grant widening after cutover. Muse connection/callback and approval proof SHALL remain additional, with a precise blocker recorded if unavailable; a Muse blocker SHALL NOT substitute for mandatory live proof.

#### Scenario: Muse callback cannot complete
- **WHEN** the additional Muse connection is blocked by callback configuration
- **THEN** acceptance still requires the full two-client and first-party live pass with sanitized receipts
- **AND** recording only the Muse blocker does not satisfy acceptance
