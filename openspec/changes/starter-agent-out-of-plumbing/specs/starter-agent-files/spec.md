## ADDED Requirements

### Requirement: Starter policy is editable content above minimal plumbing
The system SHALL implement the design's complete section mapping using seed AGENTS.md, separate resident starter/hooks.md and on-demand memory, access, onboarding, time and workspace skills, with only name, one-line trigger and path resident per skill. Generic loading SHALL read the selected agent's actual hook bytes before AGENTS.md, independently of AGENTS.md readability or skill selection, with source paths and explicit owner instructions taking precedence. The hooks SHALL contain the mapped memory, continuity, input-method, incomplete-onboarding, harness, grounding-current and response-priority advice; stock AGENTS.md SHALL NOT duplicate it. Plumbing SHALL retain factual founder clock data, identity/honesty, untrusted-envelope and nonce-history framing, input-method provenance, code disclosure filtering and founder-only exposure. Tool-enabled turns SHALL expose the four tool definitions; tool-less turns SHALL instead include the factual line "This turn has no tools available." derived from effective allowed_tools=() and tool_choice="none", independent of editable content. Owners SHALL be able to replace starter files and the main agent without hidden fallback or broader authority.

#### Scenario: Relevant guidance is available without losing the memory trigger
- **WHEN** a default starter handles an owner-taught durable fact
- **THEN** its resident editable hook directs immediate recall, persistence and verification, with detailed guidance in starter-memory
- **AND** unrelated skill bodies, service recipes, inventories and command-center summaries are absent from resident instructions

#### Scenario: A known timezone needs no discovery decision
- **WHEN** the platform knows the founder's timezone and the task is time-relative
- **THEN** a minimal factual resident line gives current timezone and local date/time without a skill load or a redundant timezone question
- **AND** turn data refreshes across timezone changes and local midnight; an unknown timezone is never guessed and private clock data is excluded from unauthorized tiers

#### Scenario: Tool availability is factual even with no starter instructions
- **WHEN** a default, customized, blank or replacement agent takes a turn with allowed_tools=() and tool_choice="none", including with starter/hooks.md absent
- **THEN** the request includes the factual no-tools line and no available tools, independently of AGENTS.md
- **AND** only prioritization advice comes from editable hooks; a tool-enabled turn does not receive the no-tools line

#### Scenario: Owner control includes the resident hooks
- **WHEN** the owner edits, empties, deletes or Undoes starter/hooks.md, or selects a replacement main agent
- **THEN** the loader uses only the selected agent's actual instruction files and never restores missing hook text or appends the old main's hooks
- **AND** D10 preserves the choice on later upgrades; linked/unreadable hooks produce a visible failure without traversal or fallback

#### Scenario: Replacement or deletion retains the floor
- **WHEN** an owner replaces the main agent, empties or deletes AGENTS.md, or its path is unreadable or linked
- **THEN** read_operating_instructions neither recreates the file nor returns DEFAULT_OPERATING_INSTRUCTIONS, and safe reading reports actual failures without substituting stock guidance
- **AND** the selected agent identity, code-level disclosure filtering, nonce-delimited untrusted / NOT consent history and client-reported informational input-method label remain

### Requirement: API reference stays current while owner guidance wins
The starter SHALL link to D6-owned platform-versioned read-only handbook references through ta describe or linked skill reference and SHALL NOT copy chapters into editable seeds. Owner-authored skills and instructions SHALL take precedence over reference workflow recommendations within existing authority; authoritative API schemas and code permission checks SHALL remain enforced.

#### Scenario: An owner overrides a handbook workflow
- **WHEN** an owner supplies their own skill, instructions or replacement agent
- **THEN** their workflow guidance wins and the current API reference remains available on demand
- **AND** API examples stay versioned/tested with the platform rather than fossilized in a customized seed copy

### Requirement: Every center cuts over through D10's single seed mechanism
The starter SHALL consume starter-seed-lifecycle for both provisioning and existing centers and SHALL NOT implement its own receipt/acceptance protocol. This change alone SHALL own consumer/renderer cutover integration and the all-center/dormant-center acceptance proof; seed-lifecycle SHALL provide the prerequisite transaction API. At the coordinated release boundary it SHALL switch every center to the new renderer and remove the old renderer, without owner-response, collision-mapping or dormant-center gates. Untouched or seed-identical files SHALL upgrade automatically; customizations and owner deletions SHALL remain with a visible offer under D10's per-path rules. Proven new starter/hooks.md and skill paths SHALL install alongside preserved AGENTS.md, with a note explaining where former runtime guidance now loads and how to edit/delete/Undo it. D7 learning extraction SHALL remain separately owned and SHALL NOT be selected by seed adoption.

#### Scenario: No owner reviews a custom-file offer
- **WHEN** migration finds customized AGENTS.md and a colliding skill and receives no owner response
- **THEN** those files remain unchanged, never-installed starter/hooks.md and noncolliding starter skills are added, and the center runs the new plumbing with resident hooks alongside its custom AGENTS.md
- **AND** the generic index exposes installed starter and owner skills without an AGENTS.md amendment; the visible note explains the moved guidance, edit/delete/Undo controls, and any preserved collision's unavailable guidance and offered candidate

#### Scenario: A legacy instructions file previously invoked defaults
- **WHEN** legacy AGENTS.md is empty, linked or unreadable at cutover
- **THEN** its bytes/path remain untouched while new hook/skill paths install independently
- **AND** the notice states the specific condition, that the old runtime supplied defaults, that the new runtime reads actual files with no fallback, and which hooks/skills now supply guidance or could not be installed

#### Scenario: A dormant center wakes after cutover
- **WHEN** a previously dormant center next starts a turn
- **THEN** D10 resumes its safe file transaction and receipt before that new-renderer turn
- **AND** no old-renderer compatibility path exists; file Undo cannot re-enable it

### Requirement: Cost acceptance belongs to this slice and is per adapter
Activation SHALL require deployed D6 discovery/invocation and D6's core-plus-four-schemas <1,000-token precondition. This slice SHALL independently achieve starter instructions/index/hooks <=1,000 tokens, at least 50% less non-tool resident default overhead than D6-only, and full-task total tokens for every fixture task <= D6-only for each supported adapter using matched fixtures/settings and extraction state. Evidence SHALL separate components, all calls and loaded results, SHALL NOT credit D6/D7 savings or handbook size, and SHALL NOT truncate owner content to pass.

#### Scenario: D6 is not ready
- **WHEN** a required recipe, permission check, handbook link or D6 core budget is unproved
- **THEN** content preparation may proceed but activation waits for that technical prerequisite
- **AND** known clock data continues to use the existing stored account-timezone path, not a speculative new D6 read

#### Scenario: A skill-load round trip erases the resident savings
- **WHEN** a matched cold-start or follow-up fixture uses more total tokens than D6-only on HTTP, Codex, Claude or another supported adapter
- **THEN** that adapter fails acceptance even if the resident target passes
- **AND** the report includes all input/output tokens, extraction requests, skill loads and calls and requires rework rather than pooling adapters

### Requirement: Remembering and live capabilities do not regress
Both the default starter and a preserved customized-AGENTS fixture containing none of the moved hooks SHALL pass N>=10 paired natural fact-teaching trials per supported model family against matched extraction-on baselines. The custom fixture SHALL retain identical owner bytes and receive hooks/skills without owner acceptance. Verified immediate durable-write rate and later cross-surface recall rate SHALL each be no worse than baseline in every family/fixture cell, reported separately. Acceptance SHALL also pass the design's negative controls, clock and hook-control cases and natural live memory/access/onboarding/replacement-main/unauthorized-tier proofs on an asserted deployed SHA. D7 SHALL repeat the entire two-fixture memory comparison with extraction off before retiring it.

#### Scenario: One family loses learned facts
- **WHEN** either write rate or recall rate is below extraction-on baseline in one family/fixture cell
- **THEN** acceptance fails for that cell regardless of pooled results or a successful demonstration
- **AND** the evidence reports numerator/denominator, fixtures, model settings and traces, and keeps the minimal resident hook while correcting the regression

#### Scenario: Natural live use preserves the intended behavior
- **WHEN** tiny receives fact, access and time-relative requests without naming skills, and a disposable starter resumes partial onboarding
- **THEN** the design's live proofs show persisted recall, the right real access request, no repeated known-timezone question, and missing-field-only onboarding without restarting tiny's setup
