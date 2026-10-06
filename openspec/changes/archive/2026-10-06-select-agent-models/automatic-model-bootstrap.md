# Automatic model connection during sign-in

September15,2026 pre-build design supplement. Owner: Codex Patches. Implements
the founder's September14 PDT direction in PLAN Providers. This is the remaining
unpowered model-selection slice, not a new workflow or competing delivery lane.
Live second-user success is not claimed. Local implementation progress follows.

## 2026-09-15 UTC release integration

Draft PR3853 opened at c9c8bb277db5b098e3d1a2fd02967723535dcd86 after final
Fable5.1 round3 APPROVE (491s). Integrating deployed reconnect main89a335578a15
required resolving only the design.md introductory supplements; both are kept.
The browser recovery catch now distinguishes no eligible free model, unusable
authorization, changed setup and incomplete saved setup, with no automatic
retry or raw upstream error display. The key-management link uses the existing
readable legal-link style; loopback Chrome visual verification passed this turn.
No credential, account consent, model grant or private workflow was changed.

Windows command: prior18-suite browser checkpoint command plus
tests/test_onboarding_serving.py =>606passed70.11s, zero skips. Core browser/
bootstrap/route subset47passed10.61s. Ruff and450-file plugin mirror/import pass.
These use synthetic network/DOM and real local authority stores, not live OAuth.
The integration changes the reviewed head: keep PR3853 draft until fresh exact-
head approval and required CI. Three Fable rounds consumed; founder approval
for one additional review requested, not yet received or dispatched. No deploy.
Original live app refreshed05:03UTC: no newer reply than21:50PDT, confirming
reconnect preservation; six smoke PASS on89a335578a15 do not prove this bootstrap.

First integration CI found stale generated app provenance in invariants34931465623
and preview-security34931465638. Regenerated with the canonical
python WebSite/brand/render_marks.py: only generated-assets.json hash changed;
brand-parity Node tests and all pre-commit invariants now pass locally. Scope
guard34931463291 correctly refuses the missing new exact-head review receipt.
No gate/quarantine/workflow was weakened to pass. Fresh CI remains required.

## 2026-09-15 04:44UTC browser integration checkpoint

The hosted web browser now routes on setup, handles the distinct model callback
before WorkOS, strips code immediately, and starts same-tab authorization only
after an explicit sign-in to an empty universe. Native, reload/silent refresh,
recovery, cancellation and unavailable status do not auto-start. Tab-scoped PKCE
state is one-attempt; uncertain exchanges offer explicit resume/key management.
The return screen renders the existing free-only request and calls the existing
owner answer path only on an approval/decline click. Connected state is reread,
not inferred from credential deposit. The ordinary manual form stays generic;
the authorized OpenRouter signup preset is a separate entry. Once serving,
sys_connect_llm remains collapsed/nonblocking as Connect another LLM.

Resume now redisplays a matching existing owner/free-only request after partial
activation without redepositing/rotating its custody or creating new consent.
Real-store tests prove owner approval reaches serving and interruption after
publication can resume/enable through the same request. Anonymous ingress,
fresh-owner home provisioning, callback headers/no mutation and browser
cancel/expiry/foreign-state/no-loop behavior are covered. Tests originally ran
with engine MCP tools dark; enabling the production feature in the fixture
allows HTTP agent eligibility without changing runtime policy.

Windows verification (this worktree, September15): the prior421-test command
plus test_app_hosted_model_connect.py, test_app_model_picker.py,
test_app_request_rail_executes.py, test_pending_requests.py,
test_pending_requests_power.py and test_request_rail_honest_asks.py returns
566passed54.98s/no skips. Ruff and450-file mirror/import pass. Synthetic browser
DOM, hosted exchange and catalogue; real state/authority/enable stores.
No actual OpenRouter account/consent/key/answer proof. No workflow was edited.
Final Fable5.1 round3 remains required before push/PR/deploy; not deployed.
Follow with local visual check, CI and rendered separate free-user onboarding.

## 2026-09-15 04:23UTC backend composition checkpoint

The authenticated begin/exchange/resume endpoints and exact public callback shell
are now implemented locally. Begin validates canonical same-origin JSON and
challenge before provisioning the current owner's home. Exchange binds one flow,
rechecks home/admin/empty setup before network and again before deposit, checks
the frozen preset digest, and never returns the key. Callback GET only serves
the no-store/no-referrer shell; invalid handles/deeper paths stay challenged.

The composer uses existing connect_http, candidate registration, discovery,
first-agent preparation and request_from_user. It derives an actual eligible
free tool-model seed from the granted catalogue, using the existing free-model
policy, not a bundled release or router alias. Discovery's exact granted GET is
factored for pre-registration use; the verified-definition wrapper remains.
No serving assignment is created until the owner answers the existing request.
Partial catalogue failures resume from the owner's existing vault deposit;
foreign/deleted-home/changed-source attempts hold. Existing custom discovery
metadata is not overwritten. No separate secret or approval store was added.

Verification: Windows automatic-model-bootstrap worktree, command
`python -m pytest -q tests/test_onboarding_model_connect.py tests/test_model_bootstrap.py tests/test_model_bootstrap_candidate.py tests/test_discovery_http.py tests/test_discovery_snapshot.py tests/test_discovery_exact_numbers.py tests/test_onboarding_app.py tests/test_onboarding_auth_boundary.py tests/test_hosted_model_auth.py tests/test_model_bootstrap_binding.py tests/test_onboarding_model_setup.py tests/test_onboarding_openai_device.py`
=>421passed32.43s, no skips. Ruff,450-file plugin mirror/import, diff check,
OpenSpec validation and lane admission pass. Synthetic exchange/catalogue,
real vault/connection/definition/binding/pending-request stores; no live keys.
Integration exposed and corrected the existing action's bare definition-id
input convention; its capture still owns normalized manifest identities.

NOT READY TO DEPLOY: frontend has not yet replaced engine_connected routing,
handled this callback before WorkOS, implemented same-tab once-per-sign-in
navigation/resume/cancel, or rendered the free-model confirmation and optional
generic additional-source rail. Preserve one return confirmation, no auto-answer.
Also test fresh-home bootstrap, anonymous ingress/callback headers, grant-only
partial failures, no-model recovery, and actual approval/enable continuation.
Full Fable round3 review remains after integration, then CI/deploy and separate
free-user browser proof. No peer currently active for bootstrap; no push/PR.

## September15 04:03UTC implementation checkpoint

Setup projection is committed at b334127d. The next local component adds bundled
acquisition metadata next to discovery contracts and a generic pkce_user_key_v1
transport. It binds expiring single-attempt flows to owner/home/preset digest and
S256 challenge, constructs a separate canonical HTTPS callback path, bounds the
exchange deadline/body/key, refuses redirects, and reports uncertain outcomes
without upstream secrets. No public route invokes it yet. Tests include an
alternate source using the same protocol, not only the OpenRouter preset.

An inert first-agent helper composes existing home/admin and binding primitives.
It resumes only an untouched canonical configured binding; custom, edited,
foreign, ambiguous or already-assigned agents are held, never reset or enabled.
This component does not yet deposit keys, register models or raise model consent.

Windows verification in automatic-model-bootstrap worktree:
`python -m pytest -q tests/test_hosted_model_auth.py tests/test_model_bootstrap_binding.py tests/test_onboarding_model_setup.py tests/test_onboarding_openai_device.py tests/test_onboarding_app.py tests/test_onboarding_auth_boundary.py`
=>217passed25.16s, no skips. This includes44 new transport/binding cases; the
unchanged prior173-case projection/onboarding surface remains green. Ruff and
447-file plugin mirror/import probe pass. No Linux or live OAuth proof claimed.

Still required: authenticated begin/exchange/resume composition with fresh
home/admin/empty-state fences; exact grant/vault/candidate/discovery chain;
return callback before WorkOS, routing/confirmation and optional generic rail;
real separate-user flow. Candidate registration currently requires a nonempty
model before discovery: derive a real eligible seed from the granted catalogue
without a hardcoded release or legacy fallback, then retain discovered/free-only
execution authority. Final round3 Fable review gates the completed integration,
not these unexposed components. No push, PR, signup, consent or credential change.

## Outcome and current seams

TinyAssets sign-in resolves the person's own universe, then automatically opens
OpenRouter's hosted signup/sign-in/authorization when it has no LLM connection.
Returning completes a free-only model setup without an already-running LLM.
Afterward a nonblocking generic Connect another LLM request remains available.
Cancellation offers resume/another source/skip, never repeated forced redirects.
An expired/exhausted/failed existing connection goes to recovery, not bootstrap.

Source inspection September15: onboarding/__init__.py::_handle_me exposes one
engine_connected boolean; app.html::enterSignedIn treats false and unavailable
as the same Connect screen. pending_requests.py synthesizes sys_connect_llm
only while serving authority is unavailable. Neither proves absence of stored
connections. Existing deterministic connect_http, connect_compute and
configure_provider_capability(model_discovery) need no powered agent. Existing
model_access_requests.capture_action/execute_action provide owner/home/revision/
assignment fences but require a configured agent binding. Existing source
contract preset openrouter_user_models_v1 provides discovery, pricing and caps.
The arbitrary paste/inference path requires a powered agent and is not bootstrap.

## Proposed implementation boundary (requires Fable review before code)

Fable5.1 round2 process31465 completed386s ADAPT. Lead disposition accepts:
replace enterSignedIn's routing key (not just append data), distinct callback
path before WorkOS code handling, safe non-serving first-binding helper, explicit
home recheck at exchange and existing owner-capable request_from_user. The prior
claim that raising model consent requires an agent was wrong; no new raiser.
Review artifact: docs/reviews/2026-09-15-automatic-model-bootstrap-fable.md.
Implement web-first; native deep-link return stays explicitly unverified rather
than inheriting the web success claim. Exact implementation review remains ahead.

1. Add a strict owner-scoped setup projection to the authenticated app status.
   Separate empty, existing/recovery, connected, and unavailable observations.
   Read failures are unavailable, never empty. Inspect this owner's home only:
   assignment (including failed/pending), native deposit metadata, owned compute
   definitions and connection records, rather than live health or host quota.
   Unrelated HTTP channels do not count as LLMs. Ambiguous provider artifacts
   hold for recovery rather than invite destructive first setup. GET does not
   provision a home or start auth; a bounded authenticated POST does.
2. Generic hosted-authorization transport with an OpenRouter bootstrap preset.
   Define acquisition metadata alongside source-contract/onboarding data: display
   name, acquisition/help URL, fixed authorization/exchange endpoints, protocol
   (PKCE code-to-user-key), selected inference/catalogue/benchmark scopes and
   source-contract version. No hardcoded model name, host-name sniffing of pasted
   keys, prompt-based interpretation, or changes to native provider executors.
   Do not claim arbitrary OAuth/CLI protocol compatibility. Later sources may
   supply reviewed contract data for the supported protocol. Remote model rows
   cannot supply new auth/redirect/endpoints. No dynamic callback origin from a
   request Host header; use the configured canonical HTTPS app origin.
3. The automatic redirect is navigation, not silent user consent. TinyAssets
   discloses that connecting powers this universe using eligible free models and
   their provider's privacy/limits; OpenRouter handles signup/login/its consent.
   After return, present the existing owner model-access confirmation if there
   is no already-recorded explicit consent for that exact setup. Do not pretend
   OpenRouter consent alone approves arbitrary TinyAssets workflow authority.
   Make this one coherent onboarding journey, not a manual key form. No extra
   paid plan, credit purchase, API-key paste or running agent prerequisite.
4. App POST begin resolves/provisions the authenticated owner's current home,
   rechecks empty setup and freezes preset/version, home and empty-assignment
   baseline. Use S256, unpredictable correlation nonce and PKCE verifier; bind
   challenge to owner+home+flow. Follow existing bounded expiring one-shot flow
   leasing semantics in onboarding/openai_device.py, without treating its OpenAI
   grant as authority for this flow. Independent namespace/protocol tag prevents
   cross-flow redemption. Callback must be fixed, same-origin and correlation-
   checked; merely GETting it never deposits or enables anything. Strip code
   from the address bar before loading external resources. Referrer no-referrer,
   Cache-Control no-store; never log code, verifier, key or raw upstream response.
5. Authenticated exchange requires same owner/current home/admin, matching flow
   and verifier, unexpired lease and unchanged empty baseline before network or
   writes. Do not follow exchange redirects; strict bounded response/deadline.
   Key goes server-to-existing vault via connect_http, never chat/browser JSON.
   Exact preset endpoints only: inference POST plus necessary discovery GET;
   no wildcard egress, broad credential scope, implicit purchase or maintainer key.
   Register candidate and configure discovery through existing deterministic
   primitives. No legacy serveOn path that can enable paid-capable defaults.
6. Ensure an unambiguous owner-approved first agent binding without resetting
   custom content or enabling it. Capture existing bind_model_access action with
   discovered scope, cost_caps=None and this sole selected provider. Reuse the
   pending-request owner answer path and publication/enable fences. Automatic
   ranking/fallback is limited to fresh eligible free models; changed/nonzero/
   unknown pricing remains ineligible at launch. No model is available is an
   honest limited/setup state, not permission to buy credits or borrow capacity.
7. Durable state stays in existing vault/connection/definition/discovery/pending
   request/assignment stores. The short-lived OAuth flow may expire on restart;
   restart before exchange is an explicit resumable retry, not a redirect loop.
   Persisted partial setup resumes through its captured owner request and
   deterministic IDs, not code reuse or a second credential store. An ambiguous
   exchange failure reports uncertainty and asks user to restart authorization;
   it never claims no key was minted. A callback replay cannot duplicate grants
   or overwrite a newer connection. Cross-tab connection/home changes hold.
8. Auto-begin once per explicit sign-in attempt after an empty result. A reload,
   heartbeat, expired access-token refresh, back button or cancelled callback
   cannot begin repeatedly. Retry requires the visible Resume connection action.
   Existing signed-in empty accounts receive the same resumable setup entry.
   After connection, render sys_connect_llm as optional Connect another LLM;
   before connection/recovery keep honest blocking guidance. This is platform
   UI, not an instruction that the user's agent maintain a bootstrap workflow.

## Verification and rollout

Before implementation: Fable research/architecture review this supplement and
the actual named seams. Existing unpowered review is research round1, not approval
of this newer automatic-OAuth design. No new top-level MCP tool is proposed.

Tests: new/existing OpenRouter account journey (vendor UI belongs to user);
no-account user can reach authorization before any LLM call; zero network until
proper authenticated begin; separate owner/home and cross-tab races; no shared
credentials; empty vs revoked/expired/unavailable; cancel/back/refresh/restart;
CSRF/state/verifier/expiry/replay/duplicate exchange; bounded malformed upstream;
partial deposit/register/discovery/bind/enable; custom binding preservation;
free-only price drift, rate exhaustion and actual model/fallback receipt.

Production proof requires independent exact-head review, CI, protected deployed
SHA and public canary, then a rendered free-only second-user connection and real
answer surviving refresh. User handles credential/account terms/consent actions.
Original subscription account is neither credential source nor proof for this
user. No private workflow edits; original checklist retest remains a separate
regression signal. Keep bootstrap resumable if deployment rolls back.

## External sources (verified September15,2026)

- https://openrouter.ai/docs/guides/overview/auth/oauth documents S256 auth,
  login/authorization, code return and POST /api/v1/auth/keys user-controlled key.
- https://openrouter.ai/auth unauthenticated fetch redirects to sign-up with a
  return-to-auth URL; not proof our integrated callback already works.
- https://openrouter.ai/docs/faq documents limited free-model availability.
  OAuth consent does not itself enforce the universe's free-only cost policy.
