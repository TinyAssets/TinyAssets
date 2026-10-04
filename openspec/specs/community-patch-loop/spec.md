# Community Patch Loop

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

The live self-improvement pipeline: wiki bug filings enqueue canonical investigation runs whose Patch Packets land back on the wiki page; the auto-ship validator is a pure dry-run envelope and PR creation stays feature-flagged off.

## Requirements

### Requirement: Bug Filing Enqueues A Canonical Investigation Request

When a chatbot files a bug through the wiki `file_bug` action, the system SHALL write the bug page immediately and then attempt to enqueue a canonical bug-investigation request. The auto-trigger SHALL be enabled only when an investigation handler resolves — either the Goal named by `TINYASSETS_BUG_INVESTIGATION_GOAL_ID` exposes a strictly-public canonical branch version, or `TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID` supplies a fallback branch definition. When enabled, the enqueue SHALL append exactly one owner-queued BranchTask carrying `request_type` `bug_investigation` and the mapped bug payload to the universe's branch-task queue, and SHALL NOT start a run. When no handler resolves, or the enqueue is rejected or errors, filing SHALL fall back to wiki-write-only and the bug filing itself SHALL still succeed. This behavior lives in `tinyassets.bug_investigation` and the `file_bug` handler in `tinyassets.api.wiki`.

#### Scenario: Fallback branch definition enables the enqueue

- **WHEN** `_maybe_enqueue_investigation` runs for a filed bug while `TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID` names a branch definition and no Goal canonical is set
- **THEN** exactly one BranchTask is appended to the queue with `request_type` `bug_investigation`, `trigger_source` `owner_queued`, the resolved fallback `branch_def_id`, and inputs carrying the bug id, title, and severity
- **AND** no run is started by the enqueue

#### Scenario: No configured handler still files the bug

- **WHEN** a bug is filed while neither a Goal canonical nor `TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID` resolves a handler
- **THEN** no investigation task is queued
- **AND** the `file_bug` call still returns a filed bug page

#### Scenario: Trigger failure never breaks the filing

- **WHEN** the enqueue path raises a dispatcher-rejection `RuntimeError` or a bad-input `ValueError`
- **THEN** `_maybe_enqueue_investigation` recovers and returns `None`
- **AND** the `file_bug` response still reports the bug as filed

### Requirement: Investigation Runs Attach A Patch Packet To The Wiki Bug Page

A daemon dispatcher SHALL claim a queued `bug_investigation` task and run the canonical investigation branch. On successful completion the system SHALL extract the investigation's patch packet from the run output and append — or replace, if one already exists — a single `## Patch Packet` section on the source bug's wiki page, preserving the original bug description. The loop SHALL produce wiki artifacts only and SHALL NOT create repository commits, branches, or pull requests. A run that did not complete, a run whose output carries no patch packet, or a bug page that cannot be located SHALL be skipped without mutating any page. Attachment lives in `attach_patch_packet_comment` (`tinyassets.bug_investigation`) invoked from the dispatcher in `fantasy_daemon`.

#### Scenario: First completion appends the Patch Packet

- **WHEN** a `bug_investigation` run for a bug completes with a non-empty patch packet
- **THEN** a `## Patch Packet` section is appended to that bug's wiki page
- **AND** the original bug description text is still present on the page

#### Scenario: Re-run replaces rather than duplicates

- **WHEN** a second completed investigation attaches a patch packet for the same bug id
- **THEN** the bug page contains exactly one `## Patch Packet` section

#### Scenario: Non-completed run or empty packet is skipped

- **WHEN** the run status is not `completed`, or the run output contains no patch packet
- **THEN** no page write occurs and the attach result is a skip with a reason

### Requirement: Auto-Ship Validation Is A Pure Dry-Run Safety Envelope

The auto-ship validator (`validate_ship_request` in `tinyassets.auto_ship`) SHALL be pure — performing no filesystem, network, or repository writes — and SHALL always return `ship_status` `"skipped"` with `dry_run` true; whether a packet passed the envelope SHALL be carried by `would_open_pr`. A packet SHALL be treated as passing only when it satisfies every blocking gate: `release_gate_result` equals `APPROVE_AUTO_SHIP`, `child_keep_reject_decision` equals `KEEP`, `child_score` is at least 9.0, `risk_level` equals `low`, `ship_class` is one of `docs_canary`, `metadata_canary`, or `test_fixture_canary`, the coding-packet status is `KEEP_READY` or `AUTO_SHIP_READY`, every changed path is under an allowed canary prefix while matching no forbidden prefix or env/secret/auth substring, and the diff is within the byte cap with no null bytes and no heuristic secret matches. The envelope SHALL have no warning tier that lets a failing packet pass; the rubric and trajectory checks SHALL default to warn-only channels that annotate the decision without changing the pass/block verdict.

#### Scenario: Compliant canary packet would open a PR

- **WHEN** a packet satisfies every envelope gate
- **THEN** the decision reports `would_open_pr` true, `validation_result` `passed`, `ship_status` `skipped`, and a `revert:`-prefixed rollback handle

#### Scenario: Any unmet gate blocks the packet

- **WHEN** a packet fails a gate — for example `child_score` below 9.0, a forbidden or non-allowlisted changed path, an oversized diff, or a `release_gate_result` other than `APPROVE_AUTO_SHIP`
- **THEN** the decision reports `would_open_pr` false with at least one blocking violation
- **AND** `ship_status` is still `skipped`

#### Scenario: Warn-mode checks never flip the verdict

- **WHEN** the rubric or trajectory eval mode is `warn`
- **THEN** `would_open_pr` matches the verdict produced with the mode `off`
- **AND** any warnings are recorded on their own annotation channel without adding a blocking violation

### Requirement: Auto-Ship Validates And Records But Opens No Pull Request

The auto-ship lane SHALL validate a ship packet and record the attempt; it SHALL NOT open, push to, or merge a pull request on any forge, and there SHALL be no GitHub-specific pull-request action (the former `open_auto_ship_pr`, removed 2026-09-24). A universe that wants a pull request opens it the way it calls any API: a workflow node on the generic `authenticated_external_call` sink, through a connection its owner deposited, with the owner's own credential.

#### Scenario: There is no platform pull-request opener

- **WHEN** a caller asks the extensions surface for `open_auto_ship_pr`
- **THEN** the action is not in the auto-ship dispatch table and no forge request is made

### Requirement: Auto-Ship Attempts Are Recorded In An Append-Only Ledger

Every validated auto-ship attempt SHALL be recorded as one row in a per-universe append-only JSONL ledger (`auto_ship_attempts.jsonl`), file-locked so concurrent writes serialize. A passed validation SHALL record `ship_status` `"skipped"` with `would_open_pr` true; a blocked validation SHALL record `ship_status` `"blocked"` with the recorded violations. `ship_attempt_id` and `created_at` SHALL be immutable, only whitelisted fields SHALL be mutable through `update_attempt`, and `ship_status` SHALL be constrained to the valid lifecycle set (`skipped`, `blocked`, `opened`, `merged`, `failed`, `rolled_back`). A duplicate `ship_attempt_id` or an out-of-set `ship_status` SHALL be rejected rather than silently accepted. The ledger lives in `tinyassets.auto_ship_ledger`.

#### Scenario: Passed and blocked attempts record distinct rows

- **WHEN** a passed decision and a blocked decision are each recorded via `attempt_from_decision` plus `record_attempt`
- **THEN** the ledger holds a `skipped` row with `would_open_pr` true and a separate `blocked` row carrying the violations

#### Scenario: Duplicate attempt id is rejected

- **WHEN** `record_attempt` is called twice with the same `ship_attempt_id`
- **THEN** the second call raises a `ValueError` instead of appending a duplicate row

### Requirement: Loop Health Is Watched By Read-Only Monitors

The community patch loop SHALL be observed by read-only monitors that never mutate repository or loop state. `scripts/community_loop_watch.py` SHALL query public GitHub state and report per-stage health — observation-canary freshness, open P0-outage issues, tier-3 clone smoke, production deploy, and website deploy — exiting non-zero only when a retained uptime or deploy stage is red. The revert-loop canary SHALL classify the recent rate of REVERT commits into OK, WARN, and CRITICAL bands. As-built limitation: the historical cheat-loop intake/writer/checker machinery has been removed from the codebase (retired 2026-06-25), so "auto-fix disabled" denotes the absence of that machinery rather than a runtime toggle — there is no `AUTO_FIX_DISABLED` code gate.

#### Scenario: A red retained stage fails the watch

- **WHEN** `community_loop_watch` observes a retained uptime or deploy stage that is red
- **THEN** the overall status is `red` and the process exit code is non-zero

#### Scenario: Revert-rate classification bands

- **WHEN** the revert-loop canary evaluates an activity-log tail of REVERT commits
- **THEN** it returns OK below the warn thresholds and escalates to WARN or CRITICAL as the revert count-and-window thresholds are crossed

#### Scenario: Stale observation warns before paging

- **WHEN** the latest valid observation-canary run is over 90 minutes old with default thresholds
- **THEN** its monitoring-cadence status is yellow until its age exceeds 360 minutes, when it becomes red
- **AND** a fresh measured endpoint failure still reports red
- **AND** the separate workflow alarm sink dispatches the allowlisted stale uptime canary even when the overall status is yellow, without opening, closing, or commenting on alarm issues
- **AND** a self-heal follow-up does not dispatch another follow-up

#### Scenario: A deploy conclusion has no failure signal

- **WHEN** the latest completed deploy run is skipped, cancelled, neutral, or stale
- **THEN** the deploy stage warns yellow rather than reporting a measured failure
- **AND** failure, timed_out, action_required, and unrecognized conclusions still report red

#### Scenario: Unknown observation preserves incident state

- **WHEN** the overall monitoring status is unknown
- **THEN** the alarm sink performs no workflow dispatch or incident mutation

### Requirement: Auto-ship PR creation is scoped, idempotent, and stale-head safe
PR creation SHALL operate only on a recorded eligible auto-ship attempt and a validated same-repository `auto-change/*` head. An explicit token SHALL take precedence; a production call with universe and destination SHALL resolve a per-universe `vcs/github/write` credential; environment token fallback SHALL apply only when no scoped destination lookup is requested. An attempt already recorded as opened SHALL return its recorded PR evidence without another network write, and a head that is behind or diverged from base SHALL fail with `pr_create_stale_head` before PR creation.

#### Scenario: Scoped vault credential wins
- **WHEN** an eligible attempt requests PR creation with a universe, destination, both a vault credential and an environment token
- **THEN** the GitHub requests use the per-universe destination-scoped write credential and do not expose either token in result evidence

#### Scenario: Recorded open attempt is reused
- **WHEN** an attempt already has `ship_status=opened` and a PR URL
- **THEN** PR creation returns the recorded URL and commit evidence with `already_open=true` and performs no additional POST

#### Scenario: Stale head is refused before creation
- **WHEN** the GitHub comparison reports the auto-change head behind or diverged from its base
- **THEN** the attempt is marked failed with `pr_create_stale_head` and no PR-create POST occurs

### Requirement: Auto-ship health is a read-only status projection
`get_status.auto_ship_health` SHALL summarize the append-only attempt ledger without polling, rolling back, or mutating it. The projection SHALL include bounded recent attempts, opened-PR observation state, rollback recommendations for regressed opened or merged attempts, the observation window, ledger availability, and warnings; a ledger read failure SHALL return `ledger_available=false` with a warning rather than fail the status request.

#### Scenario: Recent and regressed attempts are summarized
- **WHEN** the ledger contains recent attempts plus an opened attempt whose observation status regressed
- **THEN** health lists bounded recent attempts and opened PRs and includes a rollback recommendation for the regressed attempt

#### Scenario: Ledger read failure stays observable
- **WHEN** the attempt ledger cannot be read
- **THEN** status still returns an `auto_ship_health` object with `ledger_available=false` and a `ledger_read_failed` warning

### Requirement: Branch-task restarts reuse completed runs and recover nested Patch Packets
The daemon branch-task executor SHALL derive the stable run name `branch-task-<branch_task_id>` and reuse a matching completed durable run rather than execute it again. It SHALL return metadata containing the reused run id, status, actor, branch definition, and reuse flag; the stored output is consumed internally rather than returned. For a completed bug-investigation task it SHALL find the first non-empty packet in `patch_packet`, `candidate_patch_packet`, or `child_candidate_patch_packet`, recursively inspect `attached_child_output`, or derive implementation/test fields from a `coding_packet`; non-empty strings SHALL become an implementation sketch. It SHALL attach the recovered packet to the source wiki page, while non-completed runs, non-investigation tasks, missing bug ids, and empty packets SHALL not write a false Patch Packet.

#### Scenario: Completed durable run is reused after restart
- **WHEN** a claimed branch task has a matching completed run under its stable run name
- **THEN** the executor returns reused-run metadata with that run id and `reused_existing_run=true`, uses stored output only for packet recovery, and does not invoke branch execution again

#### Scenario: Nested child packet is attached
- **WHEN** a completed bug-investigation output contains `attached_child_output.candidate_patch_packet`
- **THEN** the packet is normalized and attached to the referenced bug page under `## Patch Packet`

#### Scenario: String packet becomes an implementation sketch
- **WHEN** a recognized candidate packet field contains a non-empty string
- **THEN** the attached packet renders that string as the implementation sketch

#### Scenario: Failed investigation does not publish a packet
- **WHEN** a bug-investigation run is not completed
- **THEN** packet attachment returns a skipped reason and leaves the wiki page unchanged
