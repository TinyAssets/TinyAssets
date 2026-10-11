# Host-Q Digest v3 — DRAFT (promote when v2's Q4 + Q1-3 cleared)

**Date:** 2026-04-19 (updated post-Q-host-action-1 resolution)
**Author:** navigator
**Status:** Pre-draft. Hold until host clears v2 (Q4 PLAN.md.draft review + Q1/Q2/Q3 full-platform). Promoting earlier dilutes attention per lead's standing rule. Then promote to `docs/ops/2026-04-19-host-q-digest.md` (replacing v2).
**Diff intent vs v2:** (1) ~~Q-host-action-1 (env-var bind)~~ **RESOLVED 2026-04-19 by lead per host's "you are the host" directive** — `OLLAMA_HOST=http://localhost:11434` bound at user-env, tray restarted, MCP `get_status` verified `llm_endpoint_bound: "http://localhost:11434"`. **Caveat: do NOT resume affected daemons until dev ships #49 (Fix E DB cleanup) — drift risk still open.** (2) Carry forward unanswered v2 Qs minus the cleared ones (Q4 + Q1/Q2/Q3 assumed cleared at promotion time). (3) Promote previously-deferred concerns #4/#5/#8 to first-class Qs. (4) Add Q-sat-6 from Mission 26 intel. (5) Update cheat-sheet bands — Q4 promoted back to 30-sec slot since Q-host-action-1 already RESOLVED.

---

## Framing

The dev queue and host-decision queue are now structured around four active workstreams plus one resolved item:

0. **RESOLVED:** Host-action item Q-host-action-1 (LLM endpoint bind) — closed 2026-04-19 by lead. See "Resolved this cycle" below.
1. **PLAN.md.draft review (Q4 from v2)** — single most-leveraged remaining Q; refactor execution sequence + every future feature lands on this shape.
2. **Self-auditing-tools track (Q5-Q9 from v2 + new Q-sat-6)** — Track Q dispatch + 6th surface candidate from Mission 26.
3. **Layer-3 rename (Q10-Q12 from v2)** — universe→workflow server module migration.
4. **Privacy + sensitivity (Q-priv-1 through Q-priv-4 from concern 4 + concern 5)** — bilateral asks bundled.
5. **Full-platform §11 residual** (the 11+ §11 Qs beyond v2's Q1-Q3) — the heaviest workstream; defer until host has bandwidth.

**Re-rank rationale (post-Q-host-action-1 resolution):** With the env-var bind resolved at the lead/host layer, **Q4 (PLAN.md.draft review) returns to the top-leverage position.** Q4 was v2's #1 before Q-host-action-1 emerged; with that out, Q4 is back as the single most-leveraged remaining Q. Approval there means every line of refactor work and every new feature lands on the correct shape (R1 STEERING already shipped; R2 bid-package scoping doc dispatch-ready at `docs/exec-plans/active/2026-04-19-bid-package-promotion.md`; R7 storage split scoping in flight).

---

## Resolved this cycle

### Q-host-action-1 — RESOLVED 2026-04-19

**Resolution:** Lead bound `OLLAMA_HOST=http://localhost:11434` at user-env level (persists), killed existing tray/MCP server, restarted with env in scope. **Verified via MCP `get_status`:** `llm_endpoint_bound: "http://localhost:11434"`.

**Effects closed:**
- Devin Session 2 tier-2 trust gap (`served_llm_type=any`, no endpoint binding evidence) — closed at the env layer.
- Mission 26 Probe B Branch A daemon starvation (24 min after worldbuild, zero emissions) — closed at the env layer.

**Open caveat:** Do NOT resume affected daemons (specifically the universes that ran during the unbound-window) until dev ships #49 (Fix E DB-derivative cleanup). The orphan facts in `story.db::extracted_facts` and `story.db::character_states` from the drift-window are still on disk; resuming a daemon now would pull them as canonical context. Time-bomb-coupled gap is still open until #49 lands.

**Reference:** `2026-04-19-mission26-sporemarch-echoes.md (archived in git)` §1 + §4 (cross-session pattern); `2026-04-19-schema-migration-followups.md (archived in git)` §3.4 (the time-bomb-coupling).

---

## The single most-leveraged remaining Q

### Q4 — PLAN.md.draft: approve, iterate, or reject?

**Framing.** `PLAN.md.draft` proposes three architectural commitments that haven't been canonical before:
1. A **Module Layout** section codifying 5 canonical subpackages (`tinyassets/api/`, `tinyassets/storage/`, `tinyassets/runtime/`, `tinyassets/bid/`, `tinyassets/servers/`) with a migration policy ("flat module > 500 LOC OR overlapping sibling responsibility → gets a subpackage").
2. The **self-auditing-tools** principle as a Cross-Cutting Principle — trust-critical tools include their own caveats (structured evidence + structured caveats + chatbot composes narrative).
3. The **engine/domain seam is named** — every action lives in exactly one of `tinyassets/api/` or `domains/<name>/api/`. No third location.

**These are load-bearing for refactor dispatch** (hotspots #1-#3 in `2026-04-19-project-folder-spaghetti.md (archived in git)`) AND for the §11 Track Q decision in Q5 below AND for engine/domain separation (#11 design note).

**Choices:**
- **(a) Approve as-is.** Replace PLAN.md with the draft. R2 bid-package promotion (pre-staged at `docs/exec-plans/active/2026-04-19-bid-package-promotion.md`) executes as the first canonical Module Layout commit. R7 storage split scoping (in nav flight) follows.
- **(b) Approve with edits.** Specific iteration asks; navigator revises the draft to `PLAN.md.draft.v2`; loops until approval.
- **(c) Reject the Module Layout commitment.** Keep PLAN.md as it is; refactor work proceeds opportunistically without architectural commitment to the 5-subpackage shape. Risk: future features land in the existing flat namespace and the spaghetti grows.

**Recommendation: (a).** The Module Layout absorbs the spaghetti audit's 5 target subpackages without overcommitting — every choice in the draft is reversible, and the migration policy is gradient (flat-modules-staying-flat is fine if they don't grow). The principle additions (self-auditing tools + named engine/domain seam) are documentation of patterns the codebase is already moving toward; deferring approval just delays the canonicalization.

**If (b), most likely iteration vectors:** subpackage names (e.g., `tinyassets/market/` instead of `tinyassets/bid/`), or the ~500 LOC migration threshold (could be tighter or looser), or whether `tinyassets/servers/` should be `tinyassets/entrypoints/` to match the integration-shell language.

---

## The self-auditing-tools Qs (Q5-Q9 unchanged from v2 + new Q-sat-6)

These remain queued from v2; promotion-target unchanged. Plus one addition from Mission 26.

### Q5 — Track Q standalone or P-extension? (carry-over)

**Recommendation: (a) Track Q standalone.**

### Q6 — Implementation order across the 5 surfaces? (carry-over)

**Recommendation: (b) impl-ease — get the pattern right on the first instantiation.**

### Q7 — Caveat authoring: hand-written, derived, or hybrid? (carry-over)

**Recommendation: (c) hybrid.**

### Q8 — Bundle dry-inspect with Track Q, or separate follow-on? (carry-over)

**Recommendation: (a) bundled.**

### Q9 — Audit-log retention for evidence fields? (carry-over)

**Recommendation: (b) 7-day TTL.**

### Q-sat-6 — NEW: add `get_dispatch_evidence` as the 6th self-auditing surface?

**Framing.** Mission 26 #B5 found that concern 1 (Sporemarch dispatch-guard retention) is *chatbot-unverifiable* because no MCP surface exposes dispatch-guard log lines. Same chain-break shape as Devin's LIVE-F8 (pitch-vs-product gap closed by `get_status`). Adding a 6th self-auditing surface — `get_dispatch_evidence` (or folding into existing `get_status` as an optional evidence section) — closes it.

**Choices:**
- **(a) Add `get_dispatch_evidence` as a 6th surface in Track Q.** ~0.5-1 dev-day. Closes concern 1 chatbot-side; user-sim can verify directly.
- **(b) Fold into existing `get_status`** as an optional `evidence_type=dispatch` parameter. ~0.25 dev-day. Smaller surface area, slightly less consistent with the per-surface pattern.
- **(c) Defer.** Concern 1 stays chatbot-unverifiable; user-sim missions targeting it produce CANNOT-VERIFY-VIA-CHATBOT outcomes.

**Recommendation: (a).** Adds the surface as a typed instantiation of the self-auditing-tools pattern, consistent with the §4 surface list in `docs/design-notes/2026-04-19-self-auditing-tools.md`. (b) is cheaper but obscures the pattern; (c) leaves a known-unverifiable concern in place.

---

## The layer-3 rename Qs (Q10-Q12 unchanged from v2)

These remain queued. Carry-overs:

### Q10 — Module rename target: which name?

**Recommendation: (a) `tinyassets/workflow_server.py`** — brand match.

### Q11 — Compat-flag scheme: shared or independent flip clocks?

**Recommendation: (a) two independent flags** — different cadence per rename.

### Q12 — Plugin-directory rename: hard cutover or parallel-name bridge?

**Recommendation: (a) hard cutover** + migration script + v0.2.0 release notes.

---

## The privacy + sensitivity Qs (NEW, from concerns #4 + #5)

These were previously concern-list entries waiting on host. Promoting to first-class Qs since digest is the host's queue surface.

### Q-priv-1 — Privacy mode threat model scope (from `docs/design-notes/2026-04-18-privacy-modes-for-sensitive-workflows.md` §6.1)

**Framing.** Are we defending against (a) Anthropic as honest operator that might be subpoena'd / breached, (b) Anthropic as potential adversary, or (c) arbitrary observers of chat logs? The answer determines what "acceptable metadata leakage" means for the private-universe flag.

**Choices:**
- **(a) Honest operator + breach risk.** Most realistic; allows current §7 redactor design.
- **(b) Adversarial Anthropic.** Drives a much heavier design — local-only providers strictly enforced, no fallback, paranoid metadata aliasing.
- **(c) Arbitrary log observers.** Drives strict end-to-end encryption requirements before TinyAssets can serve the use case.

**Recommendation: (a).** Matches the most plausible threat model for tier-2 hosts. (b) is over-design for the user base we have; (c) is out of scope without a security audit.

### Q-priv-2 — Metadata leakage acceptable? (from §6.2)

**Framing.** Is "host runs an Allied AP workflow" *itself* sensitive, or only the content? If yes, §7.5 universe-name aliasing becomes mandatory.

**Choices:**
- **(a) Only content sensitive.** Universe names visible in metadata is fine.
- **(b) Existence sensitive.** Mandatory universe aliasing in MCP responses.

**Recommendation: (a)** for MVP. Defer (b) until a host explicitly asks for existence-privacy.

### Q-priv-3 — Third-party providers in daemon fallback? (from §6.3)

**Framing.** When a confidential-tier daemon's local provider fails, ever fall back to a third-party API? Or always pause?

**Choices:**
- **(a) Always pause.** Hard rule. Daemon waits; host re-binds endpoint.
- **(b) Fall back after N minutes.** Compromises the confidentiality guarantee.

**Recommendation: (a).** "Local provider unavailable for >5min" is not a justification — the confidential-tier promise is non-negotiable.

### Q-priv-4 — `add_canon_from_path` extraction sequencing (from `docs/design-notes/2026-04-18-add-canon-from-path-sensitivity.md` §5)

**Framing.** The note recommends extracting `add_canon_from_path` as its own MCP tool with `destructiveHint=True` for always-allow carve-out (~0.5 dev-day). Should it ship as part of #11 MCP-split phase M1, or standalone now?

**Choices:**
- **(a) Standalone now.** Ships immediately; doesn't wait on engine/domain separation.
- **(b) Bundle into #11 M1.** Cleaner architectural fit; later.

**Recommendation: (a).** Per §7.3 of the add_canon note, option-b is viable standalone — it doesn't require #11's engine/domain seam to land first.

---

## Full-platform §11 residual (Q-fp-residual, deferred)

The 11+ §11 Qs beyond v2's Q1-Q3 (Q11-Q34 in the full-platform note's numbering) remain queued but **not promoted to first-class digest entries this round.** Reason: they're a heavy workstream, host bandwidth is finite, and most are sequencing/tactical follow-ups that depend on Q1-Q3's answers landing first. Recommend addressing as a batch in v4 once Q1-Q3 are resolved and we know which §11 sub-tracks the architecture commits to.

(Concern #8 in STATUS.md is the meta-pointer to this queue.)

---

## Cheat sheet for fastest unblock (re-ranked v3, post-Q-host-action-1 resolution)

| If host has time for... | Answer | Unlocks |
|---|---|---|
| **30 seconds** | Q4 only | Refactor execution sequence (R2 bid-package + R7 storage split + R11 runtime + R12 servers + R5 universe_server). Future-feature dispatch stops landing on the wrong shape. |
| **2 minutes** | Q4 + Q-sat-6 | Above + decide if `get_dispatch_evidence` becomes the 6th self-auditing surface (unblocks concern 1 chatbot-verification). |
| **5 minutes** | Q4 + Q-sat-6 + Q5 + Q-priv-1 | Above + Track Q dispatch decision (~7 dev-days) + privacy threat model anchor. |
| **10 minutes** | Q4 + Q5-Q9 + Q-priv-1-4 + Q-sat-6 | Self-auditing tools fully scoped + privacy bilateral asks all answered + dispatch evidence surface decided. |
| **15 minutes** | Above + Q10-Q12 | Layer-3 rename unblocked (~3-4 dev-days). |
| **20 minutes** | Above + first batch of full-platform §11 residual | Tactical track decisions unblocked. |

**Recommend the 5-minute path.** Q4 alone unlocks the entire refactor execution sequence (R2 first canonical commit pre-staged, R7 next pre-stage in flight). Q5-Q9 are tactical (recommendations cover defaults); Q-priv-1 anchors the privacy-spec landing; Q-sat-6 is the highest-leverage net-new surface.

**If host has even less time:** Q4 alone is the most-load-bearing call. Approval means every line of refactor work and every new feature lands on the correct shape; rejection means we know to keep PLAN.md as-is and proceed opportunistically.

**Reminder:** Q-host-action-1 (LLM endpoint bind) was the prior 30-second slot; resolved 2026-04-19. Do NOT resume affected daemons until dev ships #49 — drift risk still open.

---

## Pre-answered recommendations (new working rule, 2026-04-19)

Per host-issued directive ("you reason through ambiguities to a recommendation; lead ratifies; only true intent-level questions go to host"), navigator pre-answers each Q with a confidence level. Lead ratifies HIGH-confidence answers without further check; ratifies MEDIUM-confidence with optional flag-back; **only INTENT-ONLY rows surface to host in the next check-in**.

**Self-audit method:** for each Q, read the recommendation against host signal in (a) memory files (`.claude/projects/.../memory/`), (b) STATUS.md current state, (c) recent host messages, (d) PLAN.md. If recommendation contradicts any of these, escalate as INTENT-ONLY. If no contradiction and the call is technical/derivable, mark HIGH. If no contradiction but the call has irreducible taste/pace/priority component, mark INTENT-ONLY.

| Q | Pre-answer | Confidence | Rationale + host-signal cross-check |
|---|---|---|---|
| **Q4 PLAN.md.draft** | **(a) approve as-is** | HIGH | Module Layout derived from spaghetti audit (which integrated codex's modularity audit). Naming (`tinyassets/bid/`, `tinyassets/servers/`) matches existing module names + the rebrand to "TinyAssets Server" per `project_daemon_product_voice.md`. Self-auditing-tools principle is documentation of patterns already shipped (`get_status` task #88). Engine/domain seam aligns with #11 design note already in host-review. Zero contradictions; reversible commitment; deferral cost > approval cost. |
| **Q1 Postgres-canonical** | **(a) Postgres canonical, GitHub mirror** | HIGH | Host memory `project_full_platform_target.md` requires "thousands concurrent, full node collaboration with zero daemons hosted." GitHub-canonical cannot satisfy this without a custom realtime layer (effectively rebuilding Supabase Realtime). Postgres-canonical is the only shape that meets the explicit requirement. *Note: this is a one-way door, but it's a one-way door already-implied by host's explicit scale requirement.* Not a 50/50 taste call. |
| **Q2 Pre-launch load-test** | **(a) ship pre-launch (~1.5 dev-days)** | HIGH | Host memory `feedback_always_install_ready.md` ("Main is downloadable release at all times; broken install is production bug") + `project_distribution_horizon.md` (viral spread possible any day) both make launch-day failure unrecoverable. 1.5 dev-days against unrecoverable risk is asymmetric in favor of ship. |
| **Q3 Fly.io** | **(a) defer Fly entirely** | HIGH | Q1's Supabase pick supersedes Fly. Host memory `project_godaddy_hosting.md` ("prefer existing infra over new vendors") consistent with not adding a new vendor when an existing one (Supabase) covers the surface. No host signal favoring Fly. |
| **Q5 Track Q standalone** | **(a) Track Q standalone** | HIGH | Self-auditing tools serve trust (user-facing, tier-1+2); evaluation-layers serve quality (system-facing). Different consumers, different motivations, different MVP scopes. Mixing them confuses both. Pure architectural call; no taste component. |
| **Q6 Implementation order** | **(b) impl-ease** | HIGH | The pattern is novel; getting the *first* one right matters more than tackling the highest-stakes surface first. `get_status` already proved out the shape for routing; extending to memory-scope is the smallest delta. Pure engineering discipline call; no taste component. |
| **Q7 Caveat authoring** | **(c) hybrid** | HIGH | (b) gets the structural property (drift resistance); (a) preserves narrative quality where it matters. (c) is the dominant pattern in the codebase already (typed schema + selective overrides). No host signal contradicts. |
| **Q8 Bundle dry-inspect** | **(a) bundled into Track Q** | HIGH | Same trust funnel; shipping one without the other leaves a gap exactly where Devin Session 2 succeeded. Per Devin Session 2 §3+§4 — the load-bearing claim is that current-state evidence + prospective-behavior evidence together form the trust loop. Splitting them defeats the loop. |
| **Q9 Audit-log retention** | **(b) 7-day TTL** | MEDIUM | Recommendation balances trust property against privacy footprint. Host has not signaled on retention specifically; could plausibly argue for (a) cheap-in-memory or (c) indefinite-with-opt-out. (b) is the safe-middle pick; **lead can ratify and dev defaults to it**. If a tier-2 host complaints later, revisit. |
| **Q-sat-6 `get_dispatch_evidence`** | **(a) 6th surface in Track Q** | HIGH | Closes Mission 26 #B5 + concern 1 chatbot-verification gap. Same chain-break shape as `get_status` closing LIVE-F8. Per the self-auditing-tools §4 surface list, this is the 6th canonical surface; adding it preserves pattern consistency. (b) folding into `get_status` obscures the pattern. (c) defer leaves known-unverifiable concern in place. |
| **Q10 Module rename name** | **(a) `tinyassets/workflow_server.py`** | HIGH | Brand match to "TinyAssets Server" rebrand. (b) collides with existing `packaging/.../runtime/server.py`. (c) needs verification we shouldn't bother with. Pure naming-mechanics call; no taste component beyond brand-match (which is host-directed). |
| **Q11 Compat flag scheme** | **(a) two independent flags** | HIGH | Author→Daemon rename is at Phase 1.5 with long bake horizon ahead; universe→workflow rename just starting. Tying their flip cadences forces one to delay or rush. Pure engineering call. |
| **Q12 Plugin-dir migration** | **(a) hard cutover + migration script + v0.2.0 release notes** | HIGH | Per `project_distribution_horizon.md` (small early install base), one-time reinstall friction with migration script is cheaper than parallel-name bridge that risks duplicate-plugin UI bug. If install base grows past ~10 hosts before this lands, revisit (b) — but that's a future trigger, not a current call. |
| **Q-priv-1 Threat model** | **(a) honest operator + breach risk** | HIGH (nav-defaulted, host signal-derived 2026-04-19) | **Updated:** Host did not directly answer this Q but surfaced general posture (per lead 2026-04-19): "disposable prototypes, iterate on understanding, don't over-engineer for hypotheticals." Navigator defaults to (a) per that posture. Privacy track CAN still ship local-LLM support for users who want adversary-posture defense — it becomes an **opt-in primitive** (per Q-priv-3 always-pause + tier-2 confidential routing) rather than the default architectural stance. (a) covers the platform-default; opt-in (b) covers the strict-privacy case. No further host check needed unless a tier-2 host explicitly demands strict-adversary architecture. |
| **Q-priv-2 Metadata leakage** | **(a) only content sensitive** for MVP | HIGH | Defer (b) until host explicitly asks; this is a "build later if needed" call rather than an architectural commitment. No host signal contradicts. |
| **Q-priv-3 Third-party fallback** | **(a) always pause** | HIGH | The confidential-tier promise is non-negotiable per `project_privacy_per_piece_chatbot_judged.md` + the entire trust-acquisition framing of Devin Session 2. (b) compromises the guarantee silently — worse failure mode than pause. Hard Rule 8 ("fail loudly") applies. |
| **Q-priv-4 add_canon_from_path extraction** | **(a) standalone now** | HIGH | Per `docs/design-notes/2026-04-18-add-canon-from-path-sensitivity.md` §7.3, option-b is viable standalone. Doesn't require #11 engine/domain seam. Smaller commit window; no architectural dependency to wait on. |

**Self-audit summary (updated 2026-04-19 post-host-default-derivation):**
- **HIGH (13 of 13):** Q4, Q1, Q2, Q3, Q5, Q6, Q7, Q8, Q-sat-6, Q10, Q11, Q12, Q-priv-1, Q-priv-2, Q-priv-3, Q-priv-4. Lead can ratify all without further host check.
- **MEDIUM (1):** Q9 (audit-log retention). Default dev to (b) 7-day TTL; revisit only if tier-2 friction surfaces.
- **INTENT-ONLY (0):** Previously Q-priv-1; now defaulted (a) per host's general "disposable prototypes, iterate on understanding" posture surfaced 2026-04-19. Privacy track ships opt-in adversary-posture defense (via Q-priv-3 always-pause) for users who want it; platform default is (a) honest-operator threat model.

**Effective host load: 0 questions.** All 13 v3 Qs ratify-by-lead and dispatch immediately. **Combined with v4 (2 INTENT-ONLY: §11 Q8 distribution-horizon levers + §11 Q21-nav connector-catalog research ownership), total host load across both digests = 2 questions** instead of 28 raw entries.

---

## What this draft does NOT decide

- Whether digest v3 promotes immediately or waits a longer cycle. (Lead decides at promotion time.)
- The 11+ Q-fp-residual full-platform §11 questions (Q11-Q34 in the full-platform note). Same pre-answering pass should apply to those once Q1-Q3 ratify, but those are deferred to v4 per §"Full-platform §11 residual" above.
- Whether to fold STATUS.md concern-trim recommendation (from `2026-04-19-concerns-post-session.md (archived in git)` §5) into the same conversation. (Lead decides per host-managed concerns rule.)
