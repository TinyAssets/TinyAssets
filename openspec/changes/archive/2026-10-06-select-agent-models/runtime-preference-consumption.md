# Consume preferences at the real conversational boundary

September10 2026, next integration of tasks2.1/2.2/2.4. Existing change and
approved policy/authority stores, not a new proposal or claim of activation.

## Original verified gaps, now integrated locally

The original public converse lacked selection, its sink produced no plan, and
set_serving rejected every manifest. September10 integration now joins canonical
converse, stored/current policy, fresh member catalogue and real readiness, with
public binding opt-in. New acceptance cases do not bypass the readiness gate.
See docs/reviews/2026-09-10-preference-consumption-proof.md:765 Windows/767 actual
Linux passes (3/1skips). Independent review ADAPT454s found a saved-automatic
compatibility regression on legacy bindings; corrected tree now passes793 Windows/
795 Linux (3/1skips), exact5167596c correction independently APPROVE208s.
Not deployed or UI-complete.

Originally neither public binding caller passed model_access. The authenticated
custom_agents caller now passes strict optional declarations; onboarding/serving.py
still preserves legacy behavior. The implementation extends the
existing owner binding flow with strictly parsed optional model-access declarations,
using ModelAccess validation and existing assignment publication. Absence preserves
legacy semantics. New connections opting into automatic selection accept discovered
models under explicit/free-only bounds; existing bindings are not migrated merely
because preferences were saved. Broader discovery endpoints or spending remain
ordinary explicit owner approvals through existing request/grant mechanisms.
First opt-in entrypoint: the authenticated custom_agents binding action. The
deposit/onboarding route stays legacy until its own explicit opt-in gesture exists.

## One request plan, not two competing choices

Add optional model_choice to canonical converse and its internal sink, carrying
the existing versioned ModelPreferences document. None means use saved settings;
an explicit document replaces the entire current order for this turn only.
Automatic explicitly clears the saved primary for this turn. No implicit append
of the old saved default, no save side effect, no cross-tab/global current state.
Unknown fields/types and unsupported versions fail before provider work. Reuse
the existing codec rather than invent a second reference/order format.

After genuine carrier minting, capture saved preferences from the current-home
guarded store using the verified principal and universe, never body identities.
Apply the current-home guard only for home-scoped preferences. Other authorized
owned-universe turns without an override retain their legacy path, with no home-only
preference read. Explicit overrides outside supported home scope refuse, rather
than being silently ignored. General non-home policy/journal support remains a
later required capability before exposing those controls there.
Capture generation once. Missing row and missing override preserve an existing
legacy binding exactly. A newly opted-in manifest instead uses automatic
generation0, as design.md requires for new opt-ins; this does not publish a
manifest implicitly. A corrupt/unavailable row is held, not treated as absent.
Saved automatic mode without a current override preserves an existing legacy
provider's own default; the saved row/generation remains intact and publishes no
manifest. This keeps unpowered settings followed by a legacy subscription connect
usable. Current overrides and explicit saved policy still cannot expand authority:
report that its accepted model scope needs updating through the existing binding
flow. No implicit publication or grant extension. Saving preferences alone still
does not alter assignments or credentials.

Build the plan from current accepted assignment members. Reuse _served_request_agent
and _current_selected_member_authority for owner, agent revision, current home,
accepted binding, custody and grant checks. No SQLite transaction or assignment
lock spans discovery IO. Re-read the same authoritative chain after IO, and the
router still validates it before EVERY launch. The captured plan is advisory.
The sink sets model_selection from this plan's first candidate; the runner rejects
a contradictory plan/selection pair rather than silently overriding it.
This rejection is now implemented locally in InteractiveHttpAgentTurn._run;
canonical plan production is now implemented locally, not yet deployed.
For overrides construct ModelPolicy using override.mode, current_selection equal
to its primary, saved_default=None, and exactly its fallbacks. Automatic clears
both primaries. Generation is the observed saved generation (zero when absent).
Saved mode uses its saved_default and tail with current_selection=None. Record
provenance separately: extend the versioned journal header with current/saved/
automatic source, retaining a strict v1 reader with source unknown. No rewrite of
old headers or inference that generation alone proves which policy drove a turn.
Version2 input headers now implement this provenance locally; strict version1
reads retain unknown provenance. See the native-default-policy-provenance proof
in docs/reviews for tests and remaining activation gaps.
The pure capture_preference_policy helper now constructs this exact policy:
saved documents (including saved automatic) carry source saved; current documents
(including current automatic) carry source current. Neither mode alone implies
where the choice came from. Missing saved state requires generation0; absent
saved/current choices return None for the legacy path. Real converse now calls
this helper through the owned plan assembler; the helper grants/saves no authority.

Discovery uses each connection's existing protocol contract. Preserve fresh
capability/privacy evidence and all accepted model/price restrictions. Filter
advisory candidates per member with the existing eligibility kernel, not the
definition's legacy model string. A plan-wide price ceiling must not relax a
member's ceiling; if using a combined advisory ceiling, pre-filter each member
under its own exact bounds and revalidate actual attempts independently. Retain
unfiltered choices and exclusion reasons for the eventual UI. Ranking source is
trusted boundary configuration; never a client-selected benchmark name.

## Native default and mixed-source correctness

Do not label every non-HTTP member unavailable and silently choose OpenRouter.
The automatic path must include genuinely available accepted native/local sources
and prefer their default as required by PLAN. Native execution stays native,
without a fabricated HTTP SelectedModel or cost/context facts. An empty native
model reference means provider default; fresh explicit selections alone may set
a native per-request model argument. Do not activate previously ignored model
fields in definitions or mutate process environment/global provider objects.

Native discovery/selection needs an executor-bound contract, with unsupported
discovery honestly distinguished from provider-default execution. No core model
release lists or inferred account independence. Unknown native turn effects keep
the existing no-replay rule; do not restart a CLI turn merely to traverse a
fallback list. Mixed-source continuation is allowed only with a proved safe
inference boundary and portable known progress. HTTP-only support is an interim
implementation stage, not a substitute for the requested mixed-source behavior.
The concrete native-default path belongs in _authorize_served_provider_call and
its async wrapper: recognize the existing _PROVIDER_SERVICE registry, require
model_id="", use _current_selected_member_authority, skip HTTP prepare_selected_model,
and yield selected_model=None with the exact member provider and custody snapshot.
Native explicit IDs follow through executor discovery, not a loosened default path.
In router._request_ceiling, explicit allowed_providers (including empty) still wins;
otherwise use the resolved served provider for any served authority, not just HTTP.

The catalog assembler must produce native ConnectionModels from that registry and
verified member availability/custody: source_kind subscription (or proven local),
Model(model_id=""), default_model_id="". Preserve the Claude-serving opt-in and
do not fabricate context/model metadata. Confirmed subscription execution is
unmetered; actual model remains unknown. A native first candidate uses existing
all-skipped retry only; no native whole-turn fallback traversal in this first slice.

## Serving activation

Keep _current_serving_authority's legacy-only execution guard. Add the reviewed
multi-model readiness path in set_serving using current accepted member authority,
not a fabricated legacy assignment. At least one supported eligible candidate
under the captured policy must be usable before enabling. Discovery occurs before
the exclusive mutation transaction; recheck assignment, agent revision, preference
generation, home and custody in that transaction before set_binding_serving.
No network call under the transaction and no automatic rewrite of explicit pins.
Readiness is not launch authority: the next genuine request still revalidates.

## Verification and delivery

Use real authenticated converse -> stored policy -> accepted assignment -> serving
enable -> writer -> actual selection paths in tests; don't monkeypatch the new
resolver or manually set serving in those acceptance cases. Cover absent legacy
preferences, saved automatic, saved explicit/empty tail, one-turn override without
save, two owners, changed generation during activation, rebind/deletion/revocation,
paid-model exclusion and per-member limits. Native default must win over HTTP in
mixed automatic mode; explicit HTTP must win when the user selects it. Preserve
all old CLI tests. Windows, Linux oracle and independent review precede landing.
The optional public MCP argument requires both-client rendered pre-merge checks.
UI exposes current choice separately from saved policy only after runtime works.

Independent shape review59767: exit0 ADAPT358s. Six required corrections above:
native launch, native catalog, non-home preservation, override provenance,
contradictory-plan rejection, and delta specs for binding/converse. No duplicate
review is needed before implementing these corrections. Inventory other served
sinks before activation (slack_event remains an accepted issuer but its module is
absent here). Reconcile seven extracted release commits ahead on origin/main
before landing; do not blindly rebase dirty work. Scoped-reset inventory remains
an independent unresolved concern, not proven by home/deletion guard unit tests.
