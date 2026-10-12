> # ⛔ SUPERSEDED — DO NOT BUILD FROM THIS FILE
>
> This change's core invariant — *"the chatbot **embodies** the persona and speaks in FIRST
> PERSON, never relays"* — was **REVERSED by host directive on 2026-07-02** and is
> contradicted by shipped production code (`tinyassets/universe_server.py:209`: *"You do NOT
> speak as the universe … RELAY … you are the connector, not the universe"*).
>
> Retired and reconciled by change **`reconcile-universe-personification-relay`**
> (2026-07-22). Surviving intent moved to the active successor change as deltas against the
> landed `universe-personification-and-relay` capability. Kept for its design reasoning only.
>
> **Every unchecked task below is annotated with its classification. Read the annotation
> before acting on any line.** Re-verified against `origin/main` @ `7a118dca`, 2026-07-22.

## 1. Ratified-spec amendments (`2026-06-10-tiny-first-principles-spec.md (archived in git)`) — NOT applied in this draft

> ⚠ **The "NOT applied" annotation is contradicted by the repo.** The amendment text IS
> present when this task ledger was retired, carrying the full embody / "never relays"
> invariant. PR #1578 / `f605bb99` subsequently corrected that ratified paragraph to the
> relay model before `reconcile-universe-personification-relay` was allowed to land.

- [x] 1.1 §9 (voice): embody / first-person invariant, **compact trigger-language** (no role-play sprawl), TinyAssets-surface-scoped guardrail + anti-collision + honest fallback + authorization-precedes-voice
- [x] 1.2 §3 (mind anatomy): personification = the **named interaction projection of the WHOLE mind** (voice expresses, soul governs, brain informs; goals/skills/hands/senses remain) — invariant, not an organ
- [x] 1.3 §7 (soul & org-chart): visitor-governance floor enforced in **brain assembly + authorization BEFORE voice**; persona behavior = `[composable]` default; T0/T1/T2 visitor binding

## 2. Connector behavioral surface (future build — behind verification gates + the Codex adaptations)

- [ ] 2.1 `control_station` prompt: compact first-person embodiment (trigger-language + view metadata; NO large role-play block; no tool-schema sprawl)
  > **⛔ REVERSED — do not build.** `universe_server.py:209` now instructs the opposite: the chatbot does NOT speak as the universe, it relays.
- [ ] 2.2 MCP `instructions` + tool descriptions: persona voice at connect + anti-collision "do not save into host memory" guard
  > **⛔ REVERSED (persona-voice half) / ✅ ALREADY LANDED (anti-collision half).** The instructions block now carries relay/render, not persona voice. The anti-collision guard shipped: `universe_server.py:216` — "Don't memorize persona views."
- [ ] 2.3 In-voice `assemble(lens) → view` delivery of ALREADY-AUTHORIZED content (depends on `brain-canonical-store` #1369)
  > **⛔ REVERSED as a chatbot-side mechanism — intent relocated.** Grounded first-person assembly landed *inside* `converse` (`universe_intelligence.py` builds the persona system prompt from the universe's own OKF bundle). Remaining lens/assembly work belongs to the active `brain-okf-canonical-store` change.
- [ ] 2.4 **Authorization-before-voice:** enforce identity / org-chart / privacy-tier filtering in brain assembly + action authz; voice never receives unauthorized content
  > **✅ SURVIVES — partially landed.** Still correct under relay. Landed narrowly: `converse` is founder-only + fail-closed. Unbuilt: general pre-assembly filtering by interlocutor. → carried forward as an ADDED requirement on `universe-personification-and-relay`.
- [ ] 2.5 Visitor actor binding + tier gating (no TinyAssets OAuth → T0; durable subject → T1; owner OAuth → T2)
  > **✅ SURVIVES — unbuilt.** No `identity_tier` machinery in `tinyassets/*.py`; public access is a deferred slice. Under relay the tier binds to the `converse` caller, not to an embodiment session. → carried forward; must agree with the active `universe-visibility` change.
- [ ] 2.6 Anti-collision write-path: reject profile-shaped / persona-dossier writes
  > **✅ SURVIVES — NARROWED/ADAPTED, unbuilt.** The unscoped dossier rule contradicts governed founder learning. The successor limits any future rejection to a defined external/commons endpoint, predicate, and redirect and exempts the universe's own learning path.
- [ ] 2.7 Honest fallback / degraded mode: no invented persona state; no embodiment-from-memory when no active universe/persona
  > **✅ ALREADY LANDED — do not rebuild.** `universe_intelligence.py:428` raises on a missing universe; `:164` "newly born and still learning"; `:208` never-infer/never-invent rules. Covered by three as-built scenarios.
- [ ] 2.8 Persona behavior as a forkable `[composable]` default; substrate enforces only identity/authority/privacy floor
  > **✅ SURVIVES — ADAPTED.** Custody moved first-party. Forking changes universe-side learned self-model/voice content; operational soul remains governance input and never supplies or replaces persona identity, and no script is handed to the chatbot.
- [ ] 2.9 Tool-selection regression tests (Claude + ChatGPT) proving embodiment does not degrade accuracy
  > **⛔ REVERSED as written — residual preserved.** No embodiment prompt exists to regress. The residual risk (connector instruction density vs tool-selection accuracy) is carried forward as definition task 6.3 against `live-mcp-connector-surface`, not as a threshold-less scenario in this delta.

## 3. Cross-provider review

- [x] 3.1 Codex review obtained — verdict **ADAPT** (`2026-06-25-universe-personification-codex-review.md (archived in git)`); 7 required adaptations
- [x] 3.2 Folded all 7 adaptations into proposal + design + spec delta:
  - [x] 3.2.1 Invariant = projection of the WHOLE mind (not soul+brain+voice only)
  - [x] 3.2.2 Anti-collision requirement (instructions/views + reject profile-shaped writes)
  - [x] 3.2.3 Authorization precedes voice (floor enforced in assembly, not by prompt)
  - [x] 3.2.4 Visitor actor binding (T0/T1/T2 default)
  - [x] 3.2.5 Compact / testable embodiment (no role-play sprawl; regression tests)
  - [x] 3.2.6 Honest fallback (no invented persona state)
  - [x] 3.2.7 Added scenarios (multi-universe; private-content probing; host-memory collision; outside-TinyAssets non-hijack)

## 4. OpenSpec fold-back

- [ ] 4.1 `sync-specs` → `openspec/specs/universe-personification/spec.md` (after approval)
  > **⛔⛔ REVERSED — MUST NOT RUN, EVER.** Executing this would write the embodiment model into
  > `openspec/specs/`, where it would read as current spec truth beside the shipped
  > `universe-personification-and-relay` capability. This is the single most dangerous line in
  > this change. The capability it would create must not exist.
- [x] 4.2 Draft PR opened — #1372 *(merged 2026-06-25)*
- [ ] 4.3 Archive after merge
  > **✅ SURVIVES — discharged.** Performed by `reconcile-universe-personification-relay` (2026-07-22), which archives this change with the classification attached.
