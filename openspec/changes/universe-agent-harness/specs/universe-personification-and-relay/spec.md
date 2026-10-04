## MODIFIED Requirements

### Requirement: converse runs one first-person turn on the universe's assigned engine, grounded in its own bundle
The `converse` operation SHALL resolve the universe's own directory and assigned engine (`UniverseContext`) and SHALL continue the conversation thread's persisted session (capability `universe-agent-harness`) on that engine with `role="writer"`, assembling the request mechanically from the base prompt, the selected agent's actual `starter/hooks.md` then `AGENTS.md` with owner instructions taking precedence and no missing-file fallback, its bounded `MEMORY.md`, its skill index and the session. Tool-less turns SHALL include the configuration-derived factual line "This turn has no tools available." regardless of editable instructions. The turn SHALL be in-process and scoped to the universe by construction, and SHALL NOT pass through the MCP transport auth gate. The turn SHALL run until the model stops calling tools, a user Stop, an idle provider, or a budget the user set; tools SHALL execute only in the platform tool jail, so any adapter that can serve the session may serve `converse`.

#### Scenario: converse continues the thread's session on the assigned engine
- **WHEN** `converse` is called for an existing universe with a founder message
- **THEN** it appends the message to the thread's session and runs a `role="writer"` turn on that universe's assigned engine from that session

#### Scenario: an unnamed newborn stays honest
- **WHEN** `converse` runs for a universe whose identity files hold no name
- **THEN** the assembled request contains no invented name

#### Scenario: a missing universe fails loudly
- **WHEN** `converse` is called for a universe directory that does not exist
- **THEN** it raises rather than fabricating a reply

## REMOVED Requirements

### Requirement: The engine turn is confined by a fail-closed sandbox
**Reason**: The WebFetch-only denylist was a stopgap until an OS sandbox existed; the platform tool jail (`universe_tools`, `provider_jail`) now confines every tool the agent runs, and the agent's own files are reached with tools rather than only through prompt injection.
**Migration**: Confinement is specified by `universe-agent-harness` ("The agent's core tools are file and shell tools in its jailed universe with public network") and the existing `universe-harness` jail requirements.

### Requirement: Learning is a separate tolerant model-extracted step with field-specific filtering, and reply delivery survives failures
**Reason**: The agent edits its own brain, memory and harness files directly in the turn that learns, and every change is versioned with rollback, so a second extraction call per turn is redundant cost.
**Migration**: Sole-owned by D7, after deployed memory IDs, history store, Undo and N>=10 paired write/recall trials per supported model-family x stock/customized-AGENTS fixture cell no worse than its extraction-on baseline. Preserve unresolved cursor spans verbatim with source IDs under "Learning retirement preserves unresolved sources" in this change before deleting `extract_learning`, `_learn_from_turn`, its converse invocation and `_UNRECORDED_LESSON`. Keep existing memory/history, verified-write/no-fact completion and reply tolerance on failure. No hidden background extractor or per-center compatibility path. The starter renderer slice does not own or duplicate this removal; the pre-compaction flush still preserves notes before summarization.
