# Host-Q Digest v2 — Fast Unblock

**Date:** 2026-04-19 (v2 — adds self-auditing-tools §5 Qs + PLAN.md.draft review Q; re-ranked by unblock leverage)
**Author:** navigator (for lead → host)
**Purpose:** Twelve questions across three workstreams, ordered by downstream unblock leverage. Cheat-sheet at bottom maps host time-budget to which Qs to pick.

---

## Framing

Three host-decision queues now sit in front of dispatch:

1. **Full-platform architecture (3 Qs)** — Q1/Q2/Q3 from `docs/design-notes/2026-04-18-full-platform-architecture.md` §11. Unlocks breaking 16 architecture tracks (A-P) into Work rows.
2. **PLAN.md.draft review (1 Q)** — Q4 below. Unlocks the refactor execution sequence (god-module splits + 5-subpackage layout) — every future feature lands on top of whatever shape PLAN.md.draft makes canonical.
3. **Self-auditing-tools pattern (5 Qs)** — Q5-Q9 from `docs/design-notes/2026-04-19-self-auditing-tools.md` §5. Unlocks Track Q (~7 dev-days) for trust-critical tool primitives.
4. **Layer-3 rename (3 Qs)** — Q10-Q12 from `docs/design-notes/2026-04-19-universe-to-tinyassets-server-rename.md` §5. Unlocks tasks #26-#31 (~3-4 dev-days).

**Re-rank rationale (vs v1):** PLAN.md.draft now exists. The refactor sequencing questions matter more than they did at v1 because *the draft presupposes certain answers* — every future feature dispatched before PLAN.md.draft is approved or rejected gets potentially rebuilt. That makes Q4 (PLAN.md.draft review) the **single highest-leverage Q** in the digest, displacing Q1's prior #1 spot. Q1 stays #2 because it gates the entire MVP-build sequence.

---

## The single most-load-bearing Q

### Q4 — PLAN.md.draft: approve, iterate, or reject?

**Framing.** `PLAN.md.draft` proposes three architectural commitments that haven't been canonical before:
1. A **Module Layout** section codifying 5 canonical subpackages (`tinyassets/api/`, `tinyassets/storage/`, `tinyassets/runtime/`, `tinyassets/bid/`, `tinyassets/servers/`) with a migration policy ("flat module > 500 LOC OR overlapping sibling responsibility → gets a subpackage").
2. The **self-auditing-tools** principle as a Cross-Cutting Principle — trust-critical tools include their own caveats (structured evidence + structured caveats + chatbot composes narrative).
3. The **engine/domain seam is named** — every action lives in exactly one of `tinyassets/api/` or `domains/<name>/api/`. No third location.

These are load-bearing for refactor dispatch (hotspots #1-#3 in `2026-04-19-project-folder-spaghetti.md (archived in git)`) AND for the §11 Track Q decision in Q5 below AND for engine/domain separation (#11 design note).

**Choices:**
- **(a) Approve as-is.** Replace PLAN.md with the draft. Refactor dispatch sequence executes immediately (hotspot #3 ship-anytime first; #1 + #2 post-blocker per the audit ranking).
- **(b) Approve with edits.** Specific iteration asks; navigator revises the draft to `PLAN.md.draft.v2`; loops until approval.
- **(c) Reject the Module Layout commitment.** Keep PLAN.md as it is; refactor work proceeds opportunistically without architectural commitment to the 5-subpackage shape. (Risk: future features land in the existing flat namespace and the spaghetti grows.)

**Recommendation: (a).** The Module Layout absorbs the spaghetti audit's 5 target subpackages without overcommitting — every choice in the draft is reversible, and the migration policy is gradient (flat-modules-staying-flat is fine if they don't grow). The principle additions (self-auditing tools + named engine/domain seam) are documentation of patterns the codebase is already moving toward; deferring approval just delays the canonicalization.

**If (b), most likely iteration vectors:** subpackage names (e.g., `tinyassets/market/` instead of `tinyassets/bid/`), or the ~500 LOC migration threshold (could be tighter or looser), or whether `tinyassets/servers/` should be `tinyassets/entrypoints/` to match the integration-shell language.

---

## The full-platform architecture Qs (Q1-Q3 — high leverage)

### Q1 — Postgres-canonical, GitHub-as-export?

**Framing.** This is the one-way-door decision in §4 of the full-platform note. Picking this unblocks the schema, RLS policies, and the entire realtime collaboration layer — without it, the design rebuilds around a different core.

**Choices:**
- **(a) Postgres canonical, GitHub mirror** — Supabase Postgres holds the live state; an hourly Action exports public rows as flat YAML to a `TinyAssets-catalog/` repo; PR ingestion round-trips back. Real-time collab works natively via Supabase Realtime. **One-way door:** reverting to GitHub-canonical post-launch means data migration.
- **(b) GitHub canonical (status quo)** — every state change is a commit; live collab requires custom build-on-top. Realtime presence + concurrent edits are not natively available; we'd build them ourselves.

**Recommendation: (a).** Only shape that satisfies the full-platform requirements (live collab + RLS + presence) without a custom realtime layer. The one-way-door risk is real but cheaper than building Supabase Realtime ourselves.

**Cross-link to Q4:** PLAN.md.draft already flags GitHub-canonical as "under host review (Q1)" not asserted as truth — Q1's answer determines which framing stands.

### Q2 — Pre-launch load-test harness: ship or defer?

**Framing.** §14 of the full-platform note flags the Postgres `SKIP LOCKED` scaling cliff (~128 concurrent workers degrades, ~1k spikes CPU to 100%). Whether to build a load-test harness pre-launch (track J, ~1.5 dev-days) or trust launch-day to surface bugs is the call.

**Choices:**
- **(a) Ship pre-launch (~1.5 dev-days).** Exercise 1k-subscriber fan-out, 500-daemon bid storm, cascade read storm, heartbeat load before users see the system.
- **(b) Defer to post-launch.** Launch day is the load test. Saves ~1.5 dev-days now.

**Recommendation: (a).** Viral-hook positioning means launch-day failure is probably unrecoverable. 1.5 dev-days is cheap insurance.

### Q3 — Fly.io: deferred entirely, or kept as control-reasons fallback?

**Framing.** Under the Supabase plan (§3.2), Fly.io is replaced. Confirm or explicitly prefer Fly + custom realtime.

**Choices:**
- **(a) Defer Fly entirely.** No $5/mo Fly spend. Lock into Supabase as primary stack.
- **(b) Fly + custom realtime.** More dev burden (~weeks added), full control, zero stack lock-in.

**Recommendation: (a).** Postgres is portable — the Supabase escape hatch (lift-and-shift to self-hosted Postgres + Realtime open-source) preserves no-lock-in without paying the build-it-ourselves tax now.

---

## The self-auditing-tools Qs (Q5-Q9 — Track Q dispatch leverage)

From `docs/design-notes/2026-04-19-self-auditing-tools.md` §5. Each Q gates implementation choice for the 5 successor tool surfaces (memory-scope / routing / privacy / autoresearch / moderation).

### Q5 — Track Q standalone or P-extension?

**Framing.** Self-auditing-tools is comparable in unblock value to the §11 high-leverage Qs.

**Choices:**
- **(a) Track Q (new top-level track).** 6-7 dev-day discrete track delivering all 5 instantiations as MVP.
- **(b) P-extension.** Fold into Track P (evaluation-layers unification) since both are observability-shaped.

**Recommendation: (a).** Self-auditing tools serve trust (user-facing); evaluation-layers serve quality (system-facing). Different consumers, different motivations, different MVP scopes.

### Q6 — Implementation order across the 5 surfaces?

**Choices:**
- **(a) Trust-blast-radius:** privacy → memory-scope → routing → moderation → autoresearch.
- **(b) Impl ease:** routing → memory-scope → privacy → autoresearch → moderation. Easiest-first to validate the pattern shape.
- **(c) User-tier sequencing:** routing → memory-scope → privacy → autoresearch → moderation matched to tier rollout.

**Recommendation: (b).** Pattern is novel enough that getting the *first* one right matters more than tackling the highest-stakes surface first. `get_status` already proved the shape for routing; extending to memory-scope is the smallest delta.

### Q7 — Caveat authoring: hand-written, derived, or hybrid?

**Choices:**
- **(a) Hand-written per surface.** Highest quality; highest authoring cost; drift risk.
- **(b) Derived from typed schema** in `tinyassets/protocols.py`. Lower drift; more rigid.
- **(c) Hybrid.** Derived as baseline; hand-written overrides where surface-specific nuance matters.

**Recommendation: (c).** Get structural property from (b); preserve narrative quality where it matters via (a).

### Q8 — Bundle dry-inspect with Track Q, or separate follow-on?

**Choices:**
- **(a) Bundled into Track Q.** Self-auditing covers current state + prospective behavior in one delivery.
- **(b) Separate follow-on.** Adds scope to a track that needs to ship before §11 Q answers settle.

**Recommendation: (a).** Same trust funnel; shipping one without the other leaves a gap exactly where Devin Session 2 succeeded.

### Q9 — Audit-log retention for evidence fields?

**Choices:**
- **(a) Rolling window N=20 in-memory.** Cheap; lost on restart.
- **(b) Persisted with TTL (e.g., 7 days).** DB cost; survives restart; supports retroactive audit.
- **(c) Persisted indefinitely with user opt-out.** Highest trust; highest privacy footprint.

**Recommendation: (b).** Balances trust property against privacy footprint. Tier-2 users who care can opt into longer.

---

## The layer-3 rename Qs (Q10-Q12 — lower leverage, fast)

From `docs/design-notes/2026-04-19-universe-to-tinyassets-server-rename.md` §5. Answering them unlocks tasks #26-#31.

### Q10 — Module rename target name?

**Choices:** `(a) tinyassets/workflow_server.py` — brand match. `(b) tinyassets/server.py` — collides with bootstrap stub. `(c) tinyassets/mcp_server.py` — name may be taken.

**Recommendation: (a).**

### Q11 — Compat-flag scheme: shared or independent flip clocks?

**Choices:** `(a) Two independent flags` — different flip cadence per rename. `(b) One shared flag` — simpler env, forced cadence.

**Recommendation: (a).** Author→Daemon is at Phase 1.5; universe→workflow just starting. Independent.

### Q12 — Plugin-directory rename: hard cutover or parallel-name bridge?

**Choices:** `(a) Hard cutover` + migration script + v0.2.0 release notes. `(b) Parallel-name bridge` for one release.

**Recommendation: (a).** Per `project_distribution_horizon.md`, install base is small. Hard cutover cleanest.

---

## Cheat sheet for fastest unblock (re-ranked v2)

| If host has time for... | Answer | Unlocks |
|---|---|---|
| **30 seconds** | Q4 only | Refactor execution sequence (hotspot #3 ship-anytime first). Future-feature dispatch stops landing on the wrong shape. |
| **2 minutes** | Q4 + Q1 | Above + schema + RLS + realtime layer design (track A, ~2 dev-days). |
| **5 minutes** | Q4 + Q1 + Q2 + Q3 | Above + full MVP track decomposition: A through P (~24-29 dev-days with 2 devs). |
| **8 minutes** | Q4 + Q1-3 + Q5 (Track Q yes/no) | Above + Track Q dispatch decision (~7 dev-days). Q6-Q9 can pick defaults. |
| **12 minutes** | Q1-Q12 | Everything above + layer-3 rename (~3-4 dev-days). |

**Recommend the 5-minute path.** Q4 + Q1-3 unblocks the durable architectural commitment + the entire MVP build sequence. Q5-Q12 are tactical and dev can move on parallel tracks while host catches up.

**If host has even less time:** Q4 alone is the single most-leveraged Q. Approval there means every line of refactor work and every new feature lands on the correct shape; rejection means we know to keep PLAN.md as-is and proceed opportunistically.
