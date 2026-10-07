# Served model setup: reviewed next slice

September14 2026. Existing select-agent-models intent; platform enablement only.
PR3843 is deployed as005b01df; six app smoke checks pass. This document is a pre-code
public-surface/authority proposal, not a shipped specification or implementation.
Fable5.1 shape review: docs/reviews/2026-09-14-model-setup-tools-shape-fable.md.

## Outcome

The universe agent can inspect available models, save the owner's preferred
selection/order, configure discovery on an already authorized connection, and
request a genuine additional model-access grant in the app. It does not need
the operator to edit private workflows, bindings, keys or developer-console state.

## Surface and existing seams

| Operation | Reuse | Boundary |
|---|---|---|
| read_graph model_options | api/model_options.read_model_options | Pinned current home, owner/admin; served admission ticket for outbound discovery; third-party strings untrusted. |
| read_graph agent_bindings / agent_binding | api/custom_agents list/get | Pinned universe plus SQL universe/id scope and ACL; no global agent-definition forwarding. |
| write_graph model_preferences save | parse_preference_write and ModelPreferenceStore.save | Expected generation, authenticated actor/current home, require_current_home=True; no assignment/custody/grant mutation. |
| write_graph connection configure_provider_capability | api/provider_capability model_discovery branch | Vetted bound engine, existing owned definition/grant; only model_discovery, not a generic connection/voice forwarder. |
| connect_compute | Existing served operation | Registration is candidate-only, no inference/spending grant; do not add another registration tool. |
| pending_request ask with bind_model_access action | Existing person-driven request rail and provider binding publication | Validate the exact access proposal on creation; only authenticated owner answer can authorize it; served answer remains refused. |

No new top-level MCP tool and no provider-name table. API storage stays canonical;
avoid a second preferences parser or a new private model-policy store. Use the
same boundary for browser and connector ingress where extraction is necessary.
One-turn choice remains request-local; saving defaults is a separate operation.

## Admission and consent

Selection is not authorization. Saved model references may become unavailable;
the inference boundary must still revalidate current accepted membership, custody,
price limits and capabilities. A preferences receipt must not claim execution.
Discovery metadata likewise does not add endpoints or grant paid inference.

The proposed typed ask carries binding id, expected revision, root provider and
the complete reviewed model_access map. The person must see a deterministic
description of the actual permission change, not merely agent-authored prose.
The ask cannot answer itself through fields, a replayed transcript or tool calls.
Validate foreign binding/home/ownership at raise time and again at answer time.
Preserve other accepted providers and ceilings; do not turn a partial map into an
implicit revocation, broaden an explicit model list or invent catalogue entries.

Before code, resolve the exact existing bind/set_serving sequence: no automatic
enablement hidden in an access-only request, no success receipt when a second
stage refuses, and no stale retry silently publishing a second assignment.
Keep the existing rail semantics: downstream refusal leaves the request pending
and returns the error, rather than falsely recording answered. This was verified
for extend_http in api/pending_requests.py; the new action must prove its own
equivalent behavior. Do not create a new failure-state schema just for this action.

## Verification to implement

September14 sequence review is preserved in
docs/reviews/2026-09-14-model-consent-sequence-fable.md (ADAPT,341seconds).
Confirmed in source: bind increments R and resets configured; enable changes
status only, so completion is R+1/serving, not R+2. The universe assignment can
change through a different binding without advancing this agent's revision.
Add an optional internal expected_assignment_digest fence to set_serving,
checked before discovery and again inside the existing write transaction.
An omitted fence preserves current callers. This is a prerequisite, not a new
served binding mutation or a completed consent flow.

The final action must explicitly disclose reconnect and partial failure. Pending
and failed publications share the next generation with ready, so retry cannot
infer success from revision/generation alone. The review's failed-retry example
publishes G+2, while its B/C classifier only accepts G+1; reconcile this before
implementing the classifier, and test a second failure rather than copying the
table mechanically. Preserve membership/caps; no unrecorded auto-grant or restore.

Classifier disposition: capture original digest and generation as well as R.
Untouched state must still match that digest. Exact proposed pending/failed
membership may be retried with R unchanged and generation greater than G;
bind fences that newly observed digest before publishing. Bound/done states
require R+1, ready, generation greater than G, exact proposed root/membership,
owner and matching provider_ref. This permits repeated failed publications
without pretending every retry remains G+1. An already matching original ready
assignment can reconnect without rebinding. Reconnect pins its observed digest
and current home inside its transaction. Request resolution failure stays an
error; a subsequent matching serving-state answer resolves without re-publishing.
The access-only operation preserves every existing member's cost caps and every
other member's scope. It can change only the nominated source's model scope or
add a free-only source. Spending-limit changes remain outside this action.

- Non-home pinned universe cannot read home model state or save preferences.
- Foreign binding ids never read or create a pending access request.
- Stale preference generation returns conflict/current snapshot without overwrite.
- Stale binding revision refuses consent execution without changing assignment or
  falsely resolving the request; retry/partial-stage behavior is explicit.
- Preference saves and discovery setup leave grant endpoints/scopes and assignment
  digest unchanged. Other providers and caps survive model-access setup.
- Pending owner consent remains visible; served answer and prefilled self-consent
  are refused. No inference through unaccepted membership.
- Existing connection registration becomes visible in model_options as unaccepted.
- Discovery outside granted endpoints makes no request and never widens access.
- Repeated catalogue reads obey served admission and envelope remote strings.
- Same behavior through shared native/HTTP tool dispatch, canonical adapters and
  owner UI; final proof is a refreshed rendered app conversation after deployment.

## Review dispositions and remaining scope

Accept Fable's three pinned reads, two non-authorizing writes and separate typed
owner-consent boundary. Do not expose broad agent_binding mutations under the
bound founder identity. The reviewer correctly separates catalogue/account proof
from source exposure. One wording correction: lack of native enumeration does
not necessarily mean default-only selection; PR3843 also supports explicitly
entered accepted model identifiers. It still does not establish a complete list.

This slice does not prove native account enumeration, Claude metadata support,
OpenRouter account readiness, actual selected answering-model receipts or whole
workflow capability. Those remain full-goal requirements. No app-owned harness/UI
proposal or background-self project is taken over.
