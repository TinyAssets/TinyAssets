## REMOVED Requirements

### Requirement: A turn that recorded its own lesson does not pay for a second pass
**Reason**: Neither settled nor unsettled turns require platform extraction after cutover.
**Migration**: `starter-agent-files` moves recording into the memory skill; preserve reply delivery on failure and unresolved source material.

### Requirement: An unsettled learned-watermark is the record of work owed
**Reason**: Platform extraction cursors no longer drive model work.
**Migration**: Preserve unresolved spans verbatim with source IDs in the owner-only review file before retiring cursor use; never discard history or declare unwritten facts learned.

### Requirement: The turn records its own lesson in-turn, at no extra cost
**Reason**: The platform nag and promised zero-cost recording become on-demand starter behavior; reading a skill can cost a call.
**Migration**: Memory skill records owner-taught durable facts in-turn, verifies persistence, reports failures and keeps pending review sources. Measure actual calls.

### Requirement: The deferred path, when it exists, re-derives its authority
**Reason**: This change deletes extraction rather than adding a deferred extractor.
**Migration**: Do not launch background learning or extend request leases; any later owner-built work remains subject to existing execution authority.

### Requirement: The foreground turn keeps budget priority
**Reason**: The proposed secondary extraction operation is retired.
**Migration**: Remove its scheduling obligation; ordinary model admission and budget enforcement remain unchanged.

### Requirement: One path for every account
**Reason**: Starter learning is replaceable owner content, not a mandatory account-wide behavior.
**Migration**: Apply the same non-overwriting adoption protocol to every account; retain provider-neutral plumbing and owner-bound compute.
