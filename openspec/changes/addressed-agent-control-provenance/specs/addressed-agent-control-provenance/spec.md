# addressed-agent-control-provenance

This delta implements the controls portion of universe-agent-harness section 4.18;
it does not replace that change's shared-brain or visibility requirements.

## ADDED Requirements

### Requirement: Agent control identity is captured by authenticated admission
The platform SHALL capture a versioned addressed-agent snapshot from existing owner/current-home admission and owned conversable binding records, and SHALL refuse missing or mismatched custom provenance rather than selecting main. The snapshot SHALL add no permission and SHALL agree with the existing enclosing execution authority.

#### Scenario: forged custom identity
- **WHEN** a tool payload, session selector or queue body names an agent not established by the authenticated turn/run record
- **THEN** admission refuses before model or effect dispatch and records no work for that claimed agent

#### Scenario: default main
- **WHEN** a fresh authenticated owner request omits an agent
- **THEN** admission explicitly captures main and preserves its existing conversation keys and authority checks

### Requirement: Launch transport authenticates one admitted identity
The trusted launcher SHALL mint a distinct per-launch transport credential bound server-side to the persisted turn/run, snapshot digest, owner, universe, expiry and launch lifecycle. The credential SHALL authenticate only the engine-launch audience and SHALL grant no effect permission. Non-secret references, shared owner bearers and caller-supplied selectors SHALL NOT substitute for it. Separate launch credentials SHALL be inaccessible across native sandboxes/processes; absent a proved isolation boundary, that native path SHALL remain held. Direct public-owner calls SHALL be distinguished by server-authenticated audience/context, never missing engine metadata. Exit, Stop, revocation and superseding resume SHALL invalidate the old binding.

#### Scenario: researcher replays main's reference
- **WHEN** researcher's valid launch credential accompanies main's reference, or an expired/stopped launch credential is reused
- **THEN** engine admission refuses without selecting main or creating work

#### Scenario: native isolation is not established
- **WHEN** a native process can read another launch's credential through shared environment, files, mounts or process access
- **THEN** native activation stays held; a shared token or an opaque reference cannot waive the missing boundary

#### Scenario: public owner and engine audiences differ
- **WHEN** a launch credential is used at public owner MCP, or an unbound engine call claims to be a direct owner
- **THEN** authentication refuses rather than admitting explicit main; independently authenticated public owner calls retain their existing consent semantics

### Requirement: Durable work retains the addressed agent
Turn and run records SHALL persist the validated snapshot; initial, nested, queued, retried and resumed work SHALL retain it through existing execution contexts and claim fences. Existing agent-manifest subjects SHALL remain authoritative for manifest work, and branch-version subjects SHALL not be replaced with agent subjects.

#### Scenario: researcher run resumes after process restart
- **WHEN** an admitted researcher run is resumed or its queued child starts
- **THEN** it reloads researcher's persisted identity, checks current scope, and never uses the browser's current selection or main's defaults

### Requirement: Current scope and control changes constrain further dispatch
Every engine-controlled model launch, engine tool admission, run/queue/nested/resumed admission and effector dispatch SHALL recheck existing owner/home/execution authority, the pinned custom binding revision/definition, and applicable current controls. Deletion, revision change, promotion or revocation SHALL hold further admissions through those doors; an acknowledged change preceding admission SHALL win, while already-admitted work SHALL retain honest outcome records. Native provider-internal tools SHALL NOT be represented as passing an engine pre-tool hook: revocation SHALL revoke launch authentication and request supervised termination, with confirmed exit or failure/unknown outcome recorded. Internal native activity before confirmed termination is an explicit residual; the separate D2 native-yield dependency remains held.

#### Scenario: researcher changes while work waits
- **WHEN** the owner revises or deletes researcher before a queued effect is admitted
- **THEN** the effect stays unsent and held, without replay or rebasing onto main

#### Scenario: native binding is revoked mid-loop
- **WHEN** a native turn's binding is revised or revoked while internal tools are running
- **THEN** no further engine/effector admission succeeds, supervised termination is requested and its observed outcome is recorded; internal actions before confirmed exit are not claimed prevented or undone

### Requirement: Rules and review use the acting agent
The effector SHALL select rules and review switches from the validated snapshot, retain existing standing grants and tighten-only review, and provide that agent's pinned instructions as untrusted review evidence. Owner Rules reads and changes SHALL bind the current home, selected agent and expected custom revision.

#### Scenario: researcher hands off while main allows
- **WHEN** researcher has a hand-off rule and main permits the same action with review disabled
- **THEN** researcher's actual effector door refuses the action, sends nothing and does not consult main's off switch

### Requirement: Requests preserve the asking agent through settlement
Creation, deduplication, suppression, served withdrawal and answer routing SHALL use server-captured asking-agent provenance. A served withdrawal SHALL atomically require the same asking agent, pending status and agent origin. The owner SHALL retain visibility/dismissal of historical requests; stale action-bearing requests SHALL not execute or continue under another identity.

#### Scenario: answer researcher while viewing main
- **WHEN** the owner answers researcher's valid request while main is selected
- **THEN** the answer continues researcher or reports a held continuation; it never silently continues main or exposes secret field values

#### Scenario: identical asks by two agents
- **WHEN** main and researcher raise identical asks and one is muted or withdrawn
- **THEN** the other's request and standing decision remain unchanged

### Requirement: Stop and journals identify the addressed agent
Served live turns and journals SHALL carry the captured agent. Addressed Stop SHALL target that owner's live agent turns, and stop-all SHALL require an explicit distinct request. Deleted-agent live work SHALL remain stoppable by its verified owner without restoring that agent's execution authority.

#### Scenario: two live agents
- **WHEN** the owner stops researcher while main also has a live turn
- **THEN** only researcher's turn is interrupted and both journals retain their own identities

### Requirement: Legacy compatibility does not invent provenance
New default-main owner requests SHALL remain compatible, but a migrated main default SHALL not authorize ambiguous historical work. Unverified queued/resumable work and stale action-bearing asks SHALL stay held for reissue unless existing authoritative lineage proves their identity. Mixed-version workers SHALL not execute new records while ignoring provenance.

#### Scenario: old queued row has only a default main column
- **WHEN** a consumer cannot prove whether that row originated from main or a custom agent
- **THEN** it holds the row without dispatch and does not backfill authority from the default

### Requirement: Legacy recurring work is visibly held until owner reconfirmation
Existing recurring definitions without provable addressed identity SHALL hold future firings, including main's definitions, and expose a durable effective held state and reason through existing automation list/get projections. The existing public owner automation resume door SHALL require explicit provenance confirmation, selected agent and expected revision before capturing a fresh snapshot. Only the definition's authenticated owner in their current home SHALL reconfirm after current binding and existing authored-branch/execution checks. A revision-guarded update SHALL persist provenance and advance existing activation/claim fencing for future work, preserving schedule/timezone/inputs/overlap without missed-fire replay. Admin pause/delete rights SHALL NOT authorize owner provenance creation. No timestamp/default inference SHALL grandfather work.

#### Scenario: ordinary main recurring definition crosses rollout
- **WHEN** an existing main automation has no proved snapshot
- **THEN** its future firings hold and list/get clearly report reconfirmation required, even if desired state remains active; ordinary resume does not silently restore dispatch

#### Scenario: owner reconfirms a held definition
- **WHEN** the stored owner explicitly confirms the displayed definition and agent through public resume at its current revision
- **THEN** only future work gets the newly validated snapshot; old queued/resumable work remains held, schedule semantics are retained and missed effects are not replayed

#### Scenario: engine or co-admin tries to reconfirm
- **WHEN** an engine launch or another admin supplies the confirmation payload
- **THEN** no owner snapshot is created and the held definition remains held
