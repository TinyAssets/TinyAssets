## ADDED Requirements

### Requirement: Activity records live in platform storage outside the universe
The platform SHALL keep every activity record, effect intent and status event in `.agent-sessions/<universe>/agent-activities.db` under the data root, outside every universe folder, where no tool jail, provider jail or workflow can read or write it. Activity ids SHALL be minted by the platform. A record SHALL name its owner principal, derived server-side from the authenticated creator and never from tool arguments or templates, its agent, its session key `activity:<id>`, its title, brief, origin, status, outcome, result summary, last completed tool call, and its current run and runner generation. The store's bytes SHALL be charged to the owning universe's quota. Each activity SHALL keep at most 200 status events. Before an account's `.agent-sessions/<home>` is removed, every activity in it SHALL be fenced and failed so no live runner can recreate it.

#### Scenario: the agent cannot forge its own activity state
- **WHEN** the agent writes to any path from bash, a workflow or a provider jail
- **THEN** no activity record, effect intent or event changes

#### Scenario: account deletion removes activities
- **WHEN** the owner deletes their account while an activity is running
- **THEN** the activity is fenced and failed and the universe's `agent-activities.db` is removed with the rest of `.agent-sessions/<home>`

### Requirement: An activity runs as runs of the universe's Activities branch
An activity SHALL execute as runs of one owner-authored agent-node branch per universe, part of the harness layer: seeded on first use, visible and editable by the owner, re-seeded if missing, and refused with `activity_branch_invalid` if edited to have no agent node. Its runs SHALL be admitted, budgeted, seated and recovered exactly as foreground runs are, with no change to provider authority. A run SHALL count as an activity's run only when the activity record names that run; run inputs SHALL NOT establish the link. Each run SHALL bind the owner with the same requestless identity and per-node admin re-check that automations use; if the owner no longer owns or administers the universe, the activity SHALL fail with `owner_lost`; with no compute connected it SHALL wait on the owner with that reason. Only one adapter SHALL depend on this substrate.

#### Scenario: a forged activity id is an ordinary run
- **WHEN** the agent runs the Activities branch itself with another activity's id in its inputs
- **THEN** that run does not continue the activity's session, report its progress or write its effect intents

#### Scenario: the owner customises background work
- **WHEN** the owner edits the Activities branch's agent node instructions
- **THEN** the next activity run uses the edited branch

### Requirement: Activities are dispatched durably with one live run each
A dispatcher SHALL run on the automation pump's cadence and whenever an activity is created or answered, and SHALL select queued activities and in-progress activities whose run has ended without settling it or was interrupted; a live run, or one whose owner is alive or unknown, SHALL NOT be replaced. Claiming SHALL advance the runner generation by compare-and-set, reserve a run without executing it, bind that run to the record under the generation, and only then release it; a run SHALL execute only if, at its start, the record names it under a current generation. Every runner write SHALL carry its generation so a superseded run's writes change nothing. An activity over the seat count SHALL wait visibly and never be refused. Waiting on the owner SHALL be a yield: once the run's effects have settled, the agent's request SHALL be bound to the exact pending action, and the run SHALL end as completed (never as interrupted), releasing its seat; an owner answer SHALL re-queue the activity only if it answers the request the activity is waiting on.

#### Scenario: an approval does not hold a seat
- **WHEN** an activity waits on an owner approval
- **THEN** its run has ended, its seat is free, and a queued activity takes it

#### Scenario: a stalled run is not replaced
- **WHEN** an activity's run is alive but slow
- **THEN** no second run is started for it

### Requirement: Schedules start activities through ordinary automations
An automation targeting the Activities branch with a title and brief SHALL start one activity per firing. The record SHALL be created only from a platform-owned firing context (the claimed attempt's owner, automation id and due time), never from run inputs, idempotent on the automation id and due time, so re-firing the same attempt after a crash returns the same activity. A run of the Activities branch that no activity record names SHALL be refused before it executes. Automations SHALL need no new target kind.

#### Scenario: a weekly report
- **WHEN** an automation targets the Activities branch for Mondays at 09:00
- **THEN** each Monday exactly one activity starts and appears under In progress, then Completed

### Requirement: An activity's external effects are recorded before they fire
For an activity's run, and any run it starts, the effector SHALL record an effect intent keyed by the run, node, effect index and a digest of the resolved request (method, URL and transformed body) as planned, SHALL durably commit it as sent before the request leaves, and SHALL send nothing if either write fails or the intent already exists. After the request it SHALL record confirmed or failed with a safe receipt; transport uncertainty after the request was written SHALL be recorded as unknown, never failed. When such a run is interrupted, planned intents SHALL become failed and sent intents unknown; the activity SHALL NOT continue until each unknown intent is resolved, and an unresolvable one SHALL become an owner question answered as happened, not happened or try again. Only try again SHALL permit a new attempt.

#### Scenario: a deploy during a send
- **WHEN** the daemon restarts after an activity's effect was committed as sent but before its outcome was recorded
- **THEN** the activity asks the owner whether it happened and does not send it again unless told to try again

### Requirement: Activities resume after a restart from their last completed tool call
A runner taking over an in-progress activity SHALL continue its session natively when the adapter can resume, or otherwise from a session built from the record: the brief, the completed tool calls from the durable turn journal or their safe summaries, the partial result and the effect resolutions. It SHALL be told it was interrupted and SHALL NOT re-execute a completed tool call.

#### Scenario: two activities and a deploy
- **WHEN** two activities are in progress and the daemon restarts
- **THEN** both continue without the owner reconnecting, and neither repeats a completed tool call

### Requirement: The agent and the owner manage activities through one contract
The universe agent's served tools SHALL offer `write_graph target=activity` with operations `start`, `stop`, `pause` and `resume`, and `read_graph target=activities` and `target=activity`, scoped to the agent's own universe. Starting an activity from inside an activity SHALL be refused with `nested_activity_unavailable`. The owner's app SHALL offer list, stop, pause, resume and delete for the authenticated owner's own home only, with compare-and-set on the record's revision. Listing, and an activity's events and effect intents, SHALL be returned completely, paged by an explicit cursor and never cut at a size cap. Stopping SHALL keep the partial result. Status changes SHALL reach the agent's main session as platform-composed lines whose only agent-supplied text is the activity's quoted, one-line, length-bounded title.

#### Scenario: a long list is complete
- **WHEN** the agent lists its activities and there are more than one page
- **THEN** each page carries a cursor to the next until every activity has been returned

#### Scenario: stop keeps the work
- **WHEN** the owner stops a running activity
- **THEN** it stops at its next tool boundary and its result summary so far remains on the record
