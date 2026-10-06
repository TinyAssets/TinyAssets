# Your starter package

Everything here is yours to edit, remove or replace. These are files and recipes;
files alone do not install a UI or start an automation. Your agent uses the
existing governed operations and verifies their receipts.

| File | Purpose |
| --- | --- |
| settings.json | Proactivity: off, low (default), high |
| goals.json | Goals and their evidence, status and next step |
| feed.md | Interests, priorities and sourced updates |
| ideas.json | Editable prompt examples; selecting one only prefills chat |
| monitors.json | Explicitly requested watches and reminders |
| command-center.json | Replaceable app_ui component |
| workflows.json | Branch creation payloads and automation templates |
| checkins.py | Meaningful-change check and notify through ta |

Ask your agent to set up the default layout and daily goals review. It first
checks existing UI/schedules and reads the matching starter skills. For each
workflow it creates the `branch` via `write_graph target=branch operation=create`,
then adds the returned `branch_def_id` to `automation` and creates it via
`write_graph target=automation operation=create`. It records receipts and IDs
in installations.json. A repeated setup reuses those IDs after verifying them.
Owner-requested cadence overrides templates. Run only one workflow per mode;
the scheduler prevents overlapping runs of the same branch. Connected owner
compute is required for these agent steps.

Do not activate the watch template until actual sources, predicates and
connections are configured. Checks read current settings each time. Missing or
invalid files fail visibly and are never replaced with defaults. Off stops
unsolicited goal updates, while requested watches/reminders remain active.
Cancel them explicitly to stop them.

The helper creates goals-state.json, monitors-state.json and reminders-state.json
after successful checks. These are editable acknowledgements, not new goals.
An owner's settled notification decline is acknowledged without retrying; it
is reported as suppressed rather than delivered. Long notification summaries
are shortened with a pointer to the full owner file, which remains untouched.
Deleting a state file resets its history (goals establish a silent baseline;
requested watches/reminders may notify again). Keep these files when editing
skills. Notify success means an inbox item exists; inspect its delivery report
before claiming a phone received push. A crash between delivery and acknowledgement
can retry; existing outstanding-notification deduplication is not exactly-once delivery.
