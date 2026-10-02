# User-Owned Automations

## Purpose

Universe-owned, owner-controlled scheduled and event-triggered branch runs. An automation belongs to exactly one universe and to the authenticated principal who created it with an admin ACL on that universe. Each due run resolves the universe's current serving provider assignment and credential custody at the moment it fires and executes through the daemon's own assigned-queue consumer, under the same foreground admission and budget as a manual `run_graph` -- no host-run worker fleet, executor runtime identity, or preparation-time provider pin is a prerequisite. A registration that cannot fire is refused loudly with a named, owner-actionable reason, and one principal's authorization failure at run time is a single recorded refusal, never an abort of the pump for other universes.

## Requirements

### Requirement: An automation belongs to a universe and its owner
An automation SHALL be stored as a row bound to exactly one universe and to the authenticated
principal that created it, who MUST hold an admin ACL on that universe at creation; a request
without an authenticated principal or without that ACL SHALL be refused.

#### Scenario: Anonymous registration is refused
- **WHEN** `write_graph target=automation operation=create` arrives without an authenticated principal
- **THEN** the daemon refuses with `authentication_required` and stores nothing

#### Scenario: Owner sees and controls it
- **WHEN** the owner lists, pauses, resumes or deletes the automation from their surface
- **THEN** the change is applied to that row only and reflected on the next read

### Requirement: Execution derives authority from the current serving assignment
Each due run SHALL resolve the universe's current provider assignment and credential custody at
run time and launch through the served carrier under foreground admission and budget; no run
SHALL depend on an executor runtime identity, a provider recorded at preparation, or any
host-supplied enrollment manifest.

#### Scenario: Provider switched between runs
- **WHEN** the owner rebinds the universe to a different provider and a run comes due
- **THEN** the run launches on the new provider without any re-preparation

#### Scenario: Daemon restarted between runs
- **WHEN** the daemon restarts and the same run comes due
- **THEN** the run launches once; the `(automation_id, due_at)` fence prevents a second launch

### Requirement: A registration that cannot fire is refused loudly
The daemon SHALL refuse to store an automation that cannot run at that moment, with
a named reason, instead of accepting it silently. Registration SHALL refuse exactly what
foreground admission refuses: a row admission would reject is a row that fails every period
forever. Each reason SHALL reach the owner as a sentence they can act on, not a bare token.

#### Scenario: Consumer flag off
- **WHEN** the assigned-queue consumer is disabled and an owner registers an automation
- **THEN** the daemon returns `automation_unavailable` with reason `consumer_disabled`

#### Scenario: A workflow the owner did not author
- **WHEN** an owner registers a readable but foreign-authored branch, which foreground admission
  refuses because it requires `branch.author == principal`
- **THEN** the daemon returns `automation_unavailable` with reason `branch_not_owned`

#### Scenario: Serving on an open compute provider
- **WHEN** the universe's ready assignment names the owner's own open `api_key_http` provider
- **THEN** registration succeeds exactly as for a subscription source, and each due run launches
  on that provider under the same owner identity and usage rules

### Requirement: One principal's failure is one recorded refusal
When a due run cannot be authorized, the daemon SHALL record one refusal row for that automation
and continue with the others; it SHALL NOT abort the pump.

#### Scenario: Owner lost admin
- **WHEN** the owner's admin ACL on the universe was revoked before a run came due
- **THEN** a refusal is recorded, the automation is paused with that reason, and other universes' runs proceed

### Requirement: Stopped legacy controls explain their recorded disposition
The automation list SHALL expose a stopped legacy control's recorded reason as
`stopped_because`, without expiring it with transient refusal freshness. Paused
controls SHALL expose their recorded `pause_reason`. Missing evidence SHALL be
reported as an unknown reason, never inferred owner intent. The owner app SHALL
retain these universe-scoped legacy rows and their picked reasons.

#### Scenario: Retirement remains explainable after the freshness window
- **GIVEN** a stopped legacy control with a retirement refusal recorded after its state change
- **WHEN** the agent or owner app lists automations after the refusal freshness window
- **THEN** the recorded reason remains visible and the control's state is unchanged

#### Scenario: No matching stop evidence
- **GIVEN** a stopped legacy control with no refusal for that universe and automation at or after its state change
- **WHEN** automations are listed
- **THEN** the stop reason is explicitly unknown

### Requirement: Nothing executes outside a user's universe
The daemon SHALL run no host-owned worker, no platform actor, and no automation whose owner is
not a user principal with an admin ACL on its universe.

#### Scenario: Fleet services absent
- **WHEN** the deploy converges the compose project
- **THEN** no `worker*` service exists and no `tinyassets.cloud_worker` process runs

### Requirement: Schedules are retired; their rows keep a recorded disposition
The scheduler's schedule half SHALL NOT exist: `schedule_branch`, `unschedule_branch`,
`list_schedules`, `pause_schedule` and `unpause_schedule` SHALL be unknown `extensions` actions,
and no tick loop SHALL fire a `branch_schedules` row. Cadences are automations. Every existing
`branch_schedules` row SHALL be kept, marked inactive and paused, with the retirement reason as its
`pause_reason`, so a row is never dropped without a record. The event-subscription actions remain.

#### Scenario: An existing schedule row is kept with its reason
- **GIVEN** a `branch_schedules` row, active or not
- **WHEN** the scheduler schema migration runs
- **THEN** the row still exists, is inactive and paused, and its `pause_reason` names the retirement

#### Scenario: A retired schedule no longer holds its branch
- **GIVEN** a branch whose only schedule row was active before the retirement
- **WHEN** its owner deletes the branch
- **THEN** the retired row is not reported as a dependent

### Requirement: Served agents can reach the owner's automation lifecycle
The served graph tools SHALL expose automation list/get/create/pause/resume/delete
using the same owner-scoped adapter as the canonical connector, with bound actor,
pinned universe and existing authorization, revision and creation preflight.
No operation SHALL require exposing a raw credential or editing a workflow.

#### Scenario: Inspect an attached automation
- **WHEN** a bound served agent reads its universe's automations or selects one by ID
- **THEN** it receives the existing projected state and revision, without another universe's record

#### Scenario: Owner pauses or retires an automation
- **WHEN** the bound owner requests pause or delete with the current revision
- **THEN** the existing handler changes only that automation and readback reflects the result
- **AND** the receipt does not claim that an already-running job has stopped

#### Scenario: Retired automation no longer prevents branch deletion
- **WHEN** an owner retires an automation through the served route
- **THEN** the existing dependency query no longer lists that automation as a blocker
- **AND** unrelated branch dependencies remain authoritative

#### Scenario: No execution budget or ready provider is needed to stop a trigger
- **WHEN** the owner can authenticate and control the row but new execution is unavailable
- **THEN** pause/delete still reach their existing owner-scoped control handler

#### Scenario: Stale or foreign control request
- **WHEN** a control request has a stale revision or selects another universe's automation
- **THEN** the existing revision conflict or uniform not-found refusal is returned and no row changes

#### Scenario: Create or resume intentional recurring work
- **WHEN** the bound agent requests create or resume
- **THEN** existing ownership, creation preflight where applicable, store limits and runtime execution authority remain in force
- **AND** create uses fail-closed engine admission rather than bypassing execution safeguards

#### Scenario: Unsupported action or malformed payload
- **WHEN** an unsupported operation or malformed create payload is submitted
- **THEN** a structured refusal is returned without falling through to another action

#### Scenario: Documentation and dispatch agree
- **WHEN** the served tool description teaches an automation target and operation
- **THEN** that pair is accepted by the routing layer or has an explicit truthful refusal, never a hidden missing-tool path
