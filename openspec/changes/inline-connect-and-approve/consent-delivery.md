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
retain bearer coverage. Final platform counts and review disposition are recorded
below when available.

No deployment or live-user acceptance is claimed by this draft PR. The existing
change's broader spec sync/deployment acceptance stays open.
