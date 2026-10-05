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
