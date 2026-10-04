## Context
Public definitions are immutable and globally readable. Private bindings supply recipient identity and optional operational settings. Existing addressed-agent runtime consumes component `config.instructions`; the package manifest's `agents` list describes files only. The live Village owner is actively editing their own design and retains that ownership.

## Goals / Non-Goals
**Goals:** Copy selected public instruction templates with stable keys, explicit consent, fresh private bindings, declared UI alias remapping, and crash-safe additive effects.
**Non-Goals:** Publish private agents automatically, copy provider/model/permission/resource settings, activate automations, guess missing agents from filenames or names, mutate live accounts, or execute nested publisher workflows.

## Decisions
The publish action adds `agent_templates: {stable_component_key: owned_binding_id}`. Selection requires an owner-owned conversable binding. Its public definition must consist only of instruction components understood by addressed-agent runtime; unsupported executable/configuration contracts are refused rather than silently dropped. Exported `tinyassets.agent-ref.v1` components contain only name, immutable public definition ID and content fingerprint. Private binding IDs remain only in the owner's pinned action.

The public component key is explicit and stable across reorderings. UI `agent_refs` maps aliases to selected source bindings before publish, public component keys after publish and fresh recipient binding IDs after install. No script text is rewritten. A source binding ID embedded in script is refused. Existing package `agents` file inventory retains its meaning.

Recipient binding IDs derive from owner, universe, install pin and stable component key. A single agent-store transaction inserts if absent, or accepts only an exact unchanged match on owner, universe, definition, configuration, revision and status. This closes the create/record-progress crash window. Existing edits or ID collisions fail without overwriting. Only schema version and approved public name enter private configuration; serving roles, provider/model assignments and grants never travel.

Every template and declared dependency is checked before materialisation. Existing public-version readability checks precede snapshot loading. Nested `invoke_branch_spec` dependencies are unsupported in this slice and refuse rather than retaining a source branch reference. All automations stay paused. No-provider installation is deterministic.

## Risks / Trade-offs
Instruction text changes agent behavior and therefore always requires recipient consent. A public definition with additional unsupported contracts cannot be installed by this instruction-template path; no claim is made that its omitted runtime would work. This change does not recover unpublished Village villagers. Frontend support for `agent_refs` is coordinated with the integration owner before delivery. Independent floor review and browser integration proof remain required.
