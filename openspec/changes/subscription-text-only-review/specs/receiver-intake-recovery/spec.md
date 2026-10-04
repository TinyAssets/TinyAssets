## ADDED Requirements

### Requirement: Accepted intake survives downstream external-write failure
The system SHALL make every accepted receiver delivery visible from its durable `graph_deliveries` record before downstream execution. It SHALL persist independent operation outcomes in receiver-scoped `graph_delivery_operations` records in the runs database, retaining input digests, snapshot identity, attempt history, safe failure reasons, and linked run/review/effect receipts. Acceptance SHALL NOT be displayed as successful external filing.

#### Scenario: Worker never starts
- **WHEN** a delivery is accepted but no receiver worker can start
- **THEN** the receiving command center's inbox shows the pending request from the acceptance record
- **AND** no external issue or completed assessment is claimed

#### Scenario: Filing cannot obtain review
- **WHEN** the receiver's external filing is held by `auto_review_unavailable`
- **THEN** the request and filing operation remain visible with the exact safe remedy and retry eligibility
- **AND** logs are not the only failure surface

### Requirement: One connection-write failure preserves later independent receiver steps
The system SHALL support ordinary owner-authored receiver workflows in which every connection write has a durable outcome and its failure does not terminate later independent steps. Success-dependent steps SHALL retain visible pending dependencies; assessment, failure handling and internal receipt notification SHALL proceed independently where their inputs are available. It SHALL expose this composition through ordinary graph authoring and SHALL NOT silently rewrite existing owner workflows. Existing receivers SHALL retain visible accepted/failed state even before adoption; a workflow repair SHALL require its owner to adopt the composition through ordinary app graph authoring. The founder SHALL use the same mechanism as any receiver owner.

#### Scenario: An ordinary owner's connection write fails
- **WHEN** a receiver's custom tool write fails before send while later assessment and notification have their required inputs
- **THEN** assessment and notification run, and the receiving owner sees the failed write and can retry it
- **AND** a step requiring the missing external reference stays visibly pending instead of being lost

#### Scenario: Founder receives a patch request
- **WHEN** another user sends a patch request to the founder's receiver and its GitHub filing fails
- **THEN** the same durable outcomes, later-step behavior and owner inbox apply without platform-specific routing or credentials

#### Scenario: Another user's project platform refuses filing
- **WHEN** a non-GitHub destination refuses a different owner's intake filing
- **THEN** assessment and internal notification still proceed under that receiver owner's authority

#### Scenario: Assessment or outbound notification also fails
- **WHEN** assessment has no eligible compute or an external notification is held by its own write review
- **THEN** their failures are recorded independently and the durable inbox/internal receipt notice remains available
- **AND** the outbound notification is not exempted from required review

### Requirement: Every receiver owner can list pending and failed deliveries
The system SHALL expose a paginated owner-scoped inbox through `read_graph target="deliveries"`, with authorized receiver and state filters and stable cursors. Existing `read_graph target="delivery" query=<delivery_id>` SHALL expose authorized receiver operation detail and recovery guidance. The receiver detail's "Received requests" view SHALL project these records, with optional owner-chosen labels such as "Patch requests", and the existing pending-requests rail SHALL expose actionable holds. Every receiver owner SHALL have access to their own deliveries without maintainer status. Existing explicit receiver administration grants may delegate access; repository membership SHALL confer none.

#### Scenario: Ordinary receiver owner inspects pending requests
- **WHEN** the receiver owner opens Received requests or lists deliveries
- **THEN** accepted, pending, held, failed, and unknown filing outcomes are discoverable with assessment and notification status
- **AND** opening an item identifies the safe next action without requiring log access

#### Scenario: Sender attempts receiver-wide reads
- **WHEN** a sender or another owner lacks receiver command-center authorization
- **THEN** the inbox and private operation detail are refused
- **AND** the sender's existing individual receipt retains only its authorized coarse status without other requests, private assessment, or destination secrets

#### Scenario: Historical delivery lacks operation evidence
- **WHEN** an older delivery has no per-operation record
- **THEN** it is listed with `legacy_unknown` detail and its known attempt status
- **AND** neither filing success nor a safe automatic retry is inferred

### Requirement: Intake retries resume only known unfinished operations
The system SHALL support receiver-owner-authorized (or explicitly delegated) `write_graph target="delivery" operation="retry_operation"` using a delivery ID, operation key, and expected operation version. It SHALL serialize claims, preserve completed operation results, and recheck current receiver, destination, cancellation, rule, consent, and review authority. Known pre-send failures SHALL be retryable without replaying successful work. Unknown post-send outcomes SHALL require destination idempotency or read-only reconciliation before resending; without either, they SHALL remain held for owner reconciliation.

#### Scenario: Reviewer connection is repaired
- **WHEN** the owner resolves a filing review hold and retries that operation
- **THEN** filing receives fresh admission/review and assessment and completed notification are not rerun

#### Scenario: Filing may have succeeded before a timeout
- **WHEN** the process loses the external response after dispatch
- **THEN** the operation is `outcome-unknown`, no automatic send occurs, and recovery reconciles or uses a proven idempotency key
- **AND** no exactly-once guarantee is inferred from a local retry counter

#### Scenario: Concurrent retry attempts
- **WHEN** two authorized clients retry the same operation version
- **THEN** at most one execution claim is admitted and the other sees the current operation state

#### Scenario: Receiver authority is revoked
- **WHEN** receiver or destination authority is revoked before retry
- **THEN** retry is refused without borrowing sender credentials or replaying the receiver branch
