## Why

The change `universe-personification` (proposed 2026-06-24, Codex-ADAPT 2026-06-25, PR
#1372 merged 2026-06-25) states as its load-bearing invariant that a chatbot bound by the
founder's OAuth **embodies** the persona and speaks in first person, **never relays**.

**That invariant was reversed by host directive on 2026-07-02 and is contradicted by
shipped production behavior.** Three independent sources agree, re-verified against
`origin/main` at `7a118dca` on 2026-07-22:

1. **Ratified correction** — PR #1578 / `f605bb99` amends
   `2026-06-10-tiny-first-principles-spec.md (archived in git):128` to the host-directed relay model:
   the universe intelligence is the first-party personified speaker and the chatbot forwards
   through `converse` without impersonating it.
2. **Design note** —
   `2026-07-02-universe-intelligence-relay-architecture.md (archived in git)` §3 documents
   *why* it was falsified: a behavioral contract delivered in a tool result is structurally
   indistinguishable from prompt injection, and careful hosts correctly refuse it. Embodiment
   moved to a first-party model (persona in the universe intelligence's own system prompt).
3. **Shipped code** — `tinyassets/universe_server.py:209` now instructs the exact opposite:
   *"You do NOT speak as the universe: … RELAY their message via `converse` and RENDER its
   own first-person reply verbatim — you are the connector, not the universe."* The landed
   as-built capability `openspec/specs/universe-personification-and-relay/spec.md` (baselined
   2026-07-19) codifies the relay model.

The change nonetheless still carries **11 unchecked tasks** written against the reversed
invariant. Since 2026-07-19 OpenSpec is this project's spec standard and agents are directed
to work from `openspec/changes/*/tasks.md` — so an agent following the process *correctly*
would build precisely what the host said not to build. This is the
`stale-backlog-rows-misdirect` failure mode (2026-07-21: 5 of 5 dispatched rows had false
premises) reproduced inside the system that replaced the backlog.

The most dangerous single row is task 4.1 — `sync-specs` into
`openspec/specs/universe-personification/spec.md`. Running it would write the embodiment
model into `openspec/specs/` alongside the as-built relay capability, making the reversed
model read as current spec truth.

## What Changes

- **Classify all 11 unchecked tasks** (reversed / survives / already landed / unclear), each
  with a one-line reason verified against `origin/main` — recorded in
  `design.md` §"Task-by-task reconciliation".
- **Retire `universe-personification`** by archiving it with a `SUPERSEDED` banner on every
  artifact and a per-task classification written into its `tasks.md`, so the design thinking
  is preserved and readable but no longer appears in `openspec list` as claimable work.
  Nothing is deleted.
- **Carry the surviving intent forward** as ADDED requirement *deltas* against the landed
  `universe-personification-and-relay` capability — the items still correct under relay and
  **not** yet built (they stay in this change until implemented, see Impact):
  - authorization-before-voice, generalized past the current founder-only `converse` gate;
  - visitor actor binding + identity-tier gating (T0/T1/T2);
  - the anti-collision boundary, **restated honestly** — host-memory ingestion is advisory and
    not platform-enforceable, and any write restriction is scoped to the commons surface and
    must not restrict the universe's governed founder-learning path;
  - persona as a forkable `[composable]` default, reworded for first-party custody;
  - whole-mind personification on speaking surfaces, with direct-control tools kept honest;
  - one learned identity modulated across interlocutors/surfaces, never sourced from soul;
  - Tiny as the platform universe's governed personification, with no authority bypass.
- **Record what already landed** so it is not rebuilt: the anti-collision *instructions*
  guard, honest fallback, and grounded first-person assembly all shipped inside `converse`.
- **Record the resolved host decision** — PR #1578 / `f605bb99` amended the ratified narrative
  spec to the relay model before this reconciliation lands. See `design.md` §"Host decision
  resolved".

## Capabilities

### Modified Capabilities
- `universe-personification-and-relay`: adds the seven surviving-but-unbuilt requirements
  rescued from the retired change. No landed behavior is modified or removed — the relay
  model, `converse` sandbox, learning path, and founder-only gate all stand as specced.

### Removed Capabilities
- `universe-personification` (the *proposed* capability, never synced to `openspec/specs/`):
  retired. Its shipped relay-compatible content lives in the canonical as-built capability;
  seven valid but unbuilt requirements remain only in this active successor change, while its
  reversed chatbot-embodiment content must not ship.

## Impact

> **Implementation update (2026-07-29):** the original reconciliation PR was spec-only, but
> this active successor change intentionally remained open for the surviving requirements.
> Runtime tasks 6.3/6.6/6.8/6.9-interlocutor landed in PR #1776; this recovery slice implements
> the host-decided 6.1/6.7 explicit `write_page` target boundary. The historical impact bullets
> below describe the original reconciliation PR, not the current cumulative change state.

- **Spec-only. No runtime code changes.** The relay behavior already shipped and is not
  touched here.
- **`openspec/changes/universe-personification/**`** → archived to
  `openspec/changes/archive/2026-07-22-universe-personification/` with banners + classification.
- **`openspec/specs/universe-personification-and-relay/spec.md`** — **not modified.** The
  surviving requirements stay as deltas inside this change until they are built with tests;
  `openspec/specs/` is as-built truth and must not carry aspirations (`openspec/config.yaml`,
  AGENTS.md § Spec-driven development). This change therefore stays **active** rather than
  being archived on merge (Codex review 2026-07-22, finding 1).
- **Coordination update:** `STATUS.md` replaces the stale pointer to the archived change with
  the active successor change and its concrete implementation dependencies.
- **Gates:** opposite-provider review dispatched to Codex 2026-07-22 (verdict recorded in
  `design.md`); ratified correction landed in #1578; current-base strict validation and diff
  review are required before merge.
