# Automation stop reason visibility

Local investigation and proposed additive read change, 2026-10-02. No production
access, state changes, push, or deployment are authorized for this lane.

## Design

The legacy consumer retirement already records its reason in
`assigned_queue_refusals`. Read that record without the transient refusal
freshness window, matching the control's universe and automation id and requiring
the observation to be at or after its latest state change. Expose it as
`stopped_because` for stopped controls, or `pause_reason` for paused controls.
If no matching evidence remains, explicitly say the reason was not recorded.
The generic legacy `detail` describes retirement of the layer, not proof of who
stopped an individual control. Keep current automations' existing `pause_reason`.

Include the legacy row's universe id so the owner app's existing scope check
retains it. Pick the reason and legacy disposition into the app bridge; do not
expose principal ids, definition JSON, or consumer ids. No storage schema or
state-transition changes are needed: the only current production caller that
stops these controls already records a reason.

## Acceptance

- An old retirement record remains visible through `read_graph target=automations`
  and the app's `list_automations` after transient refusal freshness expires.
- Missing, foreign-universe, and pre-transition records do not invent attribution.
- Current automation pause reasons remain visible. Reads do not change controls.
- Run only `tests/test_automation_stopped_reason.py`, changed-file Ruff, and rebuild
  the Claude plugin mirror. Commit locally; production verification is out of scope.

## Investigation

`tinyassets/storage/cloud_automation_control.py:173` owns the controls. Its schema
at line 75 is keyed by `(universe_id, automation_id)`, with `principal_id`,
`definition_json`, `definition_digest`, `cadence_seconds`, `revision`,
`desired_state`, `updated_at`, and `record_json`. The principal is the immutable
definition owner, not the actor who last stopped it. Neither the columns nor the
strict serialized model (`tinyassets/cloud_automation_control.py:161`) has a stop
reason or last-actor field.

All current control updates to stopped go through `set_desired_state`
(`tinyassets/storage/cloud_automation_control.py:326`). It performs a CAS update
of revision, desired state, updated time, and serialized record (line 369). If
an activation is active, it also increments its epoch, clears its execution
binding, and sets that activation to stopped (line 395). There is no transition
event insert. Creation and initial scheduling insert active controls; rebinding
sets them active. The stopped fallback at line 1150 is only a local scheduling
decision when a control is missing, not a database update.

The sole current production caller that stops cloud controls is
`AssignedQueueConsumer._retire_fleet_controls`
(`tinyassets/runtime/assigned_queue_consumer.py:1015`), invoked at startup at
line 215. It visits **all non-stopped controls, including paused ones**, stops
each, then records `RETIRED_FLEET_CONTROL_REASON` in `assigned_queue_refusals`
under `automation:<automation_id>` with `universe_id`, `observed_at`, and the
consumer's `consumer_id` (lines 1058-1067). The reason is:

> retired_fleet_era: this automation ran on the retired cloud-automation layer and has been stopped; recreate it with write_graph target=automation

Successful retirement emits `retired fleet-era cloud automation %s in %s`
(line 1070); exceptions emit `fleet control retirement failed universe=%s
automation=%s` (line 1075). Consumer identities are `worker_assigned_<boot uuid>`
(line 179). This is platform retirement, not an owner pause or failure threshold.
No separate migration, repair, or repeated-failure path writes stopped to this
table in the current tree. The unimplemented one-shot retirement migration in
`openspec/changes/archive/2026-09-26-retire-activation-layer/tasks.md:7` is a plan,
not an additional executable writer.

Current owner and agent controls reach the **new** `AutomationStore`, not the
legacy cloud store (`tinyassets/api/automations.py:608`). Owner/admin pause records
`owner_paused` (line 656); delete records `retired`
(`tinyassets/automations.py:1179`); failure handling records `repeated_failures`
(line 2569). They change `automations`, not `cloud_automation_controls`.
The general daemon pause writes `.pause` (`tinyassets/api/universe.py:5438`);
the consumer checks that sentinel (`tinyassets/runtime/assigned_queue_consumer.py:885`).
It does not change the cloud control's desired state.

Historically, the now-deleted `tinyassets/api/cloud_automations.py` could stop these
controls on behalf of the authenticated named owner. Inspect with
`git show 0715d3488^:tinyassets/api/cloud_automations.py`: actor authentication is
at line 50, control-owner matching at line 652, the pause/resume/stop mapping at
line 714, and the store write at line 740. That path passed no reason or actor
to the store. An owner client and an agent using that owner's authority were not
distinguished in the control record. Commit `0715d3488` deleted that module.

The audit evidence is limited: `assigned_queue_refusals` has the five columns
above (`tinyassets/storage/assigned_queue_refusals.py:12`), but upserts by task id
(line 46), so it is a latest-record ledger, not an append-only events table.
The stop commit and refusal write use separate transactions. A crash or failure
between them can leave a stopped control without its reason; subsequent startup
skips already-stopped controls. Historical owner stops also lack that reason.
`cloud_automation_terminal_receipts` records slice execution outcomes, not who
changed desired state (`tinyassets/storage/cloud_automation_control.py:61`).
A missing retirement record cannot prove an owner/agent caused the stop, and an
old record may have been overwritten. Retained logs can corroborate retirement.

These are the old repository-spec cloud continuation controls, not rows of
`tinyassets/automations.py`. Their recurring pump and claim pass are retired
(`tinyassets/runtime/assigned_queue_consumer.py:380`). The owner API marks them
legacy/read-only (`tinyassets/api/automations.py:348`). Stopped blocks ordinary
transitions (`tinyassets/cloud_automation_control.py:282`); a lower-level rebind
can historically reactivate a stopped control (line 299), but no current served
legacy action or recurring executor runs it. This change does not rebind anything.

Under `openspec/changes/universe-agent-harness/design.md:324` and `:378`, the
primary agent's persistent session, D2 child activities, and D3 idle research
are distinct from user-authored automation workflows. D3 explicitly does not
reuse a user-built wake loop. None of the five supplied legacy ids represents
the primary agent's own always-on harness loop. Their definitions can establish
what user/agent-authored workflow they were intended to run. The supplied live
fact proves neither a primary-agent pause nor the historical cause of each stop.

## Operator queries (not executed here)

Open `/data/.tinyassets.db` with a read-only SQLite connection, for example
`sqlite3 -readonly /data/.tinyassets.db`. First inspect availability if necessary:

```sql
SELECT name, sql FROM sqlite_master
WHERE name IN ('cloud_automation_controls', 'assigned_queue_refusals');
```

With both tables present, this returns the five specified controls and any
recorded disposition, without hiding older evidence behind a freshness cutoff:

```sql
PRAGMA query_only = ON;
SELECT c.automation_id, c.desired_state, c.revision,
       c.updated_at AS control_changed_at,
       c.principal_id AS definition_owner_not_stop_actor,
       json_extract(c.definition_json, '$.branch_def_id') AS workflow,
       r.reason, r.observed_at AS reason_recorded_at,
       r.consumer_id AS recording_consumer,
       CASE WHEN julianday(r.observed_at) >= julianday(c.updated_at)
            THEN 'at_or_after_control_change'
            ELSE 'missing_or_older_evidence' END AS evidence_timing
FROM cloud_automation_controls AS c
LEFT JOIN assigned_queue_refusals AS r
  ON r.universe_id = c.universe_id
 AND r.branch_task_id = 'automation:' || c.automation_id
WHERE c.universe_id = 'u-01kxm1vszd8hwp7em418asq8h9'
  AND (c.automation_id LIKE 'automation_repo_59d5%'
    OR c.automation_id LIKE 'automation_repo_7a09%'
    OR c.automation_id LIKE 'automation_repo_d5c0%'
    OR c.automation_id LIKE 'automation_repo_d717%'
    OR c.automation_id LIKE 'automation_repo_fe6b%')
ORDER BY c.automation_id;
```

If the ledger table is absent, read just the control columns; the missing table
itself means there is no retirement reason/consumer available there. A matching
`retired_fleet_era` reason at/after the change identifies platform retirement;
the principal column alone cannot identify the stopping actor.

On the host, search retained aggregated logs across container replacements
(`deploy/compose.yml:50` describes this journal selector):

```sh
journalctl CONTAINER_NAME=tinyassets-logs --no-pager -o cat |
  grep -E '(retired fleet-era cloud automation|fleet control retirement failed).*u-01kxm1vszd8hwp7em418asq8h9'
```

Log retention limits apply. There is no guaranteed SQL query that reconstructs
the actor and reason for an unaudited historical owner/agent stop.

## Local result

Added evidence-based legacy stop/pause reasons and universe ids to the list;
the app picks those reasons and preserves the legacy disposition. Source edits:
`tinyassets/api/automations.py`, `tinyassets/onboarding/app_ui.js`, plus their
generated Claude plugin mirrors. The existing user-owned-automations spec is
updated, with regression coverage in `tests/test_automation_stopped_reason.py`.
Only that test file ran: **9 passed**. Changed Python source/test/mirror Ruff is
clean. Plugin rebuild and import probe passed. No production access, push, or
live state change; no claim of deployment.
