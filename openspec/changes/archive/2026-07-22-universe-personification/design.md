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

## Context

The ratified TINY spec already frames a mind as a personification — §3 *"Tiny = mind #0 … dogfooding how anyone personifies their own intelligence,"* §9 the voice engine, §7 the soul/org-chart, §1 "summon a mind." What it does not yet state is the **interaction-layer invariant**: that every surface interaction with a universe is that universe's personification acting, plus the behavioral defaults (embody/first-person, OAuth→persona binding, visitor governance, surface modulation). Host directive 2026-06-24 supplies these and resolved the forks: embody (first person); the mind IS the personification (invariant, not a new organ); visitor persona is a composable default over a substrate floor.

Codex cross-provider review (2026-06-25, `2026-06-25-universe-personification-codex-review.md (archived in git)`): **ADAPT** — direction right, but the draft left security + host-memory boundaries *prompt-enforced*, which is not a real boundary. The 7 adaptations below are folded.

This sits above the brain (`brain-canonical-store`, PR #1369): the brain produces the **authorized** assembled view; the personification governs how that already-authorized view is voiced and to whom. Soul (== universe brain, per the prior host ruling) supplies governance; the personification is the named interaction projection of the whole mind.

## Goals / Non-Goals

**Goals:**
- State the interaction-layer invariant precisely: every surface = the universe's personification = the named projection of the whole mind.
- Make the privacy/authority floor a REAL boundary (enforced in brain assembly before voice), not a prompt instruction.
- Pin embody/first-person as compact, testable connector behavior; protect tool-selection reliability + the anti-collision contract.
- Keep persona behavior `[composable]`; substrate enforces only identity/authority/privacy + honest fallback.

**Non-Goals:**
- Adding a "persona" organ/primitive (the mind IS the personification).
- Building the connector embody behavior or voice engine here (design only).
- Defining the brain's content/format (that is `brain-canonical-store`).
- Multi-persona-per-universe, persona marketplaces, cross-universe blending.

## Decisions

**D1 — The personification is the named interaction projection of the WHOLE mind (invariant, not an organ).** (host fork 2; Codex adaptation 1)
All surfaces route through the mind-as-person. Voice expresses it, soul governs it, brain informs it; goals/skills/hands/senses remain part of the mind. We do NOT define the mind as only soul+brain+voice, and we add no `persona` primitive.
Alternative: a distinct `persona` primitive — rejected (over-engineering; build-boundary law #4).

**D2 — Embody, first person — compact and testable.** (host fork 1; Codex adaptation 5)
The OAuth-bound chatbot speaks AS the persona in first person; authorized views arrive in-voice. Lands in the `control_station` prompt + MCP `instructions` as **compact trigger-language + view metadata**, never a large role-play block, never tool-schema sprawl. TinyAssets-surface-scoped, with Claude/ChatGPT tool-selection regression tests as the guard.
Alternative: relay ("Tiny says…") — rejected by host. Large role-play prompt — rejected (degrades tool selection; violates frozen-tool-schema economics).

**D3 — Authorization precedes voice (the real boundary).** (Codex adaptation 3 — the central security fix)
Identity-tier / org-chart / privacy-tier filtering happens in **brain assembly + action authorization, before any voice/persona rendering**. The voice layer only styles already-authorized content; it never receives private-tier content with an instruction to hide it. The composable persona script cannot widen disclosure.
Alternative: persona "instructed not to reveal" — rejected (prompt-enforcement is not a boundary; the exact failure Codex flagged).

**D4 — Anti-collision: persona views never enter host memory.** (Codex adaptation 2)
MCP instructions/tool descriptions + every view carry the "do not save into your memory; re-assembled fresh" guard; write paths reject profile-shaped / persona-dossier writes. First-person persona content is exactly what host memories (ChatGPT Dreaming / Claude memory) would absorb as a standing preference — guarded explicitly. (Brain anti-collision contract, research-impl ADOPT #3.)

**D5 — OAuth → persona + tier binding, with a visitor default.** (host fork 3; Codex adaptation 4)
OAuth fixes the owned universe(s) and embodied persona; one persona per universe. Tier: no TinyAssets OAuth → T0; durable host/OAuth subject → T1; verified owner OAuth → T2/founder. Persona behavior is a forkable `[composable]` default; substrate enforces only the floor.

**D6 — One identity, surface-modulated.** A single "I" across surfaces; tone/disclosure/authority modulate by interlocutor + surface; identity never changes, only expression.

**D7 — Honest fallback.** (Codex adaptation 6) On tool failure or no active universe/persona, the chatbot does NOT invent persona state or continue embodiment from memory; degraded/diagnostic modes speak honestly — the sanctioned exception to "no neutral surface."

**D8 — Tiny self-as-platform.** The platform universe is personified as Tiny, self-modeling as the platform itself (org = platform architecture + founder vision, hands = the loop, brain = the platform store). The recursion is intentional.

**D9 — Amend the narrative spec (tasks, not applied in this draft):** §9 (embody/first-person + compactness), §3 (personification = projection of the whole mind), §7 (visitor floor enforced before voice).

## Risks / Trade-offs

- **Prompt-enforced privacy** → moved to a real boundary: authorization before voice (D3); voice never sees unauthorized content.
- **Host-memory collision** → every view + write path carries anti-collision guards (D4).
- **Tool-selection degradation** → compact trigger-language only, regression-tested (D2).
- **"TinyAssets surface only" not hard-enforceable** → bounded by phrasing + tests + honest fallback (D2/D7); accepted as a soft boundary with explicit guards, not assumed.
- **Visitor identity ambiguity on host surfaces** → explicit T0 default for unauthenticated users (D5).
- **"No neutral surface" vs diagnostics** → resolved by the honest-fallback exception (D7).

## Migration Plan

Design change only — no runtime change in this draft. Ratified-spec amendments (§9/§3/§7) are tasks, not applied. Connector embody behavior is future build behind the normal verification gates + the Codex adaptations above. **Rollback:** none needed (nothing applied in the draft).

## Open Questions

- The "persona lens" parameterization — is interlocutor+surface literally a lens over the brain's `assemble(lens)`, or a separate voice-layer config? (Whichever, authorization stays in assembly per D3.)
- Exact `control_station` trigger-language wording that embodies first-person without role-play sprawl (measure against the frozen tool-schema budget).
- Durable cross-host actor binding for the same human (research-impl Gap #5) — improves T1 stability for visitors.
- Whether outbound surfaces (Twitter/email) share the persona-lens machinery or only the voice hard-lines.
