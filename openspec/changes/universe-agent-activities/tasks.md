## D2a: records, substrate, dispatch
- [ ] 1.1 `agent_activities.py`: the store (schema, checked transitions, generation fencing, keyset reads, bounded events), charged to the universe's quota.
- [ ] 1.2 The Activities branch (harness layer): seed on first use, re-seed when missing, refuse when invalid; the `ActivityRunner` adapter over runs, record-named linkage only.
- [ ] 1.3 The durable dispatcher (pump cadence plus on demand): claim, start the run, stamp it; replace only ended or interrupted runs.
- [ ] 1.4 The activity run's agent node: `activity:<id>` session, progress, pause/stop at the next tool boundary, waiting ends the run; answered-request re-queue.
- [ ] 1.5 Served tools: `write_graph target=activity` and `read_graph target=activities|activity`; nested start refused; status lines into the main session.

## D2b: effects, resume, schedules
- [ ] 2.1 Effect intents for activity runs and their child runs (lineage from the parent run): committed before the wire, unknown on transport uncertainty; recovery after interruption and the owner question.
- [ ] 2.2 Resume (native or record-built from the turn journal); account deletion fences activities first.
- [ ] 2.3 A run of the Activities branch with no activity named creates its record idempotently by `<automation_id>@<due_at>`.

## D2d: the Activity tab
- [ ] 4.1 `/app/activities` owner door (own home, revision compare-and-set, delete).
- [ ] 4.2 Activity tab: Waiting on you, In progress, Scheduled (complete, paged), Completed with receipts; stop, pause, resume.
- [ ] 4.3 Live proof: two activities in parallel after the chat is closed; one surviving a deploy with an effect in flight; one from a Monday automation.
- [ ] 4.4 Sync these deltas into `openspec/specs/` and archive.
