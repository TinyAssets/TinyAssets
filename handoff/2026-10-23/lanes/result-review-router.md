Router branch: found the box's own meaningful 5xx responses being overwritten. Checking client consumers, then moving to the turn-budget branch.

Router tests: 8 of 34 fail locally. Finding out why.

Those failures come from Windows (`os.geteuid` doesn't exist here), not from the code. Now reviewing the turn-budget branch.

Both branches stay inside T1. The router still only maps and forwards, and the reconciler can't drop a durable table. But the router change has one real break: it rewrites every error response the box itself sends.

**VERDICT: DISAGREE_EVIDENCE.** The cause is finding R1 below. Everything else is low severity.

## conv/router-503-schema-drift

**R1 — HIGH — `tinyassets/owner_router.py:214`.** The line `if response.status_code >= 500: return _unavailable()` turns any 5xx the box sends into `503 owner_box_unavailable` with `Retry-After: 5`. The branch is meant to map transport failures; these are answers from a box that is up.
- **Retries that duplicate work.** A box 500 after a POST may already have committed. The page treats 503 differently from other 5xx:
  - In `tinyassets/onboarding/app/main.js` (~6409, ~6436), a 503 means "the uploader is busy — check again", marked retryable.
  - The comment on the other-5xx branch says the outcome is unknown and "the bytes may already have committed".
  - The router now erases that difference, and `Retry-After` invites a retry of an action the box may already have done.
  - Same problem at `onboarding/__init__.py:1213`: the box returns 503 `policy_applied_request_unresolved` with a `generation` *after* the policy is applied. The router replaces it with a generic "retry in 5s" and drops the `generation`.
- **The box's own reasons and timing are thrown away.** The router overwrites the box's specific 503 bodies and any `Retry-After` the box set. Examples: `box_draining` (`owner_ingress.py:112`), `profile_unavailable`, `timezone_unavailable`, and the `retryable` kind from `inline_requests.py:90`. Rewriting the box's contract is router policy, which goes beyond "only maps and forwards" (T1).
- **The test locks it in.** `tests/test_owner_router_unavailable.py` explicitly asserts 500/502/503/504 → 503.
- **Fix:** map only transport failures (`ConnectError`, a failed wake, an `HTTPError` before the response starts). Pass box 5xx through. If hiding internals matters, strip the body of a box 500 but keep the status 500, with no `Retry-After`.

**R2 — LOW — `tinyassets/owner_router.py:250`.** An `httpx.HTTPError` before the response starts (for example a write error or protocol error after the body was partly sent) now also gets `Retry-After`. That path doesn't prove the request was never sent. It returned 503 before this branch too, so the only new part is the explicit invitation to retry.

**Checks that passed:**
- **401/403 are kept.** Box 4xx responses pass through. `identity_conflict` stays 401. An admission refusal (`PermissionError`) still becomes 404 through `admit_box` (`:79`) and is never turned into 503.
- **No owner data in errors.** The 503 body is a fixed string.
- **Reconciler, `tinyassets/sqlite_schema.py`:**
  - A drifted durable table raises `SchemaDrift` and is left alone. Drop and create happen inside one savepoint and roll back together. The table name is quoted.
  - On races: in WAL mode, a second opener holding an old snapshot gets `BUSY` when it tries the DROP. It never drops the table the first opener just rebuilt. Worst case is one failed store open while the drift is cleared.
- **Table classification:** the history of `owner_sessions` and `owner_ui_prefs` shows no layout change, so the first deploy drops nothing.

**R3 — LOW — `owner_sessions.py:57` (`owner_sessions` marked ephemeral).** Any future layout change to this table drops every session and signs everyone out at once. That's acceptable for sessions, but it should be a conscious choice. A note at the declaration would make it one.

**R4 — LOW — `sqlite_schema.py:61`.** If the `ROLLBACK TO` fails (for example after SQLite has already rolled back the whole transaction), the `RELEASE` in `finally` raises "no such savepoint". That error hides the original one.

## conv/turn-reply-budget

**B1 — MEDIUM — `tinyassets/agent_turn_coordinator.py:600`.** If the final reply still asks for tools (some OpenAI-compatible backends ignore `tool_choice="none"`), the turn raises `ProviderProtocolError`. The paid-for final attempt is wasted, and any reply text in it is thrown away. The user gets an error instead of a summary. Better: drop the tool requests and keep the text. That still guarantees the final reply can't run tools.

**B2 — LOW — `tinyassets/agent_turn_coordinator.py:704–708`.** `_last_reply_attempt` triggers when the *current source's* free limit is ≤1. Tools are then disabled even when another free source could continue the turn, so multi-source free turns end early. It also ignores `deadline` and `failure_limit`, so the actual last request can still have tools on.

**Checks that passed:**
- **No widening to paid models.** `free_only` (`:750`) blocks the free-to-paid jump. A paid-to-paid move stays within the plan's accepted order, as before.
- **No infinite loop.** Each local failure adds the model to `visited` and `_budget_skipped`, and the candidate list is finite. When it runs out, `ProviderAuthorityHeldError` is raised.
- **No double charge.** Not-sent attempts don't count against any limit, and `spent_attempts` is only used for reporting.
- **Refusals and auth failures don't advance.** `provider_refused` and `auth_invalid` are excluded. `SelectedModelUnavailableError` is raised only while the plan is being prepared, before anything is sent.
- **The attempt slice is correct.** `receipt()["reserved"] == len(attempts)`.

## Tests
I could only run them on this Windows machine, and none of the failures say anything about the code:
- **Router branch:** `tests/test_owner_router_unavailable.py` and `tests/test_sqlite_schema.py` ran 34 tests: 26 passed and 8 failed. All 8 failures are `os.geteuid` missing on Windows.
- **Turn branch:** all 5 tests in `tests/test_turn_reply_reserve.py` errored at setup with "the credential broker is not running".

Linux CI needs to give the real result for both.

VERDICT: DISAGREE_EVIDENCE
