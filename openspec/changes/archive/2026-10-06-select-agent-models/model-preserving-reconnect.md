# Model-preserving connection recovery

Pre-build authority design, September15,2026. Existing select-agent-models
intent; this is not a new independent delivery goal or a shipped requirement.
Branch: codex/model-preserving-reconnect. Owner: current Codex Patches task.

## Evidence and intended result

The original subscription user's agent suggested the existing Claude token form
while admitting it had not verified whether submitting changes defaults. No real
credential was submitted. Four synthetic real-vault/real-binding regressions in
test_onboarding_serving.py reproduce the problem: reconnecting the root clears
the model manifest; reconnecting the other accepted subscription also replaces
the root. Both happen with unchanged or replaced synthetic credential material.
An initial missing-home test setup was corrected before this reproduction.

Connecting a credential must not silently discard this user's accepted models,
root, saved preferences or private agent/workflow content. Preserve the current
model setup when refreshing one of its accepted sources. This is not permission
to add sources, broaden model scope, grant spending, or change workflows.

## Reviewed existing-seam repair

Fable5.1 round1 (79934, terminal ADAPT) confirmed all four reproductions.
Codex disposition: accept owner-last-writer selection, all-member preflight,
open-source bind-key normalization and enable-only-after-publication. This is
already cross-family review of Codex-authored work; no redundant Codex dispatch.
One reviewer suggestion is refuted by credential_vault.py's current-custody
digest check: rotation intentionally makes current custody None. Validate the
nominated source's current owner-deposited material and existing work binding,
not equality to its old custody. Reuse the adoption validator without writes.
Other members must still have exact current authority before any pending row is
published. An unavailable member holds without destroying the ready assignment.
The shared read admission ends before publication's existing exclusive/CAS path;
this does not claim to close concurrent credential changes after preflight.
Record member removal/reconsent separately; do not shrink the accepted set.

1. Keep /mcp/app/serving/bind as the authenticated owner/current-home ingress;
   no additional top-level MCP tool, duplicate vault, model-policy store or
   model-access consent mechanism. Normalize a nominated source through the
   existing source resolver; do not branch on a compiled provider list.
2. Before _platform_binding can reset or create anything, read the current
   assignment. A ready accepted manifest selects the preserving path. Validate
   owner/current admin/current home and the nominated source's membership.
   New/unaccepted sources remain deposited candidates and require the existing
   model-access confirmation; they must not replace the manifest as legacy setup.
   Pending/failed or foreign assignments cannot be rewritten by inference.
3. Resolve the current owner-selected agent binding by its authoritative provider
   reference and existing serving selection, not an arbitrary matching definition.
   Require exactly one eligible owner binding, last updated by that owner;
   ambiguity refuses without edits. With no serving match, resume only a unique
   configured owner binding matching the accepted provider reference, filtered
   before the ambiguity bound, never a first-page inventory sample.
   Preserve its definition/configuration and any other private bindings. Do not
   invoke the platform-template reset or quiesce unrelated agent bindings here.
   Review must check the historical collaborator-confused-deputy protection:
   reconnect is not consent to adopt collaborator-created or ambiguous content.
   If current metadata cannot safely identify the selected binding, surface the
   existing owner-confirmed model setup instead of guessing or resetting it.
4. Reconstruct exactly the stored root and complete ModelAccess mapping, including
   explicit model sets, discovered/default modes and cost ceilings. Bind fresh
   custody through bind_serving_provider with expected_revision and the observed
   expected_assignment_digest. Never fall back to legacy single-source setup
   when one source is unavailable. Preflight all members before publication;
   normalize stored open-provider names back to the existing resolver's bind key.
   Preserve ModelPreferenceStore untouched. Current-home checks run in the read
   transaction and again in bind/enable with require_current_home=True.
5. Enable only that same binding through set_serving with the newly observed
   digest/current-home fence. Retain existing partial-failure behavior: saved
   credential is not a successful reconnect, and publication is not successful
   enablement. Keep the prior choices inspectable and report the precise hold.
   A replay must not replace the accepted set or advance revisions unnecessarily.
6. First-time/no-manifest setup keeps its current contract. This does not add an
   unapproved second source automatically or solve free-only first bootstrap.
   Existing explicit model-access consent remains the route for adding/changing
   models; the default model picker does not become an authority mutation.

No migration is expected. The implementation must not widen the legacy policy
retry path, auto-replay a failed turn or modify native provider credentials by
itself. The owner still performs credential entry. A successful local-custody
repair is not proof upstream authentication succeeded; actual reply proves that.

## Review questions before runtime code

- Can the current assignment/provider_ref and binding status prove one current
  selected binding without adopting collaborator edits? Preserve owner-selected
  custom definitions; do not hardcode platform-only content as the product shape.
- Does the existing publication transaction fence membership/cost/routing changes
  during credential rotation, including a revoked unrelated member? Prefer an
  honest hold to regranting it or destructively reducing the accepted set.
- If credential replacement precedes model refresh, which existing recovery
  receipt can truthfully distinguish stored credential, republished authority
  and actual provider sign-in? Avoid a new public status enum unless required.

## Acceptance / verification

Four red-first root/non-root tests are the initial proof, not complete coverage.
Add distinct-owner negative cases, unknown/new source, current-home/admin
revocation, stale assignment/binding, preserved explicit models/costs/preferences,
owner-selected custom binding and collaborator/ambiguous selection. No real
provider account, credentials, private workflow or production mutation in tests.
Independent cross-family shape then exact-head review, focused Windows/base
comparison, Linux CI and existing release gates. Deployment needs protected SHA
containment/public canary, then a refreshed ordinary app conversation. Actual
credential entry remains with the owner and cannot borrow from the other user.
Rollback preserves stored policy and uses the standard release rollback; no
credential copy, deletion or attempt to resurrect old grants.
