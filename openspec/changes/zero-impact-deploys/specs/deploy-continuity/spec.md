## ADDED Requirements

### Requirement: Independent idle-instant owner handover
Compatible deploys SHALL consume target-architecture D11/S8a ownership and S4 idle fencing: only idle command-center keys move, busy keys retain admission and their old owner until completion, and unrelated keys proceed independently. Successors SHALL complete the acknowledged barrier before reconciliation or service. No live checkpoint transfer or global admission hold is permitted.

#### Scenario: Native straggler spans a release
- **WHEN** one center has a long native turn and another is idle
- **THEN** the idle center moves, the busy center keeps serving on its old owner, and that straggler delays only old retirement

### Requirement: Summed resource and provider admission budget
Overlap SHALL obey design D1's summed cgroup inequality against measured MemTotal, including gateway, tunnel, OS and safety reserves and all provider descendants. Both generations SHALL share one provider_admission limit. Protected gateway/tunnel OOM priorities SHALL be verified. A candidate that cannot fit SHALL remain unstarted without closing old admission.

#### Scenario: Old children occupy admission capacity
- **WHEN** the new generation requests provider capacity while old children run
- **THEN** old tokens remain counted and the shared cap and aggregate memory budget cannot be exceeded or reset

### Requirement: Transactional and downstream effect fencing
The owner SHALL check its command-center generation in the same BEGIN IMMEDIATE transaction that marks an intent sent before remote dispatch. Broker and boxhostd SHALL enforce the acknowledged D11/D5 barrier and reject stale generations. Sent or unknown effects SHALL NOT blindly replay.

#### Scenario: Old dispatch resumes after fencing
- **WHEN** an old executor pauses after sent commit and resumes after the successor's acknowledged barrier
- **THEN** its stale external dispatch is rejected and no duplicate effect is issued

### Requirement: Existing send identity and private page recovery
Ingress and receipt reconciliation SHALL retain merged #4490 client_send_id and SHALL offer no resend while delivery is uncertain. Page recovery SHALL extend merged #4497 command-center-recovery: remount then bounded reload with a two-page ceiling and verified owner/home/addressed-agent draft restoration.

#### Scenario: Lost acknowledgement during page replacement
- **WHEN** delivery is uncertain and recovery replaces the page
- **THEN** the original client_send_id retrieves its receipt without another turn or resend offer, and drafts restore only for the verified original identity
