# Finish the picker's model-access setup

September10 2026. Continues task3.1 and the already-reviewed
model-picker-surface.md contract. This is the exact browser composition to review
before enabling access mutation. No new MCP handle, storage or credential flow.

## Existing boundaries

The picker reads server bind_key, complete accepted_model_access, exact binding
id/revision and current home. Current/saved selection is never authorization.
Existing write_graph target=agent_binding operation=bind_serving_provider accepts
provider plus a complete model_access map, fenced by owner and binding revision.
Its publication is not equivalent to a successful model call. The explicit owner
confirmation includes reconnecting the agent: after bind returns its new binding
revision, call set_serving enabled=true with that exact revision. Never claim a
model ran from either response. A bind followed by failed readiness is partial
success, not a working connection.

## Explicit owner action

For an already manifest-backed source, offer "Allow discovered models" only when
its membership is absent, legacy, or an explicit subset. HTTP additions default
to model_scope=discovered, model_ids=[], cost_caps=null (free only). For an
existing accepted source, preserve its exact cost_caps instead of resetting or
widening them. Native additions allow only the exposed provider-default reference
until explicit native discovery is actually implemented. No guessed model IDs.

The confirmation names the source, the model-scope expansion and the exact cost
constraint (free-only or existing accepted ceilings). Copy the full existing map;
change only the confirmed source key from the server. Keep other constraints and
members byte-equivalent. Use explicit graph_id, agent_binding_id, expected_revision
and the same server bind_key as provider. No automatic retry of a write with an
uncertain result. Refresh and require the owner to review any conflict or changed
scope before retrying. Cancel causes no write and no preference change.

For legacy_single_provider, only offer the initial conversion on legacy_source,
not on another connection: first establish model access on the existing source,
then add alternatives. Native conversion is explicit provider-default only.
HTTP conversion explicitly says only compatible free models will be eligible;
an old paid/fixed alias is not silently accepted or given a fabricated ceiling.
Count rows whose fresh compatibility/cost checks pass after removing only the
model-membership refusal being explicitly changed. Show that count and refuse
conversion when it is zero. This is advisory; final readiness still checks live
authority. If re-enable fails after legacy conversion, offer an explicitly clicked
restore of the same legacy provider (bind without model_access, then set_serving
with the returned revision). Never silently restore or retry uncertain writes.
The owner can cancel and retain their existing legacy behavior. No current
choice/default/fallback is rewritten by access setup.

With no valid legacy source or serving binding, show existing connection/setup
guidance rather than pretending an access expansion repaired missing authority.
This does not complete the separate fully-unpowered/general-provider setup work.

## Proof required

Review ADAPT331s accepted these three corrections before implementation: explicit
re-enable, positive eligible-count precheck plus legacy recovery, and server-derived
access_method in sources and legacy_source (api_key_http/subscription_cli/null for
unknown). The browser does not infer the access method or bind key from a prefix.
It does not string-match write error prose. Existing revoked members are retained;
their separately confirmed removal is not silently folded into an expansion.

Actual browser action sends the exact existing shared write shape; cancellation,
scope/revision conflict, ambiguous response and late signout do not retry or
change preferences. Complete accepted maps preserve paid ceilings and unrelated
members. No source prefix guessing, paid opt-in, model-release list or operator
workflow edit. After confirmed publication refresh the shared catalogue; only its
fresh eligible choices become selectable. Prove legacy native bootstrap and
manifest HTTP addition through the real backend seams with synthetic wires, plus
rendered UI. This is not live acceptance until normal app use confirms it.
