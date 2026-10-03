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

### Requirement: Durable work retains the addressed agent
Turn and run records SHALL persist the validated snapshot; initial, nested, queued, retried and resumed work SHALL retain it through existing execution contexts and claim fences. Existing agent-manifest subjects SHALL remain authoritative for manifest work, and branch-version subjects SHALL not be replaced with agent subjects.

#### Scenario: researcher run resumes after process restart
- **WHEN** an admitted researcher run is resumed or its queued child starts
- **THEN** it reloads researcher's persisted identity, checks current scope, and never uses the browser's current selection or main's defaults

### Requirement: Current scope and control changes constrain further dispatch
Every new model/tool/effect admission SHALL recheck existing owner/home/execution authority, the pinned custom binding revision/definition, and the applicable current controls. Deletion, revision change, promotion or revocation SHALL hold further dispatch; an acknowledged change preceding admission SHALL win, while already-admitted work SHALL retain honest outcome records.

#### Scenario: researcher changes while work waits
- **WHEN** the owner revises or deletes researcher before a queued effect is admitted
- **THEN** the effect stays unsent and held, without replay or rebasing onto main

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
