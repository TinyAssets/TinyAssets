# Engine events wake an owner's automation

## Why

Plan item 4 of the approved primitives plan (founder, 2026-09-27). A graph
cannot follow another graph, or wait for its owner's answer, without polling:

- `scheduler.py` admits four event types (`canon_change`,
  `branch_run_completed`, `canon_upload`, `pr_open`) that nothing emits. A
  subscription to one is stored and never fires. Its MCP action,
  `subscribe_branch`, also believed a caller-named `owner_actor`.
- Nothing fires when a run finishes, or when the owner answers a pending
  request from the app or from the connector's `answer_request`.

## What Changes

- **A new automation trigger kind, `event`.** `write_graph target=automation
  operation=create` takes an `event_type` and an optional `event_filter` in
  place of `interval_seconds`/`cron_expr`. The filter is equality over the
  event's own fields. The row is a subscription and is never due on a clock.
- **Two emitted events, each from the one place its state changes:**
  - `run_completed`, from `runs.update_run_status` on the transition into a
    terminal status, and from the boot sweep for runs a deploy killed
    (`outcome: interrupted`). Payload: `run_id`, `branch_def_id`, `outcome`.
    A subscription must filter on `branch_def_id`. An unfiltered pair of
    subscriptions would wake each other forever without anyone asking for a
    loop; a named loop stays expressible.
  - `pending_request_answered`, from `resolve_request`, which every answering
    surface goes through. Payload: `request_id`, `kind`, `status`
    (`answered` or `dismissed`).
- **A match stores a `once` wake**, with `inputs.event` holding the payload.
  The existing pump fires it with every run-time check an automation has.
- **Stamped with the causing principal.** An event wakes only subscriptions
  that principal owns, in that principal's home universe. A run's principal is
  recorded on the run when it is created (`runs.cause_principal`: the actor,
  or the principal bound for a `universe:<id>` run), not read from whatever
  identity is ambient when it ends. An event with no principal wakes nothing.
- **A cancelled run announces nothing.** Whoever cancelled it caused that end,
  and a collaborator's cancel must not start the owner's follow-up work.
- **The four un-emitted scheduler types are retired.** `subscribe_branch`
  refuses and points at automation events. `source:<id>` subscriptions are
  unchanged.
- `file_changed` is not emitted. Universe files are written from many paths,
  and a partial emitter would be a subscription that silently misses.

## Impact

- Code: `tinyassets/automation_events.py` (new), `tinyassets/automations.py`,
  `tinyassets/api/automations.py`, `tinyassets/runs.py`,
  `tinyassets/storage/pending_requests.py`, `tinyassets/scheduler.py`,
  `tinyassets/api/runtime_ops.py`.
- Storage: the `trigger_kind` CHECK widens to `event`, and the `event_type` and
  `event_filter_json` columns are added. `runs` gains `cause_principal`. All
  are applied on connect; a run created before this carries none and announces
  nothing.
- Public surface: new `create` payload fields, and new projection fields under
  `trigger`.
- Resolves concern `2026-09-02-non-source-event-subscriptions-never-fire.md`.

C2 folds `node-scheduled-wake`, `owner-message-wakes`, `served-inbound-webhook-triggers`, `automation-schedule-timezone` into this single active change.
