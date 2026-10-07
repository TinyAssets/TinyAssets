# Picker ingress: verified remaining seams

September10 2026, local source inspection. This records unfinished task3.1, not
an approved new public endpoint or a deployed control. Existing design.md remains
the governing user behavior; no PLAN principle is changed.

## Reuse what exists

- `onboarding/model_preferences.py` already exposes authenticated current-home
  GET/POST `/mcp/app/models/preferences`. Its approved contract forbids provider
  probing, home creation or inference during settings access. Keep it unpowered.
- `api/compute_connection.py:read_compute_providers` lists owner/admin-scoped
  registered definitions. Registration is candidate inventory, not an assignment,
  availability proof or authority to use every discovered model.
- The existing `custom_agents` bind action now accepts explicit `model_access`;
  `set_serving` performs real readiness. Reuse these canonical mutations for an
  owner-approved opt-in, rather than introducing another grant/credential store.
- `providers/served_model_plan.py` owns current accepted-member collection and
  per-member capability/cost filtering. Its final no-candidate exception is right
  for execution but wrong for a repair/settings screen. Separate display
  collection from the requirement to launch, preserving the same ownership,
  home/assignment/custody fences and discovery-outside-transaction invariant.
- `onboarding/serving.py:_platform_binding` is NOT a read helper: it can create
  or reset a binding. A catalogue read must not invoke it to find the agent.
  `provider_serving_binding.resolve_serving_agent_binding` is an existing read
  seam; an unpowered/no-serving state still needs a non-mutating inventory path.

## Internal projection now built

`providers/model_options.py` projects only allowlisted catalogue facts, reasons,
policy provenance and exact advisory order. It works with zero candidates,
preserves unavailable saved references and connection-level failures, and never
serializes account identifiers, owner ids, custody, grants or an actual execution
receipt. `in_candidate_catalog` is deliberately NOT called authorized/available:
fresh launch admission is separate. An eligible choice outside the user's exact
fallback sequence does not become a fallback. No network/storage/UI/API caller
is wired yet; review this helper with the actual authenticated catalogue consumer.

## Interface integration constraints

The catalogue must show all discovered choices with unavailable reasons, not just
the current plan's eligible tail. Registered-but-unaccepted sources stay visible
without automatic publication; missing discovery scope points to the existing
connection-request gesture, not an inference that authority has been granted.
Do not hide native sources behind an HTTP-only list. Explicit native discovery
remains unfinished (native-discovery-evidence.md).

`onboarding/app.html:MCP.converse` now forwards `model_choice` (03041ce8).
Typed `sendTurn`, voice `sendVoiceTurn`, queue persistence, in-flight restore and
resend capture the SAME choice at the user's send gesture. Reading a mutable
picker value when a queued message eventually dispatches would change the user's
already-submitted selection. Preserve old queue entries without a choice as
legacy/no override, not an implicit automatic migration. Saving a default is a
separate generation-checked operation, never a side effect of sending or refreshing.

The existing reply footer is observation-only; refreshed history currently
discards the footer. Keep actual response identity distinct from configured
selection. A failed refresh cannot turn an old catalogue into fresh availability.
The catalogue ingress/auth/refresh contract has a bounded shape review ADAPT383s,
with nine accepted adaptations in model-picker-surface.md. Both-client rendered pre-merge proof remains
required for the already-added optional canonical converse argument.
