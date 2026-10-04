## REMOVED Requirements

### Requirement: Learning is a separate tolerant model-extracted step with field-specific filtering, and reply delivery survives failures
**Reason**: The starter memory skill records facts in the active turn; a mandatory second model call is starter policy embedded in plumbing.
**Migration**: Apply `starter-agent-files` adoption and preserve unresolved source turns before removing extraction. Existing memory stays intact, failed writes remain visible/pending, and reply delivery survives memory failures. Coordinate this removal with the identical pending removal in `universe-agent-harness`; sync it once.
