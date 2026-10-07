# Candidate authority integration: verified constraints and decision gate

September9, source checkout939931a9ff0516e28b7d64ad340cafd023f57401.
The dated inventory below records the pre-build investigation, not current stage.
Shared validation and launch accounting are now independently APPROVED; manifest
storage/publication are implemented with the review's ordering bug corrected.
Discovery transport and profile publication are independently APPROVED; fresh
snapshots are locally tested. Selected HTTP text execution now integrates fresh
member/model/cost validation and budget projection, with local regression tests.
All selection work remains undeployed; unselected v2 readiness, full-agent tools,
CLI/local selection, async ingress and UI are unfinished. Current details:
connection-model-authority.md, discovery-profile.md and their proof/review files.

## Reverified constraints

- `provider_serving_binding.py::bind_serving_provider` creates a provider-specific
  serving binding, publishes a deny-all pending assignment, adopts current custody,
  and updates one agent binding's provider_ref and one ready ProviderAssignment.
  Rebinding per fallback would mutate durable serving intent and its generation.
- `provider_assignment.py::authorize_served_provider_call` validates a minted
  authenticated request carrier, exact owner/agent revision, ready assignment,
  provider_ref, binding digest and live custody under admission lock. Its current
  result is exactly one provider. A list of names cannot bypass these checks.
- `resolve_current_serving_provider_authority` is the canonical source for the
  unpowered request predicate too. A separate UI-only definition of readiness
  would recreate contradictory powered/unpowered behavior.
- `RequesterProviderEnrollmentResolver` reads explicit deployment enrollment from
  environment. It is not the self-service owner-selection seam; do not require
  operators to edit enrollment configuration for each model or future provider.
- HTTP ProviderDefinition identity includes its requested model. Subscription
  definition resolution ignores that field and constructs the native provider.
  Dynamic choices must not accidentally activate legacy ignored CLI pins, or
  mutate the shared process-global provider objects.

## Proposed direction to review, not implemented

Keep versioned owner/universe preference separate from authority. Extend the
existing serving-assignment chain to an accepted candidate set, with child
candidate records referencing the existing provider-work bindings and exact
custody identities. Reuse request-carrier validation, role/operation checks,
admission locking and per-candidate binding/custody validation. Do not introduce
a bypass through arbitrary dataclasses or treat a preference as a grant.

The manifest must bind owner, universe, agent anchor, accepted candidate
identities and allowed cost/capability constraints. Preference order and its
generation are captured separately; a reorder is not authority revocation. Legacy
single-provider assignments remain explicit, with no new fallback. New API
models may share a connection but never inherit a broader endpoint grant.

Open exact decisions: canonical manifest placement and digest, relationship to
the existing primary ProviderAssignment and provider_ref, publication transaction,
per-attempt resolution when the primary is revoked, and generation capture versus
in-flight calls. A revoked primary must not disable independently authorized
accepted alternatives; stale candidate metadata must not resurrect authority.
These are engineering decisions for independent shape review before schema/API
or serving-authority implementation, not questions for the user to solve.

## Independent review result

Claude returned ADAPT (exit0,284s); full proposal, source citations and remaining
five pre-build blockers are in
`docs/reviews/2026-09-09-model-candidate-authority-review.md`. Reuse the existing
provider-work and custody stores, add candidate membership to the serving
assignment, and factor one shared validator rather than copy the chain again.
The existing per-request invocation limit is2 and must be resolved for a longer
accepted sequence without creating an arbitrary two-model restriction. This is
not yet schema/authority approval; no such implementation has been started.

## Source inventory after review (September 9, fc617195)

`authorize_served_provider_call` now delegates assignment/binding/custody checks
to `_current_serving_authority`; explicit dispatch base_path is preserved. The
carrier, trusted request source, operation/role and exact agent status/revision
remain required before delegation. Differential tests preserve legacy behavior.

The review's v1 read-digest premise was incorrect: `_assignment_from_row` already
recomputes the assignment digest for both loaders. Future v2 reads must add the
manifest and child-row checks without weakening this existing validation.

Mutation paths found by `rg` across canonical tinyassets Python sources:

- `provider_serving_binding._assignment` is the only assignment constructor
  outside the loader. `bind_serving_provider` publishes pending then ready;
  `_write_failed_assignment` publishes failed, preserving deny-all pending if
  recovery itself fails. No current production writer publishes unassigned;
  that state is accepted by the schema/config but is not an extra hidden path.
- `custom_agents.update_binding` increments the agent revision under exclusive
  assignment admission. Provider-ref changes and `set_serving` also invalidate
  the exact carrier revision. Disabling serving does not delete the assignment,
  but execution checks serving status before considering any candidate.
- `ConnectionLedger.revoke_grant`, `revoke_connection`, and `delete_connection`
  invalidate HTTP custody through live grant resolution, not an assignment-row
  rewrite. Child rows must never override that resolution. New-generation or
  non-ready root assignments make all older children inert; no destructive
  cleanup is needed to establish denial.

Attempt accounting is not yet launch-only: router consumes the request invocation
before budget reservation and provider-slot admission. An authorize refusal does
not consume it, but a later pre-launch budget/slot refusal currently can. The
future candidate chain must charge only at the validated launch boundary, retain
aggregate token/cost/concurrency bounds and never refresh a carrier to reset its
count. A longer accepted chain must not be limited to two models. Exact finite
per-turn launch allowance and no-launch reservation release are still build gates.

The candidate manifest is an accepted authority set, not a saved preference order.
The root's legacy provider_ref anchor must not be confused with whichever member
the policy currently ranks first. Choosing or reordering an already-authorized
member must not create new custody or broaden cost/privacy permission. Dynamic
models within an already-approved connection need this same distinction; manual
provider-definition publication cannot be the final discovery experience.
