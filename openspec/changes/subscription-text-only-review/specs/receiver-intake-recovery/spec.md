## ADDED Requirements

### Requirement: Accepted intake survives downstream external-write failure
The system SHALL make every accepted receiver delivery visible from its durable `graph_deliveries` record before downstream execution. It SHALL persist independent operation outcomes in receiver-scoped `graph_delivery_operations` records in the runs database, retaining input digests, snapshot identity, attempt history, safe failure reasons, and linked run/review/effect receipts. Acceptance SHALL NOT be displayed as successful external filing.

#### Scenario: Worker never starts
- **WHEN** a patch request is accepted but no receiver worker can start
- **THEN** the receiving command center's inbox shows the pending request from the acceptance record
- **AND** no external issue or completed assessment is claimed

#### Scenario: Filing cannot obtain review
- **WHEN** the receiver's external filing is held by `auto_review_unavailable`
- **THEN** the request and filing operation remain visible with the exact safe remedy and retry eligibility
- **AND** logs are not the only failure surface

### Requirement: Assessment and notification are independent of filing success
The system SHALL support owner-authored receiver workflows in which assessment, notification, and external filing persist separate outcomes and filing failure does not suppress assessment or an internal receipt notification. It SHALL expose this composition through ordinary graph authoring and SHALL NOT silently rewrite existing owner workflows. The patch intake instance SHALL be considered repaired only after its owner-authored graph adopts this composition through the app.

#### Scenario: GitHub filing fails in the founder's intake
- **WHEN** the dogfood intake's filing operation fails before send while assessment capacity is available
- **THEN** assessment runs and the founder receives an internal notification identifying the request and filing failure
- **AND** this uses the same receiver behavior available to any user's connected destination

#### Scenario: Another user's project platform refuses filing
- **WHEN** a non-GitHub destination refuses a different owner's intake filing
- **THEN** assessment and internal notification still proceed under that receiver owner's authority

#### Scenario: Assessment or outbound notification also fails
- **WHEN** assessment has no eligible compute or an external notification is held by its own write review
- **THEN** their failures are recorded independently and the durable inbox/internal receipt notice remains available
- **AND** the outbound notification is not exempted from required review

### Requirement: Receiver owners and authorized maintainers can list pending intake
The system SHALL expose a paginated receiver-scoped inbox through `read_graph target="deliveries"`, with receiver and state filters and stable cursors. Existing `read_graph target="delivery" query=<delivery_id>` SHALL expose authorized receiver operation detail and recovery guidance. The receiver detail's "Received requests" view SHALL project these records, with "Patch requests" for the configured intake, and the existing pending-requests rail SHALL expose actionable holds. Access SHALL use the receiving command center's existing owner/admin authorization, not repository membership or a platform-wide maintainer privilege.

#### Scenario: Maintainer inspects pending requests
- **WHEN** an authorized admin opens the receiving command center's Patch requests view or lists deliveries
- **THEN** accepted, pending, held, failed, and unknown filing outcomes are discoverable with assessment and notification status
- **AND** opening an item identifies the safe next action without requiring log access

#### Scenario: Sender attempts receiver-wide reads
- **WHEN** a sender or unrelated repository maintainer lacks receiver command-center authorization
- **THEN** the inbox and private operation detail are refused
- **AND** the sender's existing individual receipt retains only its authorized coarse status without other requests, private assessment, or destination secrets

#### Scenario: Historical delivery lacks operation evidence
- **WHEN** an older delivery has no per-operation record
- **THEN** it is listed with `legacy_unknown` detail and its known attempt status
- **AND** neither filing success nor a safe automatic retry is inferred

### Requirement: Intake retries resume only known unfinished operations
The system SHALL support receiver-authorized `write_graph target="delivery" operation="retry_operation"` using a delivery ID, operation key, and expected operation version. It SHALL serialize claims, preserve completed operation results, and recheck current receiver, destination, cancellation, rule, consent, and review authority. Known pre-send failures SHALL be retryable without replaying successful work. Unknown post-send outcomes SHALL require destination idempotency or read-only reconciliation before resending; without either, they SHALL remain held for owner reconciliation.

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
