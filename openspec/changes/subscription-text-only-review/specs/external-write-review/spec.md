## ADDED Requirements

### Requirement: Consequential external writes require independent review
The system SHALL require a completed enforced text-only review before consequential external writes by agents, tasks, or graphs to any user-connected platform, including issue/request creation, pushes, merges, and comments. The reviewer SHALL only tighten existing rules and consent. Legacy review-off settings SHALL NOT bypass this requirement; workspace-only actions and app reads remain outside it. The founder's repository SHALL receive no special authority.

#### Scenario: Ordinary user merges into a connected project
- **WHEN** an agent proposes a merge on a user's connected project platform and its destination grant and owner rules permit it
- **THEN** the common review boundary runs before the merge is dispatched
- **AND** a review failure leaves the merge unsent on GitLab, GitHub, and other connected platforms alike

#### Scenario: Background task posts a comment
- **WHEN** a scheduled task or graph proposes an external comment under a legacy review-off setting
- **THEN** required review still applies at the effect boundary
- **AND** direct calls and retries cannot evade it

### Requirement: Review capability describes proven enforcement
The system SHALL distinguish `enforced_text_only`, `confined_tools`, and `unsupported`, admitting only `enforced_text_only` to required review. Eligibility SHALL be bound to current adapter/executable version, configuration, and verified enforcement evidence, and checked both by routing and direct adapter entry points. A declaration or prompt alone SHALL NOT confer eligibility. Tool requests, executable extensions, session continuation, or conflicting tool grants SHALL cause refusal.

#### Scenario: Claude subscription exposes tool-disable flags but policy remains unproven
- **WHEN** the adapter can configure empty built-in tools and strict empty MCP but cannot prove managed policy and hooks inert before execution
- **THEN** required review is unavailable before model launch
- **AND** safe mode, empty allowed-tools, or a local settings scan does not set `supports_text_only=True`

#### Scenario: Codex has a read-only jail
- **WHEN** a Codex launch has an empty workdir, read-only sandbox, and constrained network but still has callable patch or utility tools
- **THEN** it does not meet enforced text-only
- **AND** at most a separately proven `confined_tools` tier is reported, which is ineligible for required review

#### Scenario: A native executor later proves the full contract
- **WHEN** a version-bound conformance result establishes no tools or executable extensions can run under the exact launch and policy configuration
- **THEN** that launch can be eligible as `enforced_text_only`
- **AND** stale, changed, or unknown policy/version evidence makes it ineligible again

#### Scenario: HTTP response mixes a verdict and a tool request
- **WHEN** an eligible HTTP review response contains a tool request alongside verdict text
- **THEN** the response does not complete review and no external write is sent
- **AND** unsupported request extensions are refused without removing source cost or privacy controls

### Requirement: Reviewer family comes from actual work provenance
The system SHALL derive producing model families from server-owned receipts for the action's originating work and select a reviewer whose verified model family differs from every material producing family. Family SHALL represent model lineage rather than provider adapter, protocol, access method, alias label, or endpoint. Unknown producer or reviewer lineage SHALL hold the action. Deterministic work with no model producer SHALL require an explicit server-proven provenance state and still receive review.

#### Scenario: Claude work is reviewed by GPT
- **WHEN** Claude produced the proposed work and the command center has an authorized GPT model with enforced text-only execution
- **THEN** an eligible GPT reviewer is selected without changing the conversation's Claude model or saved preference

#### Scenario: GPT work is reviewed by Claude
- **WHEN** GPT produced the proposed work and an authorized Claude model has proven text-only execution
- **THEN** Claude can review it regardless of whether that eligible connection uses HTTP or a subsequently proven subscription executor

#### Scenario: Two adapters expose the same family
- **WHEN** the work used Claude through a subscription and a second HTTP source also exposes Claude
- **THEN** changing adapters does not satisfy cross-family review
- **AND** an `api:<protocol>` value is not accepted as model-family evidence

#### Scenario: Effect-only graph retains producing provenance
- **WHEN** a code-only effect node submits a model-produced patch
- **THEN** it retains that patch's producing receipt lineage
- **AND** missing lineage cannot be relabeled deterministic to bypass diversity

#### Scenario: Several families contributed
- **WHEN** both GPT and Claude materially produced the proposed work
- **THEN** the reviewer must belong to a verified third family
- **AND** the action holds with a precise explanation if no eligible source exists

### Requirement: Reviewer authority is scoped separately from conversation selection
The system SHALL select only currently authorized, command-center-owned review candidates within accepted privacy, cost, invocation, and token limits. It SHALL admit the reviewer with its own binding, credential generation, account seat, and immutable review purpose. Selection SHALL preserve conversation assignment and saved defaults. Retries SHALL share the existing two-attempt ceiling and parent budget; enforcement conflicts SHALL hold immediately. No platform, host, founder, sender, or unrelated command-center credential SHALL be borrowed.

#### Scenario: An alternate eligible source is already accepted
- **WHEN** the preferred review source has a transient capacity failure and another different-family, enforced-text-only source is already authorized within the same limits
- **THEN** a remaining review attempt may select that source with fresh admission
- **AND** it records the actual reviewer without resetting the allowance or occupying the worker's different account seat

#### Scenario: Authority changes before dispatch
- **WHEN** the selected reviewer connection is revoked or its credential, family, or enforcement generation changes
- **THEN** dispatch refuses or re-admits current eligible authority before any provider call
- **AND** an old receipt is not used as a standing grant

### Requirement: Review receipts bind the exact effect and actual reviewer
The system SHALL bind review to the owner, command center, run, originating receipts, destination connection/incarnation, operation, method/path, complete payload or artifact digest, and current rule/consent generation. It SHALL durably record every review attempt's requested and actual provider/model/family, enforcement evidence, verdict or failure, usage, and timestamps in owner-visible run provenance. Unknown identity SHALL be explicit and SHALL NOT satisfy review. Submitted artifacts SHALL remain verbatim; materially inadequate review evidence SHALL hold the action.

#### Scenario: Payload changes after a passing review
- **WHEN** the body or patch changes while the destination and operation remain identical
- **THEN** the prior review does not authorize dispatch and a newly bound review is required

#### Scenario: Reviewer identity is omitted
- **WHEN** neither telemetry nor an enforced model pin proves the actual reviewing model and family
- **THEN** the attempt records unknown identity and the write remains held
- **AND** the system does not display the requested model as an observed actual model

#### Scenario: Audit persistence fails
- **WHEN** a valid verdict cannot be durably linked to its exact effect and reviewer
- **THEN** the external write remains unsent

### Requirement: Review unavailability produces actionable owner remediation
The system SHALL hold an unavailable, malformed, timed-out, or ineligible review without sending the effect, distinguish that outcome from a completed `needs_approval` verdict, and expose the safe reason on the run and a deduplicated owner request. Remediation SHALL name an existing eligible connection/model needing authorization, or the exact missing family and enforced-text-only capability through the normal Connections flow. It SHALL NOT recommend owner approval as a bypass, an unproven subscription login, or unconsented paid compute. Budget exhaustion SHALL remain a budget stop, not a new inference allowance.

#### Scenario: Only subscriptions with unproven enforcement are connected
- **WHEN** Claude-produced work has only an unproven Codex subscription reviewer available
- **THEN** the owner is told nothing was sent and that GPT-family enforced-text-only review is required
- **AND** guidance offers a verified owner-authorized HTTP source or a proven native executor without changing the conversation model or claiming the existing subscription is sufficient

#### Scenario: Owner approves an unavailable review
- **WHEN** the owner approves the planned action but no eligible review has completed
- **THEN** the write remains held with the connection/capability remedy

#### Scenario: Review asks for consent
- **WHEN** a valid, independently proven review returns `needs_approval`
- **THEN** the owner consent flow is bound to that exact action
- **AND** subsequent dispatch still checks current authority, unchanged content, and the completed review
