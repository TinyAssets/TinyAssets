## Context

Two harness requirements (change `universe-agent-harness`) already specify the
behaviour:
- *An agent works on several activities at once, without a connected client,
  and recovers them after a restart*;
- *The agent's activities and schedules are read completely*.

This design covers only what they leave open:
- where the records live;
- what an activity executes on when no request is present;
- how an effect is recorded before it fires;
- how an activity is fenced, dispatched and resumed;
- how a schedule starts one.

Facts from origin/main, 2026-10-01, re-checked after the gpt-6-astra refute
(ADAPT) and the substrate decision (lead, 2026-10-01, option A):

| Fact | Where |
|---|---|
| A served turn's provider capability is request-scoped and dies with the request | `auth/middleware.provider_request_capability` |
| Every provider admission on main is branch-lineage. `_validate_work_lineage` requires a `BRANCH_VERSION` subject, and run admission refuses any kind but `run` | `provider_work_authority.py` (`_validate_work_lineage`); `storage/provider_work_authority._admit_run_in_transaction` |
| A non-branch agent work item existed and was **retired**, because "the agent node replaced it" | `storage/provider_work_authority.py` (`agent_invocation receipts are retired`); #2145 |
| The one canonical way to run an agent with no client is an agent node in a branch run: foreground admission (founder home, branch author = principal, assignment, credential, budget) and an agent-node seat | `foreground_run_provider._ForegroundRunProviderSession`; `graph_compiler._run_agent_with_timeout`; `shared_self` |
| A run is interrupted only when its owner's liveness lock is provably dead. `interrupted` is terminal | `runs.recover_in_flight_runs` |
| `authenticated_external_call` receives run and node identifiers, never an agent tool-call id, and records nothing before a send | `effectors/authenticated_external_call.py` |
| The existing reserve-before-send store lives in the universe folder, which a provider jail binds read-write | `storage/external_write_receipts.py`; `providers/provider_jail` |
| Automations already fire branch runs under the full user-owned contract | `automations.py`; spec `user-owned-automations` |
| Account deletion stages `<root>/<home>`. Removing `.agent-sessions/<home>` is a separate prerequisite PR | `account_deletion.py` |

## Goals / Non-Goals

**Goals**
- A durable activity record that no agent can forge.
- Execution on the platform's one canonical client-less agent path, with no
  change to the credential-guarding authority store.
- At most one live run per activity.
- No external effect sent twice across a crash.
- Durable dispatch that does not depend on boot.
- Complete, paged reads.
- Schedules that start activities.

**Non-Goals**
- Delegated authority and the research flag (D8, D3).
- Browser contexts (D5).
- The `ta` command (D6).
- Nested activities.

## Decisions

### 1. Records live outside the universe, beside `rules.db`

The store is `<data root>/.agent-sessions/<universe>/agent-activities.db`. A
provider jail binds the universe read-write, so anything inside it could be
forged. The name avoids S4's `activity.db` (the tool journal).

The schema uses SQLite with WAL and `busy_timeout`. Every transition runs under
`BEGIN IMMEDIATE`, with compare-and-set on `revision` and on `runner_generation`.

```sql
CREATE TABLE activities (
  activity_id        TEXT PRIMARY KEY,           -- 'act_' + 16 hex, platform-minted
  agent_id           TEXT NOT NULL DEFAULT 'main',
  parent_activity_id TEXT NOT NULL DEFAULT '',   -- recorded for D8; always '' here
  session_key        TEXT NOT NULL UNIQUE,       -- 'activity:<activity_id>'
  owner_principal    TEXT NOT NULL,              -- derived server-side at creation
  title              TEXT NOT NULL,              -- one line, <= 200 chars
  brief              TEXT NOT NULL,              -- <= 16 KiB
  origin_kind        TEXT NOT NULL CHECK (origin_kind IN ('ask','proposal','schedule')),
  origin_ref         TEXT NOT NULL DEFAULT '',   -- turn id | proposal id | '<automation_id>@<due_at>'
  approval_id        TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL CHECK (status IN
                       ('scheduled','in_progress','waiting_on_you','paused','completed','failed')),
  outcome            TEXT NOT NULL DEFAULT '',   -- done | stopped | failed:<class>
  waiting_reason     TEXT NOT NULL DEFAULT '',
  waiting_request_id TEXT NOT NULL DEFAULT '',
  result_summary     TEXT NOT NULL DEFAULT '',   -- <= 4,000 chars; full result in a workspace file
  result_path        TEXT NOT NULL DEFAULT '',
  last_tool_seq      INTEGER NOT NULL DEFAULT 0,
  runner_token       TEXT NOT NULL DEFAULT '',   -- the current run's id (substrate handle)
  runner_generation  INTEGER NOT NULL DEFAULT 0, -- fences every write a runner makes
  revision           INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX activities_by_update ON activities(updated_at DESC, activity_id DESC);
CREATE UNIQUE INDEX activities_by_schedule ON activities(origin_ref) WHERE origin_kind = 'schedule';

CREATE TABLE effect_intents (
  intent_key     TEXT PRIMARY KEY,   -- sha256(run_id, node_key, effect_index, wire_digest)
  activity_id    TEXT NOT NULL,
  run_id         TEXT NOT NULL,
  node_key       TEXT NOT NULL,
  effect_index   INTEGER NOT NULL,
  wire_digest    TEXT NOT NULL,      -- digest of the RESOLVED request: method, URL, transformed body
  connection_id  TEXT NOT NULL, operation TEXT NOT NULL, path TEXT NOT NULL,
  state          TEXT NOT NULL CHECK (state IN
                   ('planned','sent','confirmed','failed','unknown','owner_resolved')),
  resolution     TEXT NOT NULL DEFAULT '',  -- happened | not_happened | try_again
  receipt_json   TEXT NOT NULL DEFAULT '',  -- status code + safe summary only
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE activity_events (
  activity_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL,
  kind TEXT NOT NULL, line TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (activity_id, seq)
);
```

**Status lines** are composed by the platform. The only agent-supplied text a
line carries is the activity's title: quoted, one line, at most 80 characters.

**Lifecycle**
- *Retention.* Records stay until the owner deletes an activity, or the account
  is deleted.
- *Events.* At most 200 per activity; the oldest delivered ones go first.
- *Reads.* Event and intent reads are paged.
- *Quota.* The store's bytes (`agent-activities.db` and its WAL) are a
  `SCOPE_UNIVERSE` store in `storage_accounting.STORES`, so they count against
  the universe's quota.
- *Account deletion.* Removing `.agent-sessions/<home>` is a prerequisite.
  Before that removal, deletion fences every activity
  (`runner_generation + 1`, `failed:account_deleted`). Only creation may bring
  the store into existence, and only for a universe that exists, so a run that
  outlives the deletion cannot recreate it.

### 2. Session keys

- An activity is `activity:<activity_id>`.
- A further roster agent (D8) is `agent:<agent_id>:thread`.
- The main agent keeps `thread:principal:<owner>`.
- `agent_sessions` validates no key prefix.

### 3. Substrate: runs of the universe's activity branch (harness layer)

An activity executes as **runs of one owner-authored agent-node branch per
universe**, called *Activities*. It is part of the harness layer: seeded on
first use and at onboarding, visible, and editable like `AGENTS.md`. An owner
who wants their agent to work differently in the background edits that branch.

This is the platform's one canonical way to run an agent with no client
attached, so everything comes from existing machinery and the authority store
is unchanged:
- **Admission, credential and budget:** the foreground run lane, through the
  exact requestless recipe automations use:
  - `_bind_automation_provider_call`-style binding with the record's owner;
  - `owner_run_identity` with its boolean consumed (false means `owner_lost`);
  - actor `universe:<id>`, with `owner_user_id` persisted;
  - the per-node authority guard, which re-checks the owner's admin ACL live.
    Foreground admission alone checks founder home and branch authorship, not
    admin revocation.

  The branch is seeded with the owner as author, and only for an owner acting
  authenticated.
- **Seats:** the agent node's seat. It waits without a deadline, is never
  refused, and is released when the run ends.
- **Effects:** `EffectChain`, with the D1d auto-review bound.
- **Liveness:** run-owner liveness and the run recovery rules.

Each run carries `activity_id` in its inputs, but **inputs are never trusted
for linkage**. A run counts as an activity's run only if the record names it
(`runner_token = run_id`, set when the run is claimed, below). Only then does
its agent node continue the session `activity:<id>`, report progress and write
effect intents. Session selection, lineage, fencing and intent accounting are
platform code around the agent node. An edited branch keeps or loses its node,
but cannot change them.

**Unlinked runs do nothing activity-shaped.** A run of the *Activities* branch
that no record names is refused at its start barrier with
`activity_run_unlinked` ("start an activity instead"). That covers a run the
agent starts through `run_graph` with a forged `activity_id`, and a manual run.

**The adapter boundary.** The record, session, status lines, effect intents and
the Activity tab touch the substrate through one adapter,
`ActivityRunner { start(activity) -> run_id; state(run_id) -> live|ended|dead; stop(run_id) }`.
Changing the substrate later replaces only this adapter.

**If the branch is missing or broken:**
- an owner who deleted it gets it re-seeded on the next start;
- one edited into something with no agent node refuses the start with
  `activity_branch_invalid` and names the fix.

### 4. Dispatch and fencing: one live run, never boot-only

A dispatcher runs on the automation pump's cadence (30 s) and whenever an
activity is created or answered. It selects:
- `scheduled` activities;
- `in_progress` activities whose run has ended without settling the record, or
  has been interrupted.

A live run, or one whose owner is alive or unknown, is never replaced.

For each activity it does **reserve, bind, release**:
1. **Claim** by compare-and-set: `runner_generation + 1`, `runner_token = ''`.
2. **Reserve** a run row in `queued` state, which mints its `run_id`. Execution
   has not started.
3. **Bind**: stamp `runner_token = run_id` under that generation, by
   compare-and-set.
4. **Release** the run to the executor. Its start barrier re-reads the record
   and executes only if the record names this run under a current generation.
   Otherwise the run ends `activity_run_unlinked` without executing anything.

Recovery for every intermediate state:

| Crash after | State found | Dispatcher action |
|---|---|---|
| 1 | `in_progress`, empty `runner_token` | Re-claim (generation + 1). Nothing ran |
| 2 | `in_progress`, empty token, an unlinked queued run | Re-claim. The orphan run fails its barrier |
| 3 | `in_progress`, token = a queued or running run | Leave it. If its owner is provably dead, run recovery interrupts it, then re-claim |
| 4 | live run | Leave it |

Runner writes carry the generation, so a superseded run's writes change
nothing.

**Waiting is a yield, not an interruption.** Today `ask_first` and an
auto-review hold return a refusal to the agent; they do not suspend anything.
Inside an activity run, that refusal leads to a **yield**:
1. Every effect of the run has already settled, because sends are synchronous:
   each intent is `confirmed`, `failed` or `unknown`, and none is `planned` or
   `sent`.
2. The agent raises one owner request. It is bound to the exact pending action
   (class, connection, operation, path and its action digest), and its id is
   recorded as `waiting_request_id`.
3. The activity moves `in_progress -> waiting_on_you`.
4. The run ends with ordinary run status `completed` and outcome `yielded`,
   which releases the seat.

A yield is never labelled an interruption, and interruption recovery never
touches it. The owner's answer moves the activity `waiting_on_you -> scheduled`
by compare-and-set on that request id. An approval carries the approval id for
that exact action (D2/D3), so the next run's identical action proceeds under
*if pre-approved* and still passes auto-review.

**Pause and stop** take effect at the activity run's next tool boundary.
`holds(generation)` fails, and the run ends. Stop keeps `result_summary`.

### 5. Effects: a platform intent recorded before the wire

External effects fire in the activity's runs, and in runs those runs start.
Lineage comes from the parent run, never from inputs. The effector never has an
agent tool-call id, so the platform mints the intent from what it has:

`intent_key = sha256(run_id, node_key, effect_index, wire_digest)`

`wire_digest` hashes the resolved request: method, URL and transformed body.

**Before and after the wire:**
1. `INSERT planned`. A key conflict means the effect was already attempted, and
   nothing is sent.
2. Commit `sent` durably before the wire. If the commit fails, nothing is sent.
3. Afterwards, record `confirmed` or `failed` with a safe receipt. Uncertainty
   after the request was written is `unknown`, never `failed`.

**Recovery.** Intents are recovered only after their run is interrupted, which
happens only once its owner is provably dead:
- `planned` becomes `failed` (`not_sent`);
- `sent` becomes `unknown`.

The activity then waits on the owner: "this may already have happened:
<operation> <path> on <connection>". The answers are *happened*, *not happened*
and *try again*. Only *try again* permits a new run for that action.

Requests from the agent's own bash, through the egress proxy, are governed as
`shell.egress` by the rules and are not effector calls.

### 6. Resume

The next run of an activity continues `activity:<id>`:
- natively, when the adapter resumes (S1 `native_resume`);
- otherwise, from a session built from the record:
  - the brief;
  - completed tool calls up to `last_tool_seq` from the durable turn journal, or
    S4 safe summaries;
  - the partial result;
  - the effect resolutions.

It is told plainly that it was interrupted. Completed runs are their own
records, so nothing is replayed.

### 7. Schedules start activities without an automations migration

An automation that starts an activity is an ordinary automation targeting the
universe's *Activities* branch, with inputs `{title, brief}`. Automations,
their lease keys and their contract are unchanged.

**Creation requires server-owned provenance.** An activity is created from a
schedule only when the automation pump fires that automation. The pump passes
a platform-only firing context that is never a run input: the claimed attempt's
owner, `automation_id` and `due_at`. `_run_due_automation` already knows the
due time, and `_execute` gains the parameter.

With that context, the platform creates the record before reserving the run
(`origin_kind='schedule'`, `origin_ref='<automation_id>@<due_at>'` under its
unique index), then does reserve, bind and release (Decision 4).
- Re-firing the same attempt after a crash returns the same record.
- Every other run of the branch is unlinked and refused (Decision 3). No
  ordinary run creates an activity.
- Creation goes through one function for every origin, which applies the owner
  derivation and the nested-activity refusal. The Scheduled view lists those automations next to the
research schedule (D3).

### 8. Served-tool contract and reads

These calls are on the universe agent's served tools. The public connector is
unchanged.

| Call | Effect |
|---|---|
| `write_graph target=activity operation=start` `{title, brief}` | creates `scheduled`, wakes the dispatcher, returns `{activity_id, status}` |
| `write_graph target=activity operation=stop` / `pause` / `resume` `{activity_id}` | a checked transition, own universe only |
| `read_graph target=activities` `{status?, cursor?}` | every activity, 50 per page, keyset cursor |
| `read_graph target=activity` `{activity_id, cursor?}` | the record plus one page of events and intents |

- Pages are complete or carry `next_cursor`. Nothing is cut for size.
- `operation=start` inside an activity run is refused with
  `nested_activity_unavailable`.
- **The owner door** `/app/activities` offers list, stop, pause, resume and
  delete, for the authenticated owner's own home only, with compare-and-set on
  `revision`.

## Risks / Trade-offs

- **The substrate is a branch run.** The founder's direction is the dots
  *experience*, which this keeps. The branch is framed and placed as part of the
  harness layer (seeded, visible, editable), not as a background self. The
  alternative, a new non-branch authority kind, would change the
  credential-guarding store and reverse the `agent_invocation` retirement. The
  one-adapter boundary keeps a later reversal cheap.
- **An owner can break their own Activities branch.** It is re-seeded when
  missing, refused with a named fix when invalid, and has history and undo like
  every harness file.
- **A deploy during a send becomes an owner question.** That is deliberate.
- **No generic receipt read-back.** Unknown effects stay owner questions until
  connections can declare idempotency headers. That needs no storage change.

## Migration Plan

1. Land the `.agent-sessions/<home>` account-deletion PR first.
2. Ship the store, the *Activities* branch seed, the dispatcher and the
   served-tool targets. Nothing starts an activity until the agent calls
   `operation=start`.
3. Live proof:
   - two activities in parallel after the chat is closed;
   - one surviving a deploy with an effect in flight;
   - one started by a Monday automation.

**Rollback.** Before reverting, two steps:
1. Pause every automation targeting an *Activities* branch.
2. Stop all activities: fence them and let their runs end.

Then revert. Old code ignores the store. It would still load and run the
branch for any automation left active, without activity accounting, which is
why those automations are paused first.

## Open Questions

None blocking.
