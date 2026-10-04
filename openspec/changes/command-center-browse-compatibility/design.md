# Design

## Context
The deployed picker lists only PACKAGE_TAG definitions and hides Try on an empty list. Legacy public system definitions contain immutable workflow-version references, UI and automation specs, but no file blob. The existing installation pin provides owner consent, a claim token and resumable progress; its materialisation helpers already create private workflows, additive UIs and paused automations.

## Goals / Non-Goals
Make build, browsing and own-screen switching persistent; support truthful component-only copying of public systems. Preserve installed data, bridge/focus and account/home/epoch fences. No republishing, live-account tests, arbitrary script transformation or executing publisher resources.

## Decisions
1. Keep package and system listing shapes distinct. A system card states that it copies components only and includes no files. Validate each supported system before offering it; empty/error catalogues retain navigation and report their actual state.
2. Route an install request for a system to a separate component-plan implementation. Pins retain the explicit publication kind and complete normalized plan digest. Existing package blob verification and package plans remain unchanged. Only the existing trusted owner-answer path executes either kind.
3. Require a supported UI and public immutable workflow versions. Resolve declared UI workflow_refs from published component keys or unambiguous legacy source branch IDs into component keys before effects, then into recipient copies during materialisation. Refuse identifiable embedded source IDs with guidance; never replace arbitrary script text. Validate automation workflow/event references against the same components.
4. Component plans carry no blob, manifest, package version, imported files or publisher connection material. Reuse the existing private remix, additive UI, paused automation and progress helpers. Revalidate source public versions before each activation/resume; completed idempotent receipts return unchanged.
5. Trusted chrome renders own/shared navigation outside the custom frame and remains reachable for every valid owner session. Asynchronous catalogue results and install requests are fenced to the captured account/home/epoch. Build only prefills chat; browsing never sends a message or approves a copy.

## Risks / Trade-offs
Some older public scripts may hard-code publisher resource IDs and cannot be safely remapped; show an explicit unsupported reason rather than pretend a working copy. A public source disappearing or becoming private during a partial copy must stop remaining writes while preserving truthful progress. Local browser availability must be verified; skips are not browser proof. Independent security review and protected gates remain mandatory before integration.
