# Design — Deliver async action results (Slice 3)

Builds only on already-shipped primitives: `execute_branch_async` (fire-and-ack
run queue), `get_run` (terminal status), and `AppOutboundAdapter` /
`AppReplyAuthority` (governed, idempotent, credential-blind delivery). No new run
authority; no dependence on the in-flight harden-background change.

## Outbox store (`storage/action_result_outbox.py`)

SQLite table in the trusted physical DB, content-free:

```sql
CREATE TABLE action_result_outbox (
    run_id            TEXT PRIMARY KEY,   -- the enqueued action's run
    universe_id       TEXT NOT NULL,
    workspace_id      TEXT NOT NULL,      -- originating conversation
    channel_id        TEXT NOT NULL,
    thread_ts         TEXT NOT NULL,
    app_binding_ref   TEXT NOT NULL,      -- opaque; re-resolved at delivery
    origin_event_id   TEXT NOT NULL,
    created_at        REAL NOT NULL,
    state             TEXT NOT NULL CHECK (state IN ('pending','delivered','failed_final')),
    delivered_at      REAL,
    terminal_revision INTEGER              -- the run revision delivered for
);
```

- `record(run_id, ...)`: INSERT OR IGNORE (an action enqueued twice for the same
  run_id records once). NO credential, NO reply body.
- `list_pending()`: rows in state `pending`.
- `mark_delivered(run_id, terminal_revision)` / `mark_failed_final(run_id)`:
  atomic state transition under `BEGIN IMMEDIATE`.

## Delivery (`action_result_delivery.py::deliver_pending_action_results`)

A tick (called from the scheduler/cadence loop OR the run terminal-transition
emit-site). For each pending entry:

1. `run = get_run(base, run_id)`. If run is None or not terminal
   (`completed`/`failed`) → leave pending (skip).
2. Compose a truthful, content-safe summary from the run's terminal status +
   receipts — success: a short "done" + any run-provided public result ref;
   failure: an honest "the background job failed at <safe phase>". NEVER leak
   internal detail; NEVER claim success on a failed run.
3. Re-resolve current app authority for the conversation FRESH (mapping/binding/
   revocation via `AppReplyAuthority`); if it cannot be authorized now → hold
   (do NOT post) and leave pending (or `failed_final` after a bounded retry age).
4. Deliver through `AppOutboundAdapter.deliver(authorization, summary)` with
   idempotency key `f"action-result:{run_id}:{terminal_revision}"` — the adapter's
   own idempotent receipt store guarantees at-most-once even across a crash/retry.
5. `mark_delivered(run_id, terminal_revision)`.

Idempotency + fail-closed:
- At most ONE delivery per `(run_id, terminal_revision)` (adapter key + outbox
  state).
- A crash between deliver and mark_delivered re-runs deliver next tick; the
  adapter's receipt short-circuits the duplicate → no double post.
- A `failed`/unauthorized delivery holds pending (logged); it is never dropped and
  never posted unauthorized. A run that never terminates stays pending (bounded by
  the outbox's own retention, not delivered).

## Wiring (minimal)

- Record: where an APP-ORIGINATED turn enqueues an action run
  (`api/runs.py::_action_run_branch` when the run carries an app-conversation
  origin) → `record(run_id, conversation, binding_ref, event_id)`. When there is
  no app-conversation origin (e.g. a direct API run), record nothing.
- Deliver: add `deliver_pending_action_results` to the existing scheduler/cadence
  tick (the same loop Slice-1's lease reconciler uses) — cheap, idempotent, a
  no-op when the outbox is empty.

## Tests

1. record → list_pending; INSERT OR IGNORE dedup on run_id.
2. delivery SKIPS a still-running run (get_run non-terminal) — leaves pending.
3. delivery of a COMPLETED run → composes a success summary, calls the (mocked)
   adapter with the `(run_id, revision)` idempotency key, marks delivered.
4. delivery of a FAILED run → honest failure summary, never "success".
5. IDEMPOTENCY: a second tick after delivered does NOT re-deliver (state) AND a
   crash-simulated re-deliver before mark uses the same idempotency key (adapter
   short-circuits) → at most one post.
6. FAIL-CLOSED: an unauthorized/failed delivery holds pending, never posts, never
   drops.
7. content-safety: the outbox row + composed summary carry NO credential and no
   pre-authorized future body; a failed run's summary leaks no internal detail.
