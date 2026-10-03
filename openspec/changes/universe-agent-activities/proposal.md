## Why

The universe-agent-harness design (#4172, founder-approved 2026-10-01) makes a
new user's command center work like a ChatGPT dot: one always-on agent that
works on several things at once, with no chat open, and picks up where it left
off after a restart. The harness requirement *An agent works on several
activities at once, without a connected client, and recovers them after a
restart* says what that does. It leaves four questions open:
- where the records live;
- under what authority an activity spends compute when no request is present;
- how an effect is recorded before it fires;
- how a schedule names an activity instead of a branch.

These are storage shape, authority, a served-tool contract and a migration,
which is exactly what a wrong guess makes expensive. Design §6 D2 therefore
opens this proposal before any D2 code.

What main has today (verified 2026-10-01 at origin/main):
- **No request, no turn.** Nothing runs an agent turn without a request. A
  served turn's provider capability is request-scoped, and background compute is
  admitted only for typed work items in the provider authority store (`run`,
  `background_attempt`, `branch_task`, `agent_invocation`).
- **Deploys lose served turns.** A served turn caught by a deploy is settled,
  never resumed. Run recovery interrupts a run only when its owner process is
  provably dead.
- **Effects are not journaled.** `authenticated_external_call` records nothing
  before a send. The existing reserve-before-send store lives inside the
  universe folder, which a provider jail binds read-write.
- **Automations need a branch.** They require `branch_def_id`, and their lease
  is keyed by it.

## What Changes

- **New platform store:** `.agent-sessions/<universe>/agent-activities.db`,
  outside every universe folder and beside `rules.db`. It holds activity
  records, effect intents and status lines.
- **New session keys:** `activity:<id>`, and `agent:<id>:thread` for D8. The
  main agent keeps `thread:principal:<owner>`.
- **Substrate:** an activity runs as runs of one owner-authored agent-node
  branch per universe, called *Activities*. It is part of the harness layer:
  seeded, visible and editable like `AGENTS.md`. This is the platform's one
  canonical way to run an agent with no client. Admission, credential, budget,
  seats, effect review and run-owner liveness all come unchanged from the
  foreground run lane. The authority store is not touched; a non-branch agent
  work item was tried before and retired. Linkage comes from the record, never
  from run inputs. One adapter is the only code that knows the substrate.
- **A durable dispatcher.** It runs on the pump cadence and on demand, never
  only at boot. It replaces a run only once that run has ended or been
  interrupted, and fences every runner write by generation. Waiting on the
  owner ends the run, which releases its seat.
- **Effect intents.** For runs an activity started, the effector commits
  `planned`, then `sent`, before the wire, keyed on the run, node, effect index
  and the resolved request. Transport uncertainty is recorded as `unknown`.
  After an interruption, an unknown effect becomes an owner question and is
  never retried blindly.
- **Served-tool contract:**
  - `write_graph target=activity` with operations `start`, `stop`, `pause` and
    `resume`;
  - `read_graph target=activities` and `target=activity`, complete and
    cursor-paged.

  D6's `ta activity` wraps these.
- **Schedules:** an ordinary automation targets the *Activities* branch with
  `{title, brief}`. A run of that branch with no activity named creates its
  record idempotently by `<automation_id>@<due_at>`. There is **no
  automations migration**.
- **Owner door `/app/activities`:** list, stop, pause, resume and delete, for
  the owner's own home only.

Not in this change: delegated authority (D8), research turns (D3), browser
contexts (D5), the `ta` command (D6), and nested activities.

## Capabilities

### New Capabilities
- `universe-agent-activities`: the activity store, the *Activities* branch
  substrate, dispatch and fencing, effect intents, and the served-tool
  contract.

### Modified Capabilities
- None. Schedules use automations unchanged.

## Impact

- **Storage.**
  - A new root-side DB. It is declared in `storage_accounting`, and its bytes
    are charged to the universe's quota.
  - Prerequisite: S2 (#4188) must land first. It removes
    `.agent-sessions/<home>` on account deletion.
- **Authority.** No change. Activities run on the foreground run lane.
- **Code.**
  - New: `tinyassets/agent_activities.py`, the dispatcher and runner.
  - The *Activities* branch seed (harness layer).
  - `effectors/authenticated_external_call`: effect intents for activity runs.
  - `runs.py`: activity linkage and recovery of intents.
  - `engine_mcp_server`, the onboarding door, `app.html`.
- **No public connector change.**
