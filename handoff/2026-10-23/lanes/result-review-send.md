Still reviewing: backend intake read, now checking the browser recovery path for cross-account replay.

Checking the dispatch body for early-return paths that would mark a never-run message as completed.

Looking at how the browser's read-only "check saved conversation" settles a record — key to whether a pending-after-restart message ever runs.

A removed module is still imported at runtime — checking context.

The PR doesn't hold up. It has two blockers: one data-loss path, and one crash that breaks connector traffic on the box. I didn't run any test suites. I did run the inventory script and the deleted-module import directly.

## Findings

**1. HIGH (data loss): a message stored before a restart is never run.** `main.js:3582-3586` and `:3626`
- The browser's read-only check (`offerSavedConversationCheck`) deletes the device's recovery record as soon as it sees a founder row with the same `client_send_id` (`savedSendIndex`).
- Before this PR, that row was written only once the turn started. Now `admit_owner_send` writes it before dispatch, and a second message then waits behind the running turn for the box lock (`owner_door/send.py:556`). So the row no longer proves the message was dispatched.
- How it breaks: send B while A is running. B's row is committed with state `pending` and it waits for the lock. A deploy or box restart drops the request. The browser gets a transport error and `offerResend` auto-runs the check. The check finds B's row and forgets the record.
- Nothing on the server resumes `pending` rows; only a replay with the same identity does, and nothing in the repo calls `deliver_owner_send` outside `converse`. B shows in the transcript but never runs.
- `load_send_history` also hides B from the model. This fails T1-T10 "accepted but never run" and the spec's "Interrupted dispatch" scenario. `finishActiveTurn` (`main.js:3526`) is the same pattern.

**2. HIGH (deleted path still used):** `tinyassets/mcp_events_http.py:64` still has `from tinyassets.ingress import _invalid_constant, _object`, and this PR deletes `tinyassets/ingress.py`. `python -c "import tinyassets.ingress"` fails with `ModuleNotFoundError`. The broad `except Exception` turns that into a 503 "Event service unavailable". So in owner mode, every `/mcp` POST with protocol `2026-07-28` fails, converse included. No test covers this module.

**3. MEDIUM (CI break):** `scripts/check_background_authority_inventory.py:131` still lists `tinyassets/ingress.py`. The script prints "stale sensitive callsite" and exits 1. It is a structural guard (`ci_structural_guards.py`, `tests/test_background_authority_inventory.py`). `deploy/app-ingress-env.template` is also stale, and `storage/ingress_journal.py` now has no production caller.

**4. MEDIUM (a proven no-effect send gets stuck):** `universe_server.py:3433`
- The setup-hold branch now calls `_model_connect_request()` without the old try/except.
- If that call raises, the exception escapes after the state is already `running`. `converse` then tells the user to "retry this same send identity".
- Every retry reports `uncertain` and never runs, even though the effects were proven to be none. This breaks the spec's "Connection setup is incomplete" scenario.

**5. MEDIUM:** `conversation_store.py:631` `load_send_history` hides every row that isn't `completed` from the model, permanently. Rows stuck as `uncertain`, `pending` (see 1) or `held` stay visible in the transcript, but no later turn ever sees them.

**6. LOW-MEDIUM (retry stuck on a conflict):** `universe_server.py:3254`
- Choosing a selected conversation runs before the stored identity is looked up.
- Case: a main-thread send is admitted with no `consumer_request`, then a conversation design gets installed, then the same identity is replayed.
- The replay returns `consumer_request_required`. The browser makes a new `consumerRequest`, so the options no longer match and `admit_owner_send` returns `client_send_id_conflict` on every retry. The stored result is never returned and the row never runs.

**7. LOW:** `owner_door/send.py:572` forces `history_saved: True` onto results where the failure record wasn't saved, so the browser forgets the record. The message row itself is saved, so nothing is lost; the flag is just inaccurate.

**8. LOW (blocking):** the dispatch lock covers the whole conversation database (`send.py:532`), not one thread. Sub-agent sends, owner-answer continuations and agent wakes all wait behind the main agent's turn, while the spec says dispatch serializes per thread. Waiting requests also hold slots in the 8-worker converse executor.

**9. LOW (guard gap):** `tests/test_send_path.py` only tracks `admit_owner_send`, `deliver_owner_send` and `_dispatch_owner_send`. It doesn't catch other ways of writing founder rows:
- `record_turn(... "founder" ...)` (in `agent_turn_reconcile.py:173` and `control_plane/activities.py:220`)
- `_record_pair` with `founder_turn=None`
- `_write_pair` without `owner_send_turn`

Any of these could become a second intake without CI noticing.

**10. LOW:** `restoreInflight` (`main.js:9440`) runs once per page and only for the agent currently selected. Saved records for other agents aren't resumed until a reload while that agent is selected.

**What held up:**
- Sign-out/sign-in: recovery is filtered by owner, scope and agent, and the server keys sends by the verified actor's session. I found no way to replay another account's or agent's message.
- Two tabs or a retry with the same identity return the stored result and don't run twice.
- Legacy drafts get one stable ID and require an explicit Send or Discard.
- The steering, claim and batch callers really are gone. `/app/turn/steer` and `claimHeldLines` have no remaining callers.

VERDICT: DISAGREE_EVIDENCE
