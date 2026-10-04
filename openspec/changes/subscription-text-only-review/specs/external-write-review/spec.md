## ADDED Requirements

### Requirement: Consequential external writes require independent review
The system SHALL require a completed `enforced_text_only` or `contained` review before consequential external writes by agents, tasks, graphs or their custom tools to any user-connected software or platform, including issue/request creation, pushes, merges, and comments. The reviewer SHALL only tighten existing rules and consent. Legacy review-off settings SHALL NOT bypass this requirement; workspace-only actions and app reads remain outside it. The founder's account and receiver SHALL receive no special authority. Owner-authorized API-key HTTP review SHALL remain acceptable but SHALL NOT be required for subscription-only users with eligible reviewers.

#### Scenario: Ordinary user merges into a connected project
- **WHEN** an agent proposes a merge on a user's connected project platform and its destination grant and owner rules permit it
- **THEN** the common review boundary runs before the merge is dispatched
- **AND** a review failure leaves the merge unsent on GitLab, GitHub, and other connected platforms alike

#### Scenario: Background task posts a comment
- **WHEN** a scheduled task or graph proposes an external comment under a legacy review-off setting
- **THEN** required review still applies at the effect boundary
- **AND** direct calls and retries cannot evade it

### Requirement: Review capability describes proven enforcement
The system SHALL distinguish `enforced_text_only`, `contained`, and `unsupported`, admitting either proven tier to required review. Eligibility SHALL be bound to current adapter/executable/image version, configuration, mount and transport profiles, and verified enforcement evidence, and checked by routing and direct adapter entry points. A declaration or prompt alone SHALL NOT confer eligibility. `supports_text_only` SHALL remain false for contained execution; a distinct server-owned review requirement SHALL accept either tier. Session continuation or grants beyond the proven profile SHALL cause refusal. Tool requests or executable extensions SHALL invalidate text-only execution; local tool use confined under the complete contained profile SHALL NOT by itself invalidate its verdict.

#### Scenario: Claude has a complete launch-time absence proof
- **WHEN** the pinned launch proves empty built-in tools and MCP, isolated config with no setting sources, no executable extensions, and absence of all applicable managed sources before execution and throughout the call
- **THEN** that launch qualifies as `enforced_text_only` with `supports_text_only=True`
- **AND** stale image, credential mode, policy, configuration or executable evidence invalidates the proof

#### Scenario: Claude local files are absent but remote hooks can arrive
- **WHEN** `/etc/claude-code/managed-settings.json`, `managed-settings.d/` and `managed-mcp.json` are absent in the Linux launch filesystem but its credential mode can fetch unadmitted server-managed policy
- **THEN** the local scan and safe mode do not confer text-only eligibility
- **AND** before launch the selector may admit a separately proven contained profile or another eligible owner connection without requiring an API key

#### Scenario: Managed configuration is present or unreadable
- **WHEN** the text-only preflight finds any managed file, drop-in directory, policy bridge or managed MCP source present, unreadable, symlinked or uncheckable
- **THEN** it refuses that text-only launch before startup hooks can run
- **AND** it never certifies absence by silently ignoring the source

#### Scenario: Codex patch tools are contained
- **WHEN** a Codex reviewer with residual tools meets the complete review jail contract
- **THEN** its final valid verdict may satisfy required review as `contained`
- **AND** scratch patches are discarded, never applied, and the run records `contained` rather than text-only

#### Scenario: HTTP response mixes a verdict and a tool request
- **WHEN** an eligible HTTP review response contains a tool request alongside verdict text
- **THEN** the response does not complete review and no external write is sent
- **AND** unsupported request extensions are refused without removing source cost or privacy controls

### Requirement: Contained review cannot acquire external effect or persistent write authority
The system SHALL enforce a review-only profile through the existing provider jail with immutable read-only review input, minimal trusted runtime files, disposable empty workdir/home/config/temp, no writable host binds or owner workspace, no general network/egress proxy/engine relay, and no ambient credentials or sessions. A separately admitted inference-only transport SHALL retain real credentials outside tool reach and enforce the exact owner/model/data/budget scope, deny arbitrary endpoints and remote tools, and refuse authority expansion even when accessed by a tool. Every process and hook SHALL inherit isolation from startup. Seccomp profile, namespaces, limits, descriptors and teardown SHALL be proven for the pinned launch. The controller SHALL consume only bounded final verdict text and safe audit data, never generated artifacts or tool instructions. Missing proof, cleanup failure or boundary violation SHALL hold the write.

#### Scenario: Ordinary provider jail is mistaken for containment
- **WHEN** a launch still binds an owner workspace, checking egress socket, engine relay or persistent credential directory
- **THEN** it is ineligible as contained even if its native CLI is read-only or reports no tool calls

#### Scenario: A tool tries to use network or inference credentials
- **WHEN** a contained tool attempts DNS, direct or proxy HTTP, host loopback, a daemon socket, inherited descriptor, credential read or arbitrary request through the inference handle
- **THEN** the boundary prevents general communication, secret access and requests outside the admitted review
- **AND** no effect, new budget or platform write authority is created

#### Scenario: A nested patch sandbox requests broader access
- **WHEN** the pinned Codex helper uses a nested sandbox with the relaxed seccomp profile
- **THEN** it cannot widen the outer input/scratch mounts, network authority or credential access
- **AND** an unproven profile refuses launch rather than silently relaxing restrictions

#### Scenario: Review exits or is cancelled
- **WHEN** a contained review succeeds, errors, times out or is cancelled
- **THEN** the transport handle is revoked, all descendants including detached processes are terminated and reaped, and scratch is discarded
- **AND** only a successful verified teardown permits a valid verdict to authorize the separately gated effect

### Requirement: Reviewer family comes from actual work provenance
The system SHALL derive producing model families from server-owned receipts for the action's originating work and select a reviewer whose verified model family differs from every material producing family. Family SHALL represent model lineage rather than provider adapter, protocol, access method, alias label, or endpoint. Unknown producer or reviewer lineage SHALL hold the action. Deterministic work with no model producer SHALL require an explicit server-proven provenance state and still receive review.

#### Scenario: Claude work is reviewed by GPT
- **WHEN** Claude produced the proposed work and the command center has an authorized GPT model with proven text-only or contained execution
- **THEN** an eligible GPT reviewer is selected without changing the conversation's Claude model or saved preference

#### Scenario: GPT work is reviewed by Claude
- **WHEN** GPT produced the proposed work and an authorized Claude model has proven text-only or contained execution
- **THEN** Claude can review it through that eligible subscription or HTTP connection

#### Scenario: Subscription-only user builds and merges a patch with a custom tool
- **WHEN** an ordinary user has authorized different-family subscription connections with eligible launch profiles and their agent, task or graph proposes a merge through a custom connected-platform tool
- **THEN** a passing review and current destination consent permit the exact merge without requiring an API-key connection
- **AND** this works for GitHub, GitLab and other connected software through the same effect boundary

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
- **WHEN** the preferred review source has a transient capacity failure and another different-family source of either accepted tier is already authorized within the same limits
- **THEN** a remaining review attempt may select that source with fresh admission
- **AND** it records the actual reviewer without resetting the allowance or occupying the worker's different account seat

#### Scenario: Authority changes before dispatch
- **WHEN** the selected reviewer connection is revoked or its credential, family, or enforcement generation changes
- **THEN** dispatch refuses or re-admits current eligible authority before any provider call
- **AND** an old receipt is not used as a standing grant

### Requirement: Review receipts bind the exact effect and actual reviewer
The system SHALL bind review to the owner, command center, run, originating receipts, destination connection/incarnation, operation, method/path, complete payload or artifact digest, and current rule/consent generation. It SHALL durably record every review attempt's requested and actual provider/model/family, actual tier, version/configuration/mount/transport proof, teardown outcome, verdict or failure, usage, and timestamps in owner-visible run provenance. Unknown identity SHALL be explicit and SHALL NOT satisfy review. Submitted artifacts SHALL remain verbatim; materially inadequate review evidence SHALL hold the action.

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
The system SHALL hold an unavailable, malformed, timed-out, or ineligible review without sending the effect, distinguish that outcome from a completed `needs_approval` verdict, and expose the safe reason on the run and a deduplicated owner request. Remediation SHALL identify the exact missing family, authority or proof for either accepted tier. Owner-fixable grants SHALL use Connections; executor isolation failure SHALL NOT be presented as a requirement to buy API compute. It SHALL NOT recommend owner approval as a bypass, an unproven subscription login, or unconsented paid compute. Budget exhaustion SHALL remain a budget stop, not a new inference allowance.

#### Scenario: Only subscriptions with unproven enforcement are connected
- **WHEN** Claude-produced work has only an unproven Codex subscription reviewer available
- **THEN** the owner is told nothing was sent and which proof or authority is missing for GPT-family text-only or contained review
- **AND** guidance distinguishes executor repair from owner connection grants, with HTTP optional and the conversation model unchanged

#### Scenario: Owner approves an unavailable review
- **WHEN** the owner approves the planned action but no eligible review has completed
- **THEN** the write remains held with the connection/capability remedy

#### Scenario: Review asks for consent
- **WHEN** a valid, independently proven review returns `needs_approval`
- **THEN** the owner consent flow is bound to that exact action
- **AND** subsequent dispatch still checks current authority, unchanged content, and the completed review
