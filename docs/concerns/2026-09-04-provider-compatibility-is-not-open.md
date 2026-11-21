---
severity: P1
title: Provider compatibility is not open
filed: '2026-09-04'
summary: compiled CLI-name restrictions and ignored connection-local model choices prevent provider portability; provider default, user-saved defaults, and all available model choices are required
---

# Provider compatibility still depends on a compiled vendor set

Founder directive, 2026-09-04 PDT (recorded 2026-09-05 UTC): users must be able
to power their own universes with providers TinyAssets has not anticipated,
including future CLIs. Provider updates must not routinely require a platform
patch. Repair platform capabilities, never private workflows to force acceptance.
The existing deploy / exact `Retest your workflow checklist` loop remains active.

Additional founder requirement, revised 2026-09-04 PDT (recorded 2026-09-05 UTC):
use the provider's default unless the user chooses otherwise. Users can save
their own default and select from all models actually available through their
own connection. This supersedes the earlier same-day request to default to
the latest available model. Removing a hardcoded platform model alone does not
complete user model selection. Explicit choices must remain explicit across
updates, and model availability must come from connection-scoped discovery,
not a maintained vendor list.

## Reverified evidence

2026-09-05 UTC, local branch `codex/provider-catalogue-compat`, inspected with
`rg -n` and source reads:

- `tinyassets/providers/definition.py`: `_CLI_PROTOCOLS` admits only
  `cli:codex` and `cli:claude-code`; `_validate` rejects unknown protocols.
  HTTP definitions likewise require one of two compiled protocol encoders.
- `tinyassets/providers/call.py`: fallback registration explicitly imports
  named vendor implementations; open-definition routing bridges into that router.
- `tinyassets/providers/provider_resolver.py`: `_cli_provider` dispatches only
  the same two exact names and rejects every other CLI ref. This is the actual
  execution restriction, not just an overly narrow registration validator.
  A generic executor can reuse this existing resolution seam and `BaseProvider`
  contract; merely allowing arbitrary names would create unusable connections.
- Follow-up inspection at 2026-09-05 01:35 UTC, local reviewed commit
  `8e210a9d`, using `rg -n` and source reads: `tinyassets/api/compute_connection.py`
  has a third compiled CLI-name check, `_CLI_REFS`, in the owner-facing
  `connect_compute` handler. Its opening claim of no allowlist is contradicted
  by its subscription branch. Extending only the definition/resolver will leave
  registration blocked. The current read surface lists registered definitions,
  not a fresh account-scoped model catalogue; no `list_models`, `model_catalog`,
  `available_models`, `default_model`, or `model_selection` implementation name
  was found under `tinyassets` (name-search evidence, not proof no equivalent
  mechanism could exist under another name).
- `ProviderDefinition.model` is already an owner-supplied connection-local
  selection field, but `_cli_provider` discards it for subscription definitions.
  `ModelConfig` has no model selector; that does not mean no selection surface
  exists. The operator-global environment knob is not a substitute for that
  connection-local seam. Do not silently activate previously ignored values
  (which may be placeholders) during the current outage patch.
- Read-only follow-up on 2026-09-05 01:48 UTC: `call.py` registers universe
  definitions into a process-global router, and `provider_resolver.py` replaces
  entries by the provider's name. `router.py` freshly resolves authorized
  `api_key_http:<definition-id>` identities but not CLI definitions by id. Thus
  simply assigning each definition's model onto a shared named CLI instance
  would create a cross-connection preference hazard. This is a design constraint
  for the future selector, not evidence that the currently ignored CLI model
  field has already leaked a user's selection. Carry the authorized connection
  and model choice per invocation, or give execution definitions isolated
  identities; never make the saved owner preference mutable process-global state.
- `openspec/specs/provider-routing/spec.md`, requirements "Compute-agnostic"
  and open provider registration: promises no compiled provider set and owner
  registration by description without a code change or platform allowlist.
- `openspec/specs/credential-vault/spec.md`: the future-cli negative scenario
  explicitly permits only the two exact subscription CLI names. This is an
  as-built security restriction, not authority to remove validation unchecked.

The open registry does not yet meet the broader portability promise. Existing
`byo-llm-connect-flow` planning establishes ownership/binding boundaries but is
not an implementation of arbitrary CLI compatibility.

Independent follow-up: [Claude portability exploration](../reviews/2026-09-05-provider-portability-exploration-claude.md)
at 2026-09-05 02:08 UTC found additional closed maps in credential overlays,
serving bindings and subscription deposits, and confirmed model participates in
definition identity. The lead reverified those source paths. Model-as-preference
therefore needs an identity-preserving migration; merely honoring the current
field is not the whole fix. The review records design qualifications rather than
authorizing unchecked profile variables, local-only execution or guessed defaults.

## Incident distinction

The installed production CLI was pinned at 0.135.0; it did not automatically
update. A live model catalogue exposed a reasoning value its parser could not
accept. See [the checklist recovery record](https://github.com/TinyAssets/TinyAssets/blob/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/workspace-node/checklist-loop.md)
and [native-model review](../reviews/2026-09-05-provider-native-model-defaults-claude.md)
for dated evidence and the distinction between catalogue mismatch and model
rejection. PR #2964 is an immediate
compatibility/diagnostic repair, not closure of this architectural finding.

PR #2977 deployed `9d361262` at 2026-09-05 02:15 UTC. The exact app retest
returned at 02:17 UTC with real provider-backed sequential, parallel and heartbeat
passes. This closes the observed startup incident, not open-provider extension,
model discovery, saved owner defaults, or the complete workflow checklist.

## Required outcome and proposed direction (not shipped design)

Keep the workflow engine independent of vendor names, command flags, model
catalogues, and vendor-specific output/error formats. Connection-local,
versioned execution descriptions/adapters should supply those details; discover
and validate capabilities instead of treating a recognized brand as readiness.
Permit owner-supplied or safely derived compatibility descriptions without
adding a vendor to the platform source. Changes must be tested before activation,
with failures isolated to the affected connection and last-known-good recovery
where the upstream still supports it.

Automatic adaptation must preserve custody, spend authority, sandboxing, and
tool permissions. Help text and remote metadata are untrusted evidence, not
permission to execute arbitrary commands or widen access. An unavailable LLM
cannot be the sole dependency needed to repair its own connection. No platform
LLM, credential borrowing, or unauthorized provider fallback is permitted.

Acceptance must include an unseen CLI name, changed optional metadata and output
shapes, rejected incompatible changes, and concurrent unaffected connections;
prove real authorized calls, tool behavior, cancellation, error reporting, and
unchanged authority without editing workflows. Provider-specific translation
may remain at the connection boundary; a compiled platform brand registry must
not be the extension mechanism.

Model-selection acceptance additionally requires fresh connection-scoped model
discovery, all available choices exposed to the owner, saved user defaults and
explicit selections honored by actual execution, and provider-default resolution
when no user choice is set. Prove newly available models become selectable and
provider-default changes are followed without a platform release, while explicit
user selections stay fixed. Report capability limitations and unavailable model
choices precisely; do not silently replace a rejected user choice.

No implementation can guarantee an arbitrary undocumented future executable or
discontinued upstream will always work. The requirement is autonomous, verified
adaptation where possible and precise connection-local failure where not, not
silently fabricated compatibility or reduced safety.

Next: scope the existing transport/custody extension seams, write a reviewable
OpenSpec proposal/design for the public/storage/authority changes before code,
and obtain independent cross-family review. PLAN principles already require
user-owned portable compute; do not rewrite PLAN as an unreviewed solution.
