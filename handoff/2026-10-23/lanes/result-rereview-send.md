Checking findings 1–10 against the new hunks; next I'll try to break the startup recovery path (`start_recovery`, `converse` identity, provider-request reservation).

Findings 2, 4, 6, 7, 10 look resolved; now reading `admit_owner_send` and the full `deliver_owner_send` to probe whether one stuck pending row can wedge its thread.

All ten earlier findings are fixed. But the new recovery and projection code adds two ways a saved message can end without ever running, so the round doesn't pass yet. I read the code by hand. I ran no test suites. I only ran `validate_inventory` (returns `[]`) and `scripts/check_shapes.py` (exit 0).

## Findings 1–10

| # | Status | Where |
|---|---|---|
| 1 | Resolved | The box now resumes `pending` rows itself: `owner_door/send.py:87-137`, started at `owner_runtime.py:253`. The browser only forgets a send once a reply linked to it exists (`main.js:3523`, `:3622`). |
| 2 | Resolved | `_object` and `_invalid_constant` now live in `mcp_events_http.py:28-38`, and the import of the deleted module is gone. |
| 3 | Resolved | The `ingress.py` inventory entry, the env template, the compose line, `ingress_journal.py` and its `FENCE_BEFORE_C2` entry are all removed. `check_shapes.py:230` still names `ingress.py`, but only as a file that must not exist, which is correct. |
| 4 | Resolved | `universe_server.py:3440-3445`: a failed connection request leaves the send retryable. |
| 5 | Resolved | `conversation_store.py:660-687`: unfinished rows are now shown to the model as labelled status notes, and the current send is left out. |
| 6 | Resolved | `universe_server.py:3255-3260`: a stored identity is looked up before any selection runs, and its saved `consumer_request` is reused. |
| 7 | Resolved | `send.py:79`: a `history_saved: False` from dispatch is no longer overwritten. |
| 8 | Resolved | `send.py:14-37`: one lock per thread, and it doesn't wait. A busy thread returns `already_received`/`pending` right away and the recovery scanner runs it later. |
| 9 | Resolved | There's an AST guard (`tests/test_send_path.py`) plus runtime refusals at `conversation_store.py:809-811` and `conversation_run_admissions.py:492-493`. The only remaining `record_turn` writers write `"platform"` rows. The exempt `_sync_tail_impl` has no outside caller. Finding B below comes from this fix. |
| 10 | Resolved | `main.js:9440,9462`: saved sends for every agent the user owns are resumed; ones for agents not on screen run in the background without a chat bubble. `durable_send.js:13,24`. |

## New findings (data-loss class)

**A. MEDIUM: a drain or stop turns queued messages into failures without running them.** `owner_door/send.py:113-135`, with the state change at `:75`.
- A recovery worker enters `@owner_work` once, then works through every pending row in its thread. It never checks `stop` or whether the box is draining between rows.
- How it breaks: the user sends A, B and C quickly, so B and C wait as `pending`.
  1. A finishes, and a worker starts B.
  2. A deploy sends `/_box/snapshot-stop` ("finish admitted turns; refuse the next turn"), which sets the box to draining.
  3. B finishes. The worker moves on to C and sets it to `running` at `:75`.
  4. Dispatch reaches `session.start`, which is `@owner_work`. `Activity.enter` raises "owner box is draining".
  5. `universe_server.py:3421` records that as a failed turn and C is marked `completed`.
- C never ran. The next box generation won't resume it, because only `pending` rows are resumed. That breaks the design's "resumes pending requests on startup".
- The same gap shows up after `stop.set()`. The workers are daemon threads that `serving()` never joins, so they keep starting rows during the roughly 60 s shutdown, and process exit leaves those rows `running` (shown as uncertain) for good.
- Fix: enter the activity gate per row, before the `pending`→`running` change. Also check `stop` before each row, so a draining box leaves the row `pending`.

**B. MEDIUM: consumer runs left unprojected at cutover can never be written to the transcript.** `storage/conversation_run_admissions.py:492-493`
- On `main`, `_write_pair` writes the founder row and the reply together when a consumer (custom-conversation) run is projected. `owner_send_turn` doesn't exist on `main`.
- This round deleted the fallback for rows without it. Now any consumer admission still `pending` or `held` from before the deploy raises `TerminalUnavailable` on every repair attempt (`consumer_runtime.py:79`).
- That covers runs in flight across the deploy and existing `held` rows waiting for repair.
- A browser replay doesn't help either. `converse_turn` finds the existing row and skips setting `owner_send_turn` (`consumer_runtime.py:253-255`).
- So the message and the reply never appear in the transcript, even though the run finished. The PR has no migration for these rows.
- Fix: allow the old founder-plus-reply projection only for admissions made before the cutover. It records a run that already happened, not a new intake. Or migrate those rows in the cutover change.

## What held up
- **Running a send twice:** didn't happen in any case I tried:
  - Two box starts or a restart: state only changes to `running` while both the thread lock and `flock` are held. The `running` state is set before dispatch. Recovery only takes `pending` rows. The losing caller gets the stored receipt.
  - Browser retry racing recovery: same lock. The oldest-first check stops a later row from jumping ahead.
  - Repeated recovery attempts: provider-request reservations use a fresh nonce each time, so retrying them is safe.
  - Paths: the lock path is resolved, so the browser's `_universe_dir(uid)` and recovery's `runtime.root/center` point at the same file. `TINYASSETS_DATA_DIR` is set to `root`. `.lock` files don't trip `validate_databases`.
- **Another account's or agent's send:** `agent_of_session` rebuilds the thread key from `runtime.principal`, so another owner's thread key gives `None`. The agent is resolved from the stored thread key, and `memory_session(owner, agent)` gives back the same key.
- **Two turns of one thread at once:** every thread has exactly one key, and the per-thread lock covers every `deliver_owner_send` caller.
- **Writer guard:** it found no unguarded founder-row writer in the current tree.

A residual risk, not a finding: if a future change makes the oldest pending row fail before admission every time, the oldest-first check would hold back every later send in that thread. I found no such trigger in today's code. A deleted sub-agent's thread does stay pending and gets retried every 0.5 s, but nothing new can be sent to that agent anyway.

VERDICT: DISAGREE_EVIDENCE
