## ADDED Requirements

### Requirement: Starter policy is editable content above minimal plumbing
The system SHALL implement the design's section-to-file mapping as seed `AGENTS.md` and on-demand memory, access, onboarding, time and workspace skills, with only name, one-line trigger and path resident per skill. It SHALL retain identity/first-person/honesty, the untrusted-envelope rule, code disclosure filtering and four tool definitions in plumbing, and use the existing extension mechanism. Owners SHALL be able to replace starter files and the main agent without hidden starter fallback or broader authority.

#### Scenario: A relevant task loads its guidance
- **WHEN** a starter handles a taught fact, missing access, incomplete setup, a time-relative task or workspace work
- **THEN** it loads the matching skill and performs the behavior in the design mapping
- **AND** unrelated skill bodies, GitHub recipes, directory inventories and command-center summaries are absent from resident instructions

#### Scenario: Replacement retains the floor
- **WHEN** an owner replaces the main agent or removes starter guidance
- **THEN** the chosen files control behavior without automatic restoration
- **AND** identity describes the selected agent and unauthorized grounding remains excluded in code

### Requirement: Versioned adoption preserves existing command centers
Provisioning SHALL use one immutable versioned bundle for new centers and D10. Existing centers SHALL follow the design's prepared/active/declined receipt protocol, hash-bound owner proposal, exclusive creates, atomic activation, backlog preservation and conditional rollback. The migration SHALL NOT overwrite user edits, interpret unreadable content as absence, follow links, auto-rewrite existing `AGENTS.md`, or restore deleted/declined seeds.

#### Scenario: Customized tiny has colliding skills
- **WHEN** migration finds a modified `AGENTS.md` and a starter skill path already occupied
- **THEN** both remain byte-for-byte unchanged and an inert candidate plus one visible proposal names the collision
- **AND** activation waits for owner acceptance of reachable skills, collision mappings or an explicit replacement/no-starter choice

#### Scenario: Retry races with an owner edit
- **WHEN** an owner edits a file after proposal preparation, or preparation/activation crashes
- **THEN** a stale hash cannot authorize a write and retries resume the same version without duplicate notifications or partial active content
- **AND** rollback preserves any file changed since installation

#### Scenario: A new center starts once
- **WHEN** a new center is provisioned and subsequently removes a starter file
- **THEN** it initially receives the published bundle and later startup does not recreate that file

#### Scenario: Existing lessons survive cutover
- **WHEN** an existing conversation has unresolved learned-cursor source turns
- **THEN** their verbatim owner-only review file and source IDs survive cutover without being marked learned
- **AND** the memory skill deduplicates and records completion only after verified persistence or an explicit no-fact decision; failures remain visible and pending

### Requirement: Cutover preserves reachability and proves cost and capability
Activation SHALL require deployed D6 discovery/invocation for all referenced capabilities, and global deletion SHALL wait until no center depends on the old renderer. D6 SHALL preserve old-starter reachability through its intermediate release or coordinate handle removal with seed activation. Acceptance SHALL meet the design's <1,000-token core, <=1,000-token starter and >=50% non-tool default-overhead reduction targets using the same pinned tokenizer/fixtures, separate D6 savings, ratchet measured counts, and pass the specified live founder proof. User content SHALL NOT be truncated to satisfy a budget.

#### Scenario: A D6 prerequisite is missing
- **WHEN** any required operation, including timezone reading, is unreachable
- **THEN** seed preparation can proceed but activation and old-path deletion wait

#### Scenario: Default savings are measured without masking regressions
- **WHEN** baseline, D6-only and final requests are compared
- **THEN** evidence separates resident components, user content, loaded bodies, total tokens and round trips
- **AND** natural live tasks demonstrate remembering across surfaces, a real access request, complete and partial onboarding, replacement-main behavior and unauthorized-tier refusal on the asserted deployed SHA
