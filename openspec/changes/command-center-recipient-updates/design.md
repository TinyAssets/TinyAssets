# Design

## Context
Component copies pin the full normalized source plan and a digest of its public definition. Activated pins record recipient target IDs. File-package versions use author/name/version, but names cannot establish a stable component publication lineage. This change supplies pure contracts; it does not trust client-provided records as authority.

## Goals / Non-Goals
The planner computes reviewable updates and guards owner data. It performs no I/O, model calls, publication, installation, consent execution, storage migration or automatic scheduling.

## Decisions
- Immutable releases bind stable publication ID, author, exact definition ID/digest, parent, sequence, summary and complete component contract. A server registry must authenticate publication ownership and persist these records append-only. Hashes prove consistency, not authorization.
- Components have stable keys, kind, source digest, exact dependency digests, capability requirements and privileged-effect status. Unknown kinds and privilege changes fail closed.
- Adoptions retain each component's source release and source baseline separately from its remapped recipient target and installed digest. Selective adoption therefore reports mixed versions honestly.
- Caller-supplied observations and transformed candidates come only from authenticated server adapters. They are immutable snapshots for planning. Every planned write carries its exact expected current digest; execution must re-read under the same recipient lock/transaction before each mutation and atomically persist progress. This module is not an executor or a permission check.
- Three-way comparison preserves local-only edits, blocks divergent changes and local deletions, and detects dependency mismatches across selected AND retained components. Selection never silently expands to dependencies. Source removals require recipient resolution rather than deleting recipient data.
- Automatic policy is per adoption, off by default, and restricted to accepted capabilities. New components, privileged effects, unresolved dependencies/conflicts and missing source stop it. Plans never enable automations and require preserving existing runtime state.
- Legacy provenance uses only an activated platform pin whose source definition/plan digest and complete recorded target mapping validate. It establishes exact prior source, not a release series. Explicit linking to an authenticated release record is a later recipient decision; no author/name or content-similarity enrollment.

## Risks / Trade-offs
- Pure records cannot authenticate ownership → registry/API adapters must authenticate and use platform records, never accept a client-provided verified flag.
- In-place execution can race → plans contain digest preconditions; no apply API is exposed until durable CAS/recovery integration is reviewed.
- Exact dependency hashes can block compatible updates → explicit recipient selection is preferable to silently changing dependencies.
- Unknown effects may be omitted by publishers → capability declarations must be derived/validated by trusted adapters, not treated as an execution grant.

## Migration Plan
Add the unconnected planner and tests. A later reviewed registry/adapter patch records new adoptions and validates legacy pins. Only then connect owner consent, version browsing and opt-in scheduling. Existing installations remain unchanged.

## Concrete manual UI executor
The first connected callable adapter is limited to existing component-system UI replacements. `record_install(universe_id, request_id)` loads a real activated owner-scoped pin, validates its immutable source, reconstructs remapped UI content and requires an exact match before registering a clean baseline. Existing altered legacy screens are conflicts, not automatically linked. Agent templates use `plan.agent_templates` and `progress.agents`, coordinated with the public-template lane.

Two metadata tables live in the same `.tinyassets.db` as `universe_app_ui`. They retain hashes/IDs, not UI/script/source bodies. Each adoption has at most one pending consent, replaced by a fresh preview; the latest completed request digest lives on its adoption for retry. The retained immutable original install pin remains the evidence for copied workflow, agent and automation targets.

`preview_update` pins a server-derived exact-source/read-set plan. `commit_update` requires a matching request/digest and explicit accepted/declined decision, reserves the UI bytes using existing storage accounting outside the database lock, then rechecks ACL ownership, source availability, adoption/UI revision, source and current hashes, and dependency configuration inside `BEGIN IMMEDIATE`. UI replacement, baseline, receipt and request consumption commit together. Existing component and asset validators run; private/unowned assets are never fetched. Quota reservation is released on rollback and committed after the database commit, matching existing UI accounting semantics. A post-commit accounting failure is recoverable through the stored applied receipt; it cannot cause a second screen write.

A manually selected same-author definition is explicitly a user-selected replacement, not a publisher release. The response identifies the new UI definition and the original retained component definition. Any changes to workflows, automation specs, agent templates or UI reference bindings refuse; additions/deletions need a future explicitly supported plan. Private dependency content is preserved and fenced in the preview read set; these observations do not falsely attest it still equals the publisher baseline.

Root integration owns route registration, trusted owner consent UI, and the post-install hook. This lane creates callable APIs and real-store tests only. Future author-bound release registration/version browsing, conflict resolution and opt-in scheduling remain dependent work; this subset does not claim the whole update product complete.

## Retained automation write fence
Independent review of `44c9514e` found that retained automation IDs appeared in consent but actual automation rows were absent from its read set. Retirement after preview therefore incorrectly allowed a UI update. The repair attaches the existing trusted-base `.automations.db` before `BEGIN IMMEDIATE` and reads its actual recipient automation rows through that same connection. SQLite's write reservation spans attached databases, so existing `AutomationStore` transactions cannot retire/reconfigure a dependency between its final check and UI commit. Missing database/schema or missing, retired, foreign-owner/home or redirected workflow rows refuse. Zero-automation stores do not create an automation database.

The dependency hash covers scheduling/event/input configuration, identity, owner/home, desired state, revision and retirement. Run timestamps, result counters and last-run IDs are observational and excluded. No SQL writes target the attached database: the UI/baseline/receipt remain the sole main-database atomic mutation. This explicitly does not claim multi-file atomic writes in WAL mode; SQLite documents that limitation at https://www.sqlite.org/lang_attach.html . The real competing-writer test traces `AutomationStore.retire` reaching `BEGIN IMMEDIATE`, verifies it remains blocked inside the final UI transaction, and confirms it can retire only after observing the committed UI revision. Rollback releases both reservations.
