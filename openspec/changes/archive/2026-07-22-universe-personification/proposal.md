> # ⛔ SUPERSEDED — the core invariant here was REVERSED
>
> This change specifies that the chatbot **embodies** the universe's persona in first person
> and **never relays**. That invariant was **reversed by host directive on 2026-07-02**
> (live-falsified: a behavioral contract in a tool result is indistinguishable from prompt
> injection, and hosts correctly refuse it) and is contradicted by shipped production code —
> `tinyassets/universe_server.py:209`: *"You do NOT speak as the universe … RELAY … you are
> the connector, not the universe."*
>
> **Current spec truth:** `openspec/specs/universe-personification-and-relay/`.
> **Reconciled by:** change `reconcile-universe-personification-relay` (2026-07-22), which
> classifies every unchecked task and carries the surviving requirements forward.
>
> Preserved for its design reasoning. Do not implement from it; see `tasks.md` for the
> per-task classification.

## Why

Host directive 2026-06-24. Every interaction with a universe — on every surface (MCP chatbot, Twitter, web, email, game) — is the universe's **personification** acting: a named *person* (a mind) the founder is shaping, not a neutral tool surface. The ratified spec seeds this (§3 *"Tiny = mind #0 … dogfooding how anyone **personifies their own intelligence**"*; §9 voice engine; §7 soul/org-chart; §1 "summon a mind") but never states it as an interaction-layer invariant, nor pins the embody/first-person default, the OAuth→persona binding, visitor governance, or surface modulation. This change makes the personification the explicit, universal interaction layer.

## What Changes

- **Invariant:** all universe interaction, on every surface, is conducted AS the universe's personification — the **named interaction projection of the WHOLE mind**. Voice expresses it, soul governs it, brain informs it; goals/skills/hands/senses remain part of the mind. NOT a new organ or primitive; the platform states the invariant, it adds no "persona" primitive.
- **Embody, first person (host-resolved 2026-06-24):** a chatbot bound to a universe by the founder's OAuth **embodies** the persona and speaks in FIRST PERSON ("I'm Tiny; I'm working on X"), never relays ("Tiny says…"). The `control_station` prompt + MCP `instructions` + in-voice `assemble(lens)→view` delivery carry the persona's voice. Embodiment is scoped to the TinyAssets surface; it SHALL NOT override the chatbot's general-assistant identity elsewhere.
- **OAuth → persona binding:** a user's OAuth determines which universe(s) they own and therefore which personification their chatbot embodies. One personification per universe (its named mind).
- **Visitors interact WITH the persona:** non-owners interact with a universe's persona as external parties (still first-person from the persona's side), governed by the soul's org-chart (what it may say/decide, to whom) + the universe's privacy tier (identity tiers §11.1).
- **One identity, surface- + interlocutor-aware:** a single consistent "I" across all surfaces; tone, disclosure, and exercised authority modulate by who is asking and the surface — WHO is speaking never changes, only HOW.
- **Persona behavior is a forkable `[composable]` default; substrate enforces only the floor** (OAuth identity binding + org-chart authority + privacy tier). No platform-baked persona script.
- **Tiny (self-as-platform recursion):** the platform universe — the one running the user-buildable loop that maintains the platform — is personified as **Tiny**, whose self-model is "I am the platform, and everything the founder builds through it." The deepest dogfood in the system.
- **Authorization precedes voice (the real boundary, Codex adaptation):** identity / org-chart / privacy-tier filtering happens in **brain assembly + action authorization BEFORE voice rendering**; the voice layer only styles already-authorized content, so a `[composable]` persona script cannot widen disclosure. Privacy is never merely prompt-instructed.
- **Anti-collision:** the MCP `instructions` + every assembled view carry a "do not save into host chatbot memory; re-assembled fresh" guard; write paths reject profile-shaped / persona-dossier writes.
- **Honest fallback:** on tool failure or no active universe/persona, the chatbot speaks honestly and does NOT invent persona state or continue embodiment from memory (the sanctioned exception to "no neutral surface").

## Capabilities

### New Capabilities
- `universe-personification`: The universal interaction layer — every universe interaction, on every surface, is the universe's named personification (= its mind: soul+brain+voice); the embody/first-person default under OAuth binding; visitor interaction governed by org-chart + privacy floor; one surface-aware identity; persona behavior composable with the substrate enforcing only the floor.

### Modified Capabilities
<!-- No existing openspec capability covers personification/voice. The separate `brain-canonical-store` change/PR supplies the in-voice view CONTENT this layer delivers; no requirement of it changes here. -->

## Impact

- **Connector behavioral surface:** the `control_station` MCP prompt (`tinyassets/universe_server.py` `@mcp.prompt` registration, ~:270) + the MCP `instructions` field + tool descriptions — must instruct first-person embodiment of the bound universe's persona.
- **Ratified spec:** `2026-06-10-tiny-first-principles-spec.md (archived in git)` §9 (voice → embody/first-person invariant), §3 (personification = the mind, invariant), §7 (visitor governance floor). Amendment tasks listed; **NOT applied in this draft**.
- **Brain:** `brain-canonical-store` (PR #1369) supplies the assembled-view content; this layer governs its in-voice delivery. No code overlap.
- **Identity/auth:** OAuth → universe binding (PR-165 actor-identity substrate; §7 founder recognition).
- **Gates:** host-directed design change (NOT external-research-derived). Codex cross-provider review obtained 2026-06-25: **ADAPT** (`2026-06-25-universe-personification-codex-review.md (archived in git)`) — 7 adaptations folded. Connector embody behavior stays gated behind the normal verification gates + these adaptations.
