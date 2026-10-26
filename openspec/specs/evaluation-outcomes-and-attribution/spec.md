# Evaluation, Outcomes, and Attribution

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

The quality-and-provenance layer: NL-only run judging, node auto-promotion/flagging, deterministic KEEP rubric and conformance packs, flag-gated outcome gates with cited-in attestations, and append-only contribution/attribution ledgers feeding a selector-driven leaderboard.
## Requirements
### Requirement: Run and node judgment is natural-language only, with no numeric or AI-in-the-loop scoring
The evaluation loop (`tinyassets.api.evaluation`) SHALL let a caller attach a free-text judgment to a whole run or to a specific node via `judge_run`, and SHALL NOT assign a numeric quality score or invoke an LLM to grade the output. The `suggest_node_edit` action SHALL only assemble context — the current node body, recent run outputs, and node-scoped judgments — for the calling chatbot to act on, and SHALL NOT itself call a model to produce the edit. Recording a judgment and rolling back a node SHALL be the only writes in this loop and SHALL append a global-ledger entry, while the remaining actions SHALL be read-only and bypass the ledger.

#### Scenario: judge_run records a natural-language judgment
- **WHEN** `judge_run` is called with a `run_id` and non-empty `judgment_text`, optionally scoped to a `node_id`
- **THEN** the judgment is persisted with its author and tags and the response status is `recorded`, with no numeric score assigned
- **AND** a global-ledger entry is appended for the write

#### Scenario: suggest_node_edit assembles context without calling a model
- **WHEN** `suggest_node_edit` is called with a `branch_def_id` and `node_id`
- **THEN** it returns the current node body, recent outputs, and node-scoped judgments framed for the client to edit
- **AND** it does not invoke an LLM and does not itself mutate the node

#### Scenario: node rollback preserves forward history
- **WHEN** `rollback_node` restores a node to an earlier recorded version
- **THEN** the branch version is bumped and a new audit row with `edit_kind="rollback"` is recorded so the replaced body stays retrievable via `list_node_versions`

### Requirement: Nodes auto-promote and auto-flag from observed execution outcomes
The node evaluator (`tinyassets.node_eval`) SHALL record each node execution's success, duration, and any downstream eval signal, and SHALL automatically transition a node's status from observed outcomes. A node in `trial` SHALL auto-promote to `promoted` once it reaches at least 10 executions with a success rate of 85% or higher (and, when eval scores exist, an average at or above the eval-score floor). A node in `trial` or `promoted` SHALL auto-flag to `flagged` when its success rate falls to 50% or below over at least 5 executions, or after 3 consecutive failures. A host override SHALL be able to set any status regardless of the automatic rules.

#### Scenario: trial node auto-promotes on sustained success
- **WHEN** a `trial` node accumulates at least 10 executions at a success rate of 85% or higher
- **THEN** its status transitions to `promoted` with a recorded reason

#### Scenario: node auto-flags on consecutive failures
- **WHEN** a `trial` or `promoted` node records 3 consecutive failed executions
- **THEN** its status transitions to `flagged` with a reason citing the consecutive failures

#### Scenario: host override wins over automatic rules
- **WHEN** a host manually sets a node's status
- **THEN** the status is stored with the overriding actor and is not undone by the automatic transition logic on that write

### Requirement: The coding-packet rubric is a pure, deterministic KEEP validator
The coding-packet/release-packet rubric (`tinyassets.coding_packet_rubric`) SHALL validate a packet with no IO, network, repo writes, or LLM judgment, returning a deterministic decision with per-violation `rubric_violation` records. A packet that claims KEEP SHALL be blocked unless it satisfies every KEEP criterion, including a release score of at least the fixed `KEEP_SCORE_MIN` of 9.0, a completed child run, present child-output evidence, a complete evidence bundle, and an approved release gate. A packet that claims to have shipped SHALL be blocked unless it carries a resolvable commit SHA or PR URL.

#### Scenario: KEEP claim below the score threshold is blocked
- **WHEN** a packet claims KEEP but its release score is below 9.0
- **THEN** validation returns `blocked` with a `release_score_below_keep_threshold` violation

#### Scenario: shipped claim without a repo handle is blocked
- **WHEN** a packet claims shipped/merged but carries neither `commit_sha` nor `pr_url`
- **THEN** validation returns `blocked` with a `shipped_without_repo_handle` violation

#### Scenario: validation is deterministic and side-effect-free
- **WHEN** the same packet is validated twice
- **THEN** both calls return the same result and neither performs any IO, network, or model call

### Requirement: Conformance packs are readiness evidence that gate outcome-gate rungs
Conformance packs (`tinyassets.conformance_packs`) SHALL record standards/readiness evidence bundles whose status is one of `ready`, `blocked`, `partially-satisfied`, or `requires-human-review`, and a pack SHALL be forced to `blocked` whenever any validator or explicit blocker is present. The substrate SHALL be discipline-neutral: built-in validation SHALL apply only to recognized standards (for example `research-publication-v0`), and unknown standards SHALL default to `requires-human-review` rather than a false `ready`. A gate rung that requires a conformance pack SHALL only be satisfiable by a pack whose status is `ready` and whose goal, branch, target rung, and standard match the claim.

#### Scenario: missing required evidence forces blocked
- **WHEN** a `research-publication-v0` pack is recorded with required evidence fields missing
- **THEN** its status is `blocked` and the missing fields are listed as blockers

#### Scenario: a non-ready pack cannot satisfy a rung that requires one
- **WHEN** a gate claim is made for a rung that requires a conformance pack and the referenced pack is not `ready`
- **THEN** the claim is rejected with `conformance_pack_not_ready`

### Requirement: The outcome-gates surface is flag-gated and enforces evidence, rebinding, and conformance
The outcome-gates surface in `tinyassets.api.market` SHALL be gated behind `GATES_ENABLED=1` and disabled by default. When enabled it SHALL support gate ladders and evidence-backed rung claims, where each claim SHALL require a valid evidence URL (an http(s) URL with a host, or a TinyAssets run evidence handle), SHALL be rejected as `branch_rebound` when a non-retracted claim for the same branch and rung exists under a different Goal, and SHALL require a matching `ready` conformance pack for any rung that demands one. Monetary gate bonus actions SHALL NOT be registered or executable. Existing bonus columns and stored history SHALL remain untouched by runtime retirement.

#### Scenario: an invalid evidence URL is rejected
- **WHEN** a gate claim is submitted with an evidence value that is neither an http(s) URL with a host nor a TinyAssets run evidence handle
- **THEN** the claim is rejected with an evidence-URL validation error

#### Scenario: a rebound branch cannot re-claim under a new Goal
- **WHEN** a branch already has a non-retracted claim for a rung under one Goal and a new claim for the same rung is made after the branch was rebound to a different Goal
- **THEN** the claim is rejected with `branch_rebound` and the caller is told to retract the original claim first

### Requirement: Gate events are cited-in outcome attestations with a controlled verification lifecycle
Gate events (`tinyassets.gate_events`) SHALL record real-world outcome attestations whose relationship to a branch version is expressed as "cited in" a gate event and never as having "caused" the outcome. A gate event's verification status SHALL be one of `attested`, `verified`, `disputed`, or `retracted`, and a record SHALL start `attested`. Verification SHALL only be applied to an `attested` event by a verifier who is not the attester; a retracted event SHALL not be disputed; and retraction SHALL preserve the record as an audit trail rather than deleting it.

#### Scenario: self-verification is refused
- **WHEN** a caller attempts to verify a gate event using the same identity that attested it
- **THEN** the verification is refused because the verifier must differ from the attester

#### Scenario: verification requires the attested state
- **WHEN** verification is attempted on a gate event that is not in `attested` status
- **THEN** the verification is refused

#### Scenario: retraction preserves the record
- **WHEN** a gate event is retracted
- **THEN** its status becomes `retracted` and the prior attestation data is preserved for audit

### Requirement: Contribution and attribution are append-only ledgers with idempotent, bounded provenance
The contribution ledger (`tinyassets.contribution_events`) SHALL be a single append-only table whose `weight` is a signed real value (positive for credit, negative for regression), SHALL be idempotent on a caller-supplied `event_id` via insert-or-ignore, and SHALL be emitted from the run-completion, rollback, and graph-compilation paths. Attribution edges (`tinyassets.api.market` record-remix) SHALL record parent-to-child provenance with `credit_share` clamped into `[0, 1]`, SHALL reject a cycle by walking up to 50 ancestor hops before insert, and SHALL derive each edge's generation depth as the parent's maximum depth plus one.

#### Scenario: duplicate contribution event is ignored
- **WHEN** two contribution events are recorded with the same caller-supplied `event_id`
- **THEN** only the first is inserted and the second is silently skipped

#### Scenario: credit share is clamped to the unit interval
- **WHEN** a remix edge is recorded with a `credit_share` outside `[0, 1]`
- **THEN** the persisted credit share is clamped into `[0, 1]`

#### Scenario: an attribution cycle is rejected
- **WHEN** a remix edge would make the child an ancestor of the parent
- **THEN** the edge is rejected with a cycle-detected error and no edge is written

### Requirement: The quality leaderboard collects signals and delegates ranking to a user-buildable selector
The quality leaderboard (`tinyassets.api.quality_leaderboard`) SHALL collect per-branch signals (run counts, judgment scores, fork counts, top gate rung, publish-safety) and SHALL NOT apply any platform-owned scoring weights, all of which were removed under DESIGN-008. Ranking SHALL be produced by dispatching the Goal's selector branch — an explicit binding or the platform default — over the collected signals, and each leaderboard build SHALL trigger at most one selector LLM call, short-circuiting to an empty result with zero calls when there are no candidates. The substrate SHALL ignore selector-emitted branch version ids in favor of the authoritative active version and SHALL drop any selector-fabricated branch id that was not in the visibility-filtered candidate set.

#### Scenario: ranking comes from the selector, not a platform formula
- **WHEN** a leaderboard is built for a Goal with candidate branches
- **THEN** the collected signals are passed to the Goal's selector branch and the selector's emitted scores and rationale determine the ranking

#### Scenario: an empty candidate set burns no model call
- **WHEN** a leaderboard is built for a Goal with no visible candidate branches
- **THEN** an empty leaderboard is returned without dispatching a selector LLM call

#### Scenario: fabricated selector output is rejected
- **WHEN** the selector emits a branch id that was not in the candidate set, or a branch version id that disagrees with the authoritative active version
- **THEN** the fabricated branch id is dropped and the authoritative version id is used instead of the selector's value

### Requirement: Recorded outcome events are persistent unverified evidence records

The `extensions` outcome registry SHALL persist each accepted outcome as a
unique `outcome_event` row with a caller-supplied non-empty run ID, one of
`published_paper`, `merged_pr`, `deployed_app`, `won_competition`, or `custom`,
a generated outcome ID and recorded timestamp, and optional evidence URL,
gate-event linkage, JSON payload, and note. Recording MUST leave `verified_at`
and `verified_by` unset, MUST NOT require that the run ID already exists, and
MUST NOT invoke an evaluator, prober, or produce `EvalResult`; generic outcome
adapters remain owned by `evaluation-runtime-and-scenarios` and run only when a
caller explicitly invokes them.

#### Scenario: An outcome round-trips without ambient verification

- **WHEN** a caller records a supported outcome type against a non-empty run ID
  that is not present in the run store, with optional evidence and payload
- **THEN** the registry returns a generated outcome ID and timestamp and `get_outcome`
  retrieves the stored run ID, type, evidence, linkage, payload, and note
- **AND** the retrieved `verified_at` and `verified_by` fields remain unset
- **AND** recording performs no network probe or evaluator call

#### Scenario: An unsupported outcome type is rejected before persistence

- **WHEN** `record_outcome` receives an outcome type outside the five supported
  values
- **THEN** it returns an error containing the valid type set
- **AND** it writes no outcome row for that request

#### Scenario: Outcome listing applies current filters and bounds

- **WHEN** `list_outcomes` is called with a run ID and/or outcome type, or with
  a branch whose runs can be resolved and no explicit run ID
- **THEN** it returns matching records newest first, defaults an invalid limit
  to 50, and caps a positive requested limit at 200 rows
- **AND** an unresolved branch filter returns an empty list rather than
  inventing outcome records

#### Scenario: Explicit run filtering takes precedence over branch filtering

- **WHEN** `list_outcomes` receives both `run_id` and `branch_def_id`
- **THEN** the current handler filters on the explicit run ID and does not
  resolve or apply the branch filter

#### Scenario: A negative outcome limit is not safely bounded

- **WHEN** `list_outcomes` receives a negative numeric limit
- **THEN** the current handler passes that negative value to SQLite rather than
  clamping it to a positive bound
- **AND** the query can therefore return more than 200 records under SQLite's
  negative-limit semantics

### Requirement: Agent component attribution is append-only and locally verified
The attribution system SHALL preserve each public agent remix as append-only child-component to parent-component lineage, SHALL derive bounded generation depth from verified local parents, SHALL keep per-child-component credit at or below `1.0`, and SHALL distinguish unresolved external origin declarations from verified attribution edges.

#### Scenario: Verified local component sources earn attribution
- **WHEN** a child agent component cites existing parent components in the local public commons
- **THEN** the platform writes immutable verified lineage edges with their declared credit shares and derived generation depth

#### Scenario: External origin does not manufacture local credit
- **WHEN** an imported portable definition declares a parent component that is not resolvable in the local commons
- **THEN** the platform may preserve that declaration as informational origin metadata
- **AND** it does not write a verified attribution edge or assign local credit for that declaration
