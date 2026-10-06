## ADDED Requirements

### Requirement: Platform Liveness Follows The Current Executor Lifecycle

The platform SHALL project daemon-owned coordinator liveness from actual
started instances in the serving process, their thread lifecycle and elapsed
time since their last successful poll. Historical shared or named per-universe
heartbeat files SHALL NOT establish the expected platform executor inventory.
The public projection SHALL expose only present, alive, beat_age_s, phase and
consec_crashes; it SHALL reveal no tenant, worker, task, runtime identity or count.
An enabled missing executor SHALL report unhealthy. A dead, stopping or stalled
expected coordinator SHALL NOT be concealed by a healthy peer or fresh activity.
A stopped instance SHALL retire only after its thread exits. The stall bound
SHALL be the greater of 300 seconds and the poll interval plus 120 seconds.

Per-universe heartbeat publication, queue-descriptor classification and the
existing shell-watchdog heartbeat contract SHALL remain unchanged. An engine
MCP child that cannot observe its parent coordinator SHALL report unknown
liveness with phase external_coordinator, not infer that the parent is stopped.
The activity canary SHALL fail on unknown expected-coordinator liveness even
when its last-activity timestamp is recent. Coordinator liveness SHALL NOT be
presented as proof that a particular user's work completed.

#### Scenario: Retired artifacts cannot override current execution
- **WHEN** an expected daemon coordinator is alive and making successful polls while old shared or named heartbeat files remain
- **THEN** platform status reports the current coordinator without treating those files as expected running processes

#### Scenario: A dead current coordinator remains visible
- **WHEN** the enabled current coordinator is missing, dead, stopping or beyond its poll-progress bound
- **THEN** status reports unhealthy and fresh activity or another healthy coordinator does not hide it

#### Scenario: A child process cannot attest its parent
- **WHEN** get_status executes in a per-universe engine MCP child
- **THEN** coordinator liveness is explicitly unknown with no private identity disclosed
