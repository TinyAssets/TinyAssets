## Why

Founder direction (2026-10-04): minimal, replaceable plumbing; an editable OpenClaw/Hermes/dots-style starter built on it; user command centers may replace even the main agent. The founder's measurement on `b2bcfca0ef` is ~12–14K resident tokens per round versus pi's reported <1K; starter policy is still embedded in Python.

## What Changes

- Move starter behavior into versioned seed files and on-demand skills; remove redundant prompt inventories and the extra learning call.
- Preserve identity/first-person/honesty, the untrusted-envelope rule, disclosure filtering in code, and the four tool definitions in plumbing.
- **BREAKING:** existing centers adopt through a non-overwriting, resumable migration; customized files and owner-selected replacements win. New centers use the same starter bundle (D10).
- Measure this slice separately from D6's tool-description savings and prove remembering, asking, and onboarding through the founder's live agent.

## Capabilities

### New Capabilities

- `starter-agent-files`: editable starter content, safe seed adoption, and resident-cost/capability acceptance.

### Modified Capabilities

- `universe-personification-and-relay`: retire mandatory separate learning extraction.
- `deferred-turn-learning`: retire platform learning nudges, cursor-driven extraction, and its proposed background stages; preserve pending source material during cutover.

## Impact

This commit is proposal only. Later implementation touches `universe_intelligence.py`, `onboarding_note.py`, `universe_tools.py`, seed provisioning and `tests/test_converse_turn_cost.py`. No public tools, grants, jail or tool-description changes here: D6 (`feat/d6-ta-capabilities`) owns those ~8.5–9K tokens.

Separate change justified: the migration is one bounded, hard-to-reverse slice of `universe-agent-harness`, which already spans D1–D11. This change refines its persona/learning removal and D10 seed content; it does not duplicate their rollout. At sync, the parent must reference this migration and not restore its superseded learning requirements.

Owner: Codex. Branch: `spec/starter-agent-out-of-plumbing`. No PR requested or opened; implementation remains unstarted.
