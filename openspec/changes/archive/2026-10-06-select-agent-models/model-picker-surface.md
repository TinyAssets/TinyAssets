# Model picker: one unpowered connector surface

September10 2026. Task3.1 implementation shape for review before public-surface
code. Continues this change, its saved-preferences contract and existing selected
member authority; no new custody/storage design or top-level MCP handle.
PLAN.md read in full before this design; no principle changes proposed.

## Existing primitive and missing composition

`read_graph target=compute` already lists owner/admin-scoped registered provider
definitions. `read_graph target=agent_bindings` lists owned bindings; existing
custom_agents bind/set_serving mutations accept model_access and check readiness.
Preferences GET/POST is already authenticated and unpowered. There is no public
normalized model catalogue read, so a browser cannot compose a working picker
from that definition list. Raw provider URLs/keys or LLM-powered workflow runs
are not a substitute for the existing credential-blind discovery boundary.

Primitive checker on September10 reports CLEAN for model_options and models;
direct inspection of read_graph confirms no corresponding branch. Extend
`read_graph` with target `model_options`, not another top-level tool or parallel
app-only catalogue route. Existing arguments and direct str return/structured
adapter remain unchanged. The app calls this read directly without converse.
The new target needs both-client rendered proof before merge like the pending
converse model_choice argument.

## Scope and invariants

Derive the actor from authenticated request identity. Resolve omitted graph_id
to its existing founder home without creating one. Require current owner/admin
access and a complete current home, as the existing preferences slice does;
foreign/non-admin/unknown resources have the same non-disclosing response.
Explicit non-home model preferences remain unsupported for now and are still a
required later capability, not silently mapped to home. The app's picker is the
current-home UI; this change does not claim all-universe control complete.

Collect current preferences/generation, owned registered definitions, deposited
native service metadata, current agent binding and assignment in bounded reads.
Do not call onboarding._platform_binding or ensure_founder_serving: those may
create/reset bindings. Use the existing read resolver for a unique serving agent;
with none, retain inventory and report no_serving_binding. Multiple serving
bindings are an explicit ambiguous state, not an arbitrary first-row choice.
Native inventory uses existing depositor/custody metadata under exact owner/home
scope and the installed provider registry, not ambient host auth. Unsupported
services remain visible with a reason, not silently labelled usable.

Reuse/refactor the existing served_model_plan collection and per-member filtering
instead of duplicating eligibility, ranking or price arithmetic. Separate
collection from its execution-only no-candidate requirement. Runtime still
requires a candidate and all fresh launch checks. Display accepts zero eligible
candidates and saved references missing from discovery. Collection never sets
serving, mutates the saved row or publishes a manifest.

Discovery of registered HTTP sources uses only their existing approved
model_discovery profile and current GET grant through refresh_model_discovery.
Accepted model membership is not required merely to READ an already-approved
catalogue; it is required for inference. Unaccepted sources/models are visible
and disabled for immediate execution with outside_accepted_model_scope. Missing
discovery grants produce a setup reason, never inferred endpoint permission.
Remote reads run outside SQLite/admission; existing single-flight/lifecycle
ownership and bounded protocol decoding remain. No LLM or arbitrary executable
discovery is introduced. Native default support is honest; explicit native
catalogue enumeration remains separately required.

Recheck current home/admin/agent/assignment before returning; a changed scope
refuses the snapshot. Recheck each discovery snapshot's source authority and
freshness; failed/revoked sources lose their model rows and get an unavailable
reason rather than publishing stale authorization. Partial provider failure
does not hide independent connected sources. No durable cache is added. A client
may retain the prior visible list after a failed refresh only if labelled stale;
it never becomes fresh merely because another provider refreshed successfully.

## Response and consent

Return a versioned, explicitly advisory envelope containing universe_id, current
preference snapshot, binding id/revision (or absent/ambiguous state), existing
accepted model_access constraints, and model_options_document output. Include
per-source observation time/expiry and fixed failure reasons. Preserve opaque
provider/model refs, unknown metadata and zero prices distinctly. Never serialize
raw credentials, account ids, custody paths/digests, grant ids, proxy URLs or
remote exception text. Candidate-catalogue membership is not authorization;
actual inference still validates exact current owner authority and cost.

Return the complete protocol-bounded catalogue, not a silent first30-model subset.
Use the existing structured result channel; text summary may be bounded while
structured data preserves choices for the app. No new arbitrary model-count cap.

Saving preferences uses the existing CAS route; per-message selection uses the
existing model_choice codec/argument. Neither grants access. For an unaccepted
source, offer an explicit access-confirmation step via existing bind_serving_provider
with exact binding revision and a complete model_access map preserving accepted
members/constraints. Never reconstruct that map from model prices or selection
order. New HTTP automatic access is free-only unless the owner explicitly approves
specific cost ceilings; choosing a paid model cannot manufacture spending authority.
Missing endpoint grants use the existing user connection request/deposit flow.
Readiness must refuse a legacy binding with an explicit saved selection that it
cannot execute; a saved automatic preference still preserves legacy availability.

The app also supplies the catalogue's universe_id as an explicit query target on
the existing preference route. The handler refuses a different current home
before touching storage; its existing transactional home fence still handles a
rebind during the write. Omitted targets preserve legacy current-home behavior.
This prevents an old dialog writing generation0 preferences into a newly rebound
generation0 home. A mismatch is not permission to act on the explicit universe.

## Interface behavior and proof

One keyboard-accessible model/provider button near the composer, separate from
speaking voice. Label actual last answer/unknown truthfully; configured choice and
next-attempt preview are separate labels. Dialog shows all choices and fixed
reasons, Automatic, current selection, Save default, and move/remove fallback
controls. Preserve an explicitly empty fallback sequence. Capture the whole
current choice at each typed/voice send; queued, restored and retried messages
keep that captured choice, never whatever the picker says later. Legacy queue
entries remain no-override. Changing settings does not rewrite prior receipts.

Proof must cover authenticated and unpowered reads, foreign identities, home
rebind/deletion and revocation races, no mutations or secret leakage, all newly
discovered models, retained unavailable defaults, no paid fallback, real native
priority, explicit order, CAS conflicts, stale refresh, keyboard flow, queue/
voice/retry capture, and real selection reaching the writer. Public MCP shape
proof is both rendered clients, then deployed SHA/canary and ordinary app use.
No enabled decorative picker ships before those mutations and runtime work.

## Pre-build review: ADAPT383s, corrections accepted

Claude75270 reviewed ab522e39, exit0 on September10 08:35UTC. AGREE on one shared
read target and existing mutation/settings surfaces. No public catalogue code
was implemented before this review. The nine required adaptations below govern
implementation; they refine the shape above rather than introduce another lane.

1. Common legacy connections must not get an empty picker. Validate the full
   legacy chain through _current_serving_authority (which checks the agent's
   provider_ref and delegates _current_bound_member_authority with member=None).
   Emit its owned native default row, distinguish legacy_single_provider from
   manifest choice authority, and keep other sources visible. No explicit override
   may masquerade as legacy authority; selecting expanded access still needs opt-in.
2. Separately enumerate owner-filtered api_key_http definitions absent from the
   accepted assignment and refresh through their existing grants/profiles. Mark
   these source_not_accepted, not outside_accepted_model_scope (the latter is an
   accepted member whose exact model-id set excludes a discovered model).
3. Carry a server-derived bind_key per source: bare definition id for HTTP,
   existing service alias for native. Model refs remain api_key_http:<id>/opaque
   native refs. The UI does not strip prefixes or reconstruct authority keys.
4. Use typed/fixed failure codes at originating checks, not a string-match over
   exception prose or one discovery_unavailable bucket. Distinguish host serving
   hold, executor absence, protocol mismatch, unenforceable price components,
   missing discovery scope and revoked source. Public messages remain fixed and
   credential-blind; runtime authority guards stay unchanged.
5. Preserve a freshly discovered catalogue even when its price contract cannot
   join the current agent's contract; mark price_contract_incompatible. Runtime
   must still exclude it. Do not lose all_models when filtering rejects a source.
6. Separate complete-snapshot owner/home/agent/assignment fences from per-source
   freshness/revocation. A changed owner scope refuses the response; a failed
   source is demoted to source_revoked/discovery_expired without hiding independent
   current sources. No expired model row remains labelled usable.
7. Do not infer unique serving state from list_bindings(limit=100). Its API clamps
   at100. Add/reuse a predicate-specific read over owner+universe+serving status,
   with enough rows to distinguish zero, exactly one and multiple, even when more
   than100 newer inactive bindings exist. Do not change the owner's binding.
8. Resolve an omitted graph id using get_founder_home plus home completeness,
   never _request_universe's public-universe fallback. No home is no_home_universe;
   an explicit id must equal that current home. No bootstrap through a read.
9. Make readiness refuse legacy plus explicit saved preference rather than claim
   enabled then refuse every turn. Preserve saved-auto legacy usability. The
   refusal belongs in existing serving readiness, not just a UI disabled button.

Non-gating follow-ups: sync discovery lacks async single-flight; catalogue reads
must not claim bounded de-duplication that the chosen ingress does not provide.
The read_graph openWorldHint metadata must acknowledge authenticated external
reads while preserving readOnly/idempotent facts. Filter registered definitions
by actual owner, not admin ACL alone. ModelAccess.document is suitable owner-only
constraint data; snapshot projection includes observation/expiry/warnings only,
never custody/grant details. Actual implementation still needs independent review.

## Existing single-source setup remains identifiable

The approved API review found that legacy HTTP serving was not identified among
otherwise unaccepted sources. Add nullable legacy_source with only provider_ref,
server bind_key and configured model_id (empty for native provider default).
Populate it only after the complete legacy chain survives the final read fence;
HTTP model_id comes from its owner-filtered immutable registered definition.
It is configuration, not a reported answering model, catalogue freshness, or
permission for a new explicit choice. Do not manufacture a candidate or price.
This supports explicit access setup while preserving the prior source; invalid
or revoked legacy authority leaves the field null. No write or schema migration.
