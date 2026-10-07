# Deliver async action results as a follow-up (result-when-ready)

## Why

When a universe agent takes a heavy action from a chat turn — `run_graph` (which is
already fire-and-ack: `execute_branch_async` returns `queued` immediately) — the
action runs in the background on the Epoch-2 workers, but there is **no path to
tell the user the result when it finishes**. Today the user must ask again / poll.
For "the agent does real work and follows up," an enqueued action needs to deliver
its terminal result back to the originating conversation as a follow-up message.

The governed delivery primitive already exists but is **dark**:
`app_outbound_adapter.py::AppOutboundAdapter` (idempotent, authority-gated,
credential-blind) has no production caller. Slice 3 gives it one — a durable
outbox that links an enqueued action to its conversation and delivers the result
via the adapter when the run reaches a terminal status.

## What changes

- **A durable action-result outbox** (`storage/action_result_outbox.py`): when an
  action is enqueued from an authenticated app turn, record a content-free entry
  keyed by `run_id` — the originating `(workspace, channel, thread)`, the app
  binding reference, and the originating event/turn id. **No credential, no
  pre-authorized future body.**
- **Idempotent follow-up delivery** (`deliver_pending_action_results`): for each
  outbox entry whose run has reached a terminal status (`completed`/`failed` via
  `get_run`), compose a truthful result/failure summary, deliver it through
  `AppOutboundAdapter` with an idempotency key of `(run_id, terminal_revision)`,
  and mark the entry delivered. Re-resolve authority freshly at delivery time; a
  crash mid-delivery re-delivers safely (idempotent), never double-posts.
- **Fail-closed**: an entry is delivered at most once per terminal transition; an
  undeliverable/unauthorized entry holds (logged) rather than posting an
  unauthorized or duplicate message. A still-running run is left pending.

## Scope boundary

This slice builds the outbox + delivery core and its wiring to the run-terminal
signal + the outbound adapter. It does NOT change `run_graph`'s existing
fire-and-ack semantics, add new run authority, or require the in-flight
`harden-background-branch-execution-authority` change — it consumes the already
-shipped `execute_branch_async` queue, `get_run` terminal status, and the shipped
`app-outbound-adapter` / `app-reply-authority` capabilities.

## Impact

- Specs: `app-outbound-adapter` (its first production caller: a durable result
  outbox + idempotent terminal-result delivery).
- Code: new `tinyassets/storage/action_result_outbox.py`, new
  `tinyassets/action_result_delivery.py` (compose + deliver), a record site where
  an app-originated run is enqueued, and a delivery tick (scheduler/cadence or the
  run terminal-transition emit-site); plugin mirror + tests.
