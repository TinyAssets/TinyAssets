# Consent answer boundary (2026-10-05)

Lane: `fix/consent-asks-owner-session`, draft-PR delivery only. One central
`CONSENT_ACTIONS` set extends #4477's guard to publish, install, connect,
connect_http, extend_http, rotate_http, remove_http, grant_workspace_consent,
bind_model_access and grant_patch_intake. All app answer controls use
`/app/approvals/answer`. Existing bound HTTP scopes and their token checks remain
unchanged. Tasks 1.2–1.4 are partial; universal legacy action tokens, trusted
classification and payment-scope contracts are not claimed complete.

## Answer entry-point audit

| Entry point | Consent boundary |
| --- | --- |
| `pending_requests.answer_request` | No owner proof parameter; central guard before resolution/effects |
| connector `write_graph(target=connection, operation=answer_request)` | Same public handler; payload proof is ignored |
| served chatbot/agent `write_graph(target=pending_request)` | Existing operation allowlist excludes answers/unmute; direct connector calls still meet central guard |
| Item, Deny, Clear, retry and `dont_ask_again` variants | Guard precedes every branch, including resolved-request retries |
| Immutable publish/install pin with edited row type | Guard looks up pin before trusting the row type |
| `/app/approvals/answer` | Exact-origin JSON always required; optional matching-owner cookie supplies proof; central server classification requires it only for consent |
| `/app/approvals/preview`, `/edit`, `/decide` (including retries) | Owner-cookie gate; bound actions retain single-use revision/action/scope tokens |
| `/app/model-connect/oauth_exchange` → OAuth `complete` → `answer_connect_with_token` | Cookie check before flow consumption/exchange; internally passed proof; both helpers refuse absent proof |
| `/app/rules` writes | Owner-cookie gate; rules cannot bypass consent answer classification |
| `unmute_request` | Only lifts suppression; replacement answers still require owner proof |

## Verification scope

Per-kind approval success remains covered by existing consumer suites:
pending_requests (connect_http, extend_http), unify_connection_uses (connect),
command_center_packages (publish/install), replacing_a_rejected_credential
(rotate_http), taking_a_key_back_and_putting_it_back (remove_http),
workspace_authority (grant_workspace_consent), model_access_requests and
patch_intake_seed. Their owner gestures exercise the real protected HTTP handler
with stored cookies; no existing assertion is removed or relaxed.

The consent matrix covers all ten kinds through API and direct write_graph,
invalid proof, Clear/Deny recovery and unmute; it also tests OAuth token answers,
preview/edit/decide and rule-write refusal. Ordinary question/item/proposal suites
now retain bearer coverage after the round-1 correction; the earlier wholesale owner-helper conversion had removed question/item bearer coverage.

## Verification results

- Windows Python 3.14: 1,215 distinct tests passed, 3 platform skips. The 715-case
  consumer/browser run initially had 21 package-browser failures (old transport
  expectation and the helper's nested event loop); the corrected 21-case file
  passed. The other runs covered 184 core cases (46 pending-request cases overlap),
  192 recovery cases, 10 served-entry cases, 16 owner-session/rules cases and 147
  onboarding cases. The corrected helper also passed the 105-case consent/session
  subset. No unresolved failures; no assertion was weakened.
- Linux oracle Python 3.11: 1,217 distinct tests passed, 1 Windows-junction skip.
  The initial 1,055-case snapshot had 1,034 passes and the same 20 pre-fix nested-loop
  browser failures. The final 319-case run passed (21 package-browser, 89 consent,
  46 pending-request, 16 session/rules and 147 onboarding); its 163 additional
  session/rules/onboarding cases bring the distinct total to 1,218 cases.
- Oracle uses `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- <test paths>
  -q --tb=short --basetemp /tmp/b`. This checkout supplies `python -m pytest`
  internally; passing that prefix after `--` failed collection and was corrected.
- Changed-file Ruff, OpenSpec validation and plugin build/import probe passed.
  Mirror parity passed for all four runtime files. Hygiene: 7 test functions
  added (89 parametrized cases), 0 removed, 0 tampering findings.

The platform skips are OS-specific link tests, not failed or skipped consent
proof. POSIX cases run in the Linux oracle and the junction case runs on Windows.

## Cross-family review

The full round-1 Claude review of draft PR #4483 returned **BLOCK**.
AGREE: centralized consent refusal, immutable pin lookup, OAuth completion,
matching-owner proof and bound HTTP scopes hold. AGREE with the correctness
finding: requiring the cookie before classifying every app answer regressed
native and expired-session non-consent answers. AGREE with the coverage finding:
ordinary question/item tests had been switched to the owner helper.

The answer route now requests optional owner proof while preserving exact-origin
JSON checks. Missing/expired cookies produce `owner_session=None`; the existing
server classification alone decides consent requirements. Preview/edit/decide
still require proof. No action-kind list was added to JavaScript. A 401 sets
`authRequired`; ModelAccess preserves the request and shows the protected sign-in
link on missing proof. A signed-in `approve_action` answer returns 409
`preview_required`, directing the user to its bound card rather than another login.
Plain-question/item helpers exercise bearer answers again; consent consumers use
an explicit owner helper and the refusal matrix remains intact.

The review's classification concern describes future extensibility, not a current
bypass; this correction retains the existing central classification as requested.

No deployment or live-user acceptance is claimed by this draft PR. The existing
change's broader spec sync/deployment acceptance stays open.

## Resume verification (2026-10-05)

The review correction passed the same final selection on both platforms:
Windows Python 3.14 **364 passed**, Linux oracle Python 3.11 **364 passed**,
with no failures or skips. This selection comprises
`test_inline_approvals_real_browser.py`, `test_inline_approvals.py`,
`test_inline_owner_sessions.py`, `test_approval_sheet_scopes.py`,
`test_approval_sheet_real_browser.py`, `test_onboarding_app.py`,
`test_consent_owner_answers.py`, and `test_pending_requests.py`.
These are reruns of earlier coverage plus the five new error-transport cases,
not 728 additional distinct tests. The earlier consumer/recovery evidence above
remains applicable. Changed-file Ruff, strict OpenSpec change validation,
plugin build/import probe, and parity for all four runtime mirrors passed again.
Those results predate the full round-1 BLOCK review; the correction and fresh
verification below supersede that earlier completion claim.


## Round-1 BLOCK correction verification (2026-10-05)

Final complete runs: **Windows Python 3.14: 547 passed; Linux oracle Python 3.11:
547 passed. Zero failures and zero skips on both.** The 16-file selection includes
the previous eight-suite resume selection, request_items_and_delivery,
model_access_requests, connection_sheet_continuations,
four_boxes_become_a_signed_request, multi_value_credential_ask,
replacing_a_rejected_credential, request_rail_honest_asks, and
taking_a_key_back_and_putting_it_back. The six dependent consent suites now import
`_owner_answer` explicitly; `_answer` is again the bearer-only test transport.

New coverage includes absent/expired owner cookies for ordinary answers,
Clear and Deny versus consent refusal; cross-origin refusal without a cookie;
a signed-in bound action receiving preview guidance; 401 authentication flags;
and a real Chromium model-answer sign-in link with the pending request retained.
Initial test-fixture failures were corrected before these final complete runs:
the no-cookie fixture needed the canonical displayed-row deduplication key.
That repeated fixture failure was handed off using peer-agents; no runtime
change or assertion relaxation was required.

Changed-file Ruff and `git diff --check` passed. Plugin build/import probe passed;
all **601** canonical files mirror-match. Full-PR hygiene reports **14 test
functions added, 0 removed, 0 tampering findings**. This is a draft-PR push,
not deployment or live-user acceptance.


## PR #4483 CI repair (2026-10-05)

Merged origin/main at f0e4222763 before reproducing affected-tests job
111676802810 on the Linux oracle. The unchanged failing suite reproduced
**5 failed, 10 passed**. All five failures came from consent tests still using
`_answer`, the bearer-only transport restored by the round-1 correction:

- `test_the_owner_takes_a_key_back_from_the_rail`: removal was refused before
  returning the expected answered status.
- `test_removing_something_already_gone_is_not_an_error`: the same missing owner
  proof prevented the idempotent removal path from running.
- `test_a_standing_yes_cannot_swallow_a_removal`: the first removal was refused,
  so the stored-suppression and subsequent-removal assertions were unreachable.
- `test_a_standing_yes_cannot_swallow_a_grant_widening_either`: the first widening
  was refused, leaving the repeated ask pending instead of already held.
- `test_what_executes_is_what_the_owner_was_shown`: missing owner proof stopped
  execution before the displayed-action mismatch check.

This PR intentionally changes consent answers to require a protected owner
session. The repair therefore changes these tests' transport to `_owner_answer`,
which exercises the real HTTP cookie/origin checks. The other removal calls in
this suite use that transport too, including the stranger test, which now proves
that an authenticated non-owner still cannot remove the owner's credential.
The ordinary-question test retains bearer `_answer`. All 44 assertions remain
AST-identical; no tests were removed, skipped, xfailed or weakened. No additional
product change was needed.

Final Linux oracle (Python 3.11.16, real bubblewrap, uid 1001): **211 passed,
0 failed, 0 skipped** across `test_removal_is_reachable_from_the_served_surface`,
`test_pending_requests`, `test_consent_owner_answers`,
`test_taking_a_key_back_and_putting_it_back`, and `test_unify_connection_uses`.
Command: `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <those five
suite paths> --basetemp /tmp/b`. None of these files is heavy-listed.
Changed-PR-file Ruff, all seven pre-commit invariants, plugin build/import probe,
all 602 shipped runtime-file comparisons, and `git diff --check` passed.

The separate Diff scope declared job 111676788855 reports a missing current
Drain-Review approval receipt. This test repair does not claim a new approval,
deployment, or live-user acceptance; the existing review record remains above.

Full-PR hygiene against merged origin/main: **14 added, 0 removed,
0 tampering findings**. Both merge and repair commits carry the required
Claude Opus 5.5 co-author trailer.


## PR 4483 OAuth/browser repair (2026-10-05)

Merged origin/main before reproducing merge-queue run 37287591956 / real-browser
job 111689824472. Linux oracle (uid 1001, Python 3.11.16, Chromium, bubblewrap
0.12.0), marker `real_browser or not real_browser`, the three reported modules:
**14 failed, 64 passed**, including seven real-browser cases. Status reproduced
exactly: `Finish signing in, then return here. Your message is kept.` followed
by `Connection is not confirmed yet. Your message is kept. Retry or choose Other AI.`

Evidence separates three causes: first-run Chromium scripts mocked only the old
MCP answer transport, while the PR now sends answers to the protected HTTP door;
HostedModelConnect returned early on a transient answer exception, bypassing its
existing recovery/readback; rail extraction tests still expected the explicit
protected patch-consent door. Separately, generic OAuth completion required a
fresh cookie instead of binding consent at start, and inline bootstrap completion
needed the same bound proof to activate its free-only request after browser return.

Generic OAuth now requires protected proof at start and records approved_owner
in the flow (legacy rows default unapproved). Completion validates owner, home,
PKCE, action and expiry, consumes once, and deposits using that bound proof.
Inline start seals proof into its existing expiring flow; protected launch still
checks the owner, and callback still requires its per-flow browser cookie.
Cookie-free polling consumes the flow and answers only the server-created
free-only bootstrap request. The UI uses that completion receipt. An owner
cookie at completion cannot upgrade a legacy unapproved flow.

Every existing Chromium assertion is preserved. Its scripted inline poll now
models the server's completion receipt and throws on any bearer tool answer.
Added backend checks cover absent/forged/foreign start proof, legacy unbound
flows, and completion with no owner cookie. Existing PKCE, action-change,
expiry, owner-isolation and replay checks remain active. Restored transient
answer recovery and the rail's explicit protected patch-consent call.


Cross-family review: Claude via peer-agents, one round, verdict ADAPT.
- AGREE: begin-time owner-login refusal needed a recovery link. Both inline
  and generic starts now expose protected sign-in; added real Chromium checks
  for the links, saved message/no answer, enabled retry and cleared PKCE draft.
- DISAGREE_EVIDENCE (low, outside floor): require the original owner session to
  remain live until completion. The requested contract explicitly binds consent
  at START and allows completion without the cookie; authorization is the
  independently expiring single-use flow, not a reusable session. Its owner,
  home, PKCE/browser, action/preset and TTL bindings remain enforced. No general
  consent-answer door accepts this proof from a caller. This review is not a
  deployment or live-user acceptance claim.


Broad Linux validation of every PR-touched test module plus the two reported
JS execution modules: **1,025 passed, 1 skipped** in 380.36s. The single skip is
`test_a_directory_junction_is_never_followed` (Windows-only); its separate
Windows run passed (1 passed). The broad snapshot preceded the review-driven
sign-in recovery links; those paths and the original reported modules are
rechecked separately on Linux below. Changed-PR-file Ruff and plugin build /
import probe / whole-tree mirror parity (606 canonical files) pass. A broad
`ruff check .` also exposed 55 pre-existing findings outside this PR's diff;
no unrelated files were changed. All assertions in the nine pre-existing
first-run browser test functions remain AST-identical to origin/main.


Final Linux recheck after the review adaptation: **114 passed, 0 skipped** in
70.78s across first-run connect Chromium, hosted-model JS, request-rail JS and
generic OAuth modules. JUnit verification across the broad run and final
recheck confirms **54 distinct real-browser cases passed, none skipped or
failed** (21 first-run connect cases, including four new recovery cases).
Full-PR hygiene: **20 added, 0 removed, 0 tampering findings**. All pre-commit
checks passed. The change is pushed for PR validation; no deployed-SHA or
production live-user acceptance is claimed.
