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
| `/app/approvals/answer` | Exact-origin JSON + live matching-owner cookie supplies server-side proof |
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
retain bearer coverage.

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

Claude's read-only review of draft PR #4483 returned ADAPT with no floor/security
bypass. AGREE: centralized early refusal, immutable pin lookup, OAuth completion
checks, exact-origin matching-owner proof, unmute/withdraw non-authority and
unchanged bound HTTP scopes. No colliding lane was found.

AGREE with the review's DISAGREE_EVIDENCE correctness finding: routing app answers
through `InlineApprovals.post` turned structured server errors into exceptions,
making pending/retry guidance and rejected-grant relay unreachable. The answer
transport now returns non-authentication error bodies to its existing consumers;
missing owner proof still throws a sign-in error. Five new real-browser cases
cover structured pending/invalid responses and missing proof on answer/preview/
decide. The affected browser/onboarding suites are rerun on Windows and Linux.

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
The one required cross-family round is complete; its sole correctness finding
is addressed, with no existing assertions removed or weakened.
