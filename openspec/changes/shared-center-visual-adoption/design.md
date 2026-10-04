# Design

## Context
The shipped Preview action creates an install ask without showing a design. Generic approval then relays a message even though installation is deterministic. Catalogue navigation is hidden after adopting a custom screen. The separate live Village publication remains owned by Jonathan's agent.

## Goals / Non-Goals
Owners can browse, visually preview and explicitly copy supported public centers with no configured model. Preserve private edits, paused automations, pinned consent and source/recipient isolation. This patch does not export missing agent templates, introduce update lineage, alter live designs or activate automations.

## Decisions
- Keep all navigation and consent in trusted main-chat chrome, reachable when collapsed. Blank-screen guidance opens/prefills chat without sending.
- A read-only owner-bound `command_center_preview` returns only a verified public UI and declared public package assets, metadata and source fingerprint. It never reads publisher live files or recipient contents and never creates a consent request. Unsupported source/assets produce explicit errors.
- Render the public design only in a separate opaque-origin `/app/ui-frame` using existing sandbox/CSP. Its dedicated message listener checks exact frame/window and generation. Preview supplies explicitly empty preview read state, has no live owner bridge and refuses all writes/model/messages/installs. Trusted parent treats descriptions as text. Lifecycle cleanup invalidates old frames across selection/account/home changes. Vendored libraries retain existing digest checking.
- Carousel cards display the actual isolated visual and publisher description. Preview and Copy are distinct. Copy creates existing pinned consent and opens/focuses its exact request. Install-only acceptance/denial/refusal does not relay an agent message; intentional Reply and unrelated ask behavior remain.
- Explicitly paused automation registration preserves owner/home/owned-workflow/trigger validation without requiring an enabled consumer or ready provider assignment. Active creation and each execution keep their readiness checks.
- Installation preserves existing additive storage/reference/lease behavior. The receipt exposes an explicit Open copied screen action and paused/setup facts, with recipient-session fencing. No automatic activation or replacement of the user's selection is inferred.

## Risks / Trade-offs
Public UI scripts remain arbitrary code; containment and refusal of every owner capability are mandatory. Empty preview data may make a design sparse and must be labeled rather than fabricated as live activity. Public packages with unavailable assets are visibly unsupported. Rendering many previews is bounded by displaying the active carousel item, not granting more capability.

## Migration Plan
Additive read/UI change, no legacy-data rewrite. Existing copy records and designs remain unchanged. Rollback restores the earlier navigation without deleting installed content.

## Open Questions
None blocks this focused implementation. Actual published agent completeness and version updates are separately tracked in the coordinator follow-up plan.
