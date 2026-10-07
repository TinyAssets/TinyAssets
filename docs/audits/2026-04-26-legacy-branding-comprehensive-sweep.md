---
title: Legacy branding comprehensive sweep — every artifact teaching old vocabulary
date: 2026-04-26
author: navigator
status: read-only discovery audit — host curates dispatch
companion:
  - docs/audits/2026-04-27-project-wide-shim-audit.md (5-arc shim ledger; Arcs A/B/C/D/E address code-layer; this audit catches the doc/comment/instruction layer)
  - docs/exec-plans/active/2026-04-26-decomp-arc-b-prep.md (Arc B = code rename; this audit complements it)
  - docs/exec-plans/active/2026-04-26-decomp-arc-c-prep.md (Arc C = env-var rename; this audit complements it)
  - docs/design-notes/2026-04-27-author-server-db-filename-migration.md (Phase 6 = data rename; this audit complements it)
  - feedback_no_shims_ever (host directive 2026-04-27)
load-bearing-question: Where does a future contributor or future-self learn the wrong vocabulary, the wrong file paths, the wrong env vars, the wrong architecture story — and which fixes are dispatch-ready vs annotation-only?
audience: lead, host
---

# Legacy branding comprehensive sweep

## TL;DR

Found **~3,200 distinct legacy-branding references** across the canonical tree. Per host directive 2026-04-27 ("don't unplug, button up the edges"), the cleanup posture is **retire-with-stamp by default**, not rewrite, not delete.

| Classification | Sites | Action | Dispatch ready |
|---|---|---|---|
| **HISTORICAL CONTEXT** — content discussing the rename arc itself, audit docs, exec-plans, design notes about the migration | ~1,800 | Leave alone (paper-trail is a feature). Optional 1-line annotation where confusion is plausible. | Optional |
| **LIVE INSTRUCTION (RETIRE-WITH-STAMP)** — superseded architecture docs that compete with PLAN.md as a source of truth | ~7 doc files | Add archive-stamp header at top; optionally move to `docs/historical/`. Do not rewrite content; do not delete. | YES |
| **LIVE INSTRUCTION (REWRITE)** — chatbot-facing surfaces with vocabulary-hygiene contract violations (skills, user-facing docs, deploy README) | ~50 sites across ~15 files | Replace "Universe Server" → "TinyAssets MCP server" / "the daemon"; update "author-server DB" prose | YES |
| **SHIPPED-PHASE ARTIFACTS** — phase-named docs/tests/identifiers surviving past the phase | 17 files (5 spec preflights + 11 test names + 5 module docstrings) | Docs: retire-with-stamp. Tests: rename file (test logic stays). Module docstrings: rewrite opener. | YES |
| **DELETE-WITH-ARC-B-PHASE-3** — caller migration sites scheduled for the in-flight rename arc | ~600 | No action — rename arcs handle this | NO (already scoped) |
| **OTHER** — packaging mirrors (auto-rebuild), generated `output/*`, test fixture string literals (intentional), `.claude/agent-memory` (session context), pre-commit invariant scripts | ~550 | Skip / no action | NO |

**Headlines:**
1. **7 top-level architecture/restructure docs at the repo root retire-with-stamp.** They predate every major rewrite (universe→workflow, author→daemon, storage-package split, universe_server.py decomp) and compete with `PLAN.md` as a source of truth. Per host directive 2026-04-27, **default = retire-with-stamp** (preserves git/decision history; kills the "competes with PLAN.md" failure mode). Exception: any doc whose subject was ABANDONED rather than superseded → flag as DELETE-CANDIDATE for host routing.
2. **`Universe Server` brand mentions in user-facing surfaces are a real bug class, not naming drift.** Confirmed via `tests/test_vocabulary_hygiene.py` regression contract. Skills (`*/skills/ui-test/SKILL.md`), `docs/conway_readiness_strategy.md`, deploy/architecture-facing prose teach future agents the wrong vocabulary. **Bucket as LIVE INSTRUCTION (REWRITE)**.
3. **17 shipped-phase artifacts** carry phase identifiers (Phase 7, Phase D/E/F/G/H, Phase 7.4) that no longer organize the work. Per AGENTS.md "Full-platform architecture supersedes phased plan." Mixed dispatch: docs → retire-with-stamp; tests → rename; module docstrings → rewrite.

**Recommended dispatch sequence:** (1) §2.1 retire-with-stamp top-level docs, (2) §2.2 + §2.7-bis vocabulary-hygiene cleanup (skills + canonical user-facing docs), (3) §2.4-bis shipped-phase artifacts, (4) §2.3 strategy/spec doc 2-pass classify, (5) gated cleanups (deploy runbooks, pyproject.toml — post Arc B/C/Phase 6).

---

## 1. Methodology

### Search vocabulary

- Module/symbol legacy: `workflow.author_server`, `author_server.py`, `tinyassets/author_server`, `fantasy_author/`, `domains/fantasy_author/`, `FantasyAuthorDomain` class
- Env vars: `UNIVERSE_SERVER_BASE`, `WIKI_PATH`, `TINYASSETS_AUTHOR_RENAME_COMPAT`
- File / data: `.author_server.db`
- Brand: "Universe Server" / "Workflow Universe Server" (per `tests/test_vocabulary_hygiene.py` this is INTERNAL JARGON to suppress in chatbot-facing surfaces)
- Phased naming surviving past phases: `phase7_`, `phase_h`, `phase_e`, etc.

### Search surfaces

- All `.md`, `.py` (docstrings + comments), `.yml`, `.yaml`, `.json`, `.toml`, `.sh`, `.ps1`, `.env*` in canonical tree
- AGENTS.md, PLAN.md, STATUS.md, CLAUDE.md, README.md, INDEX.md, CONTRIBUTING.md
- `.github/workflows/`, `deploy/`, `packaging/`
- `.claude/skills/`, `.agents/skills/`, `.codex/skills/`

### Excluded by design

| Surface | Why |
|---|---|
| `.claude/worktrees/*` | Separate worktree branches, not main |
| `.claude/agent-memory/*` | Per-agent persistent memory; session context, not contributor-facing |
| `__pycache__/`, `.git/`, `node_modules/` | Generated/internal |
| `output/*` | Generated artifacts: chat traces (claude_chat_trace.md = 106 "Universe Server" refs from session logs), per-universe state, sandbox runs |
| `fantasy_author_original/` | Pre-rename snapshot, deletion-pending |
| `.tmp-*` | Transient working files |
| `tests/test_pre_commit_invariant_author_server.py` | Test fixture literals are intentional (testing the invariant that BLOCKS new uses) |
| `tests/test_vocabulary_hygiene.py` | Tests the hygiene contract; literals are intentional |

### Counts (calibrated by classification)

Per host directive 2026-04-27: counts framing matters. Three relevant classes:

| Term | Raw count | Paper-trail (HISTORICAL CONTEXT) | Dispatch-volume (DELETE-WITH-ARC) | Hygiene-surface (LIVE INSTRUCTION) |
|---|---|---|---|---|
| `.author_server.db` | 58 | ~38 (audit/design-note context) | 7 (`storage/__init__.py:47` + 4 plugin mirrors + 2 doc references gated on Phase 6) | 0 (waiting on Phase 6) |
| `workflow.author_server` / `author_server.py` | 263 | **~210 (paper-trail of the rename arc — GOOD that this exists)** | ~50 (caller migration sites scheduled for Arc B Phase 3) | ~3 (active prose in `docs/planning/daemon_task_economy.md` etc) |
| `fantasy_author` (any) | 913 | ~120 (rename arc paper-trail in audits/exec-plans) | **~600 (Arc B Phase 2 will cut 50%+ of this — primarily test caller migration)** | ~190 (canonical docs + skills + tooling) |
| `UNIVERSE_SERVER_BASE` | 253 | ~50 (Arc C exec-plan + audit refs) | ~190 (Arc C Phase 2/3 deletion) | ~10 (deploy README + AGENTS.md deprecation rows — currently correct, will be deleted) |
| `WIKI_PATH` | 106 | ~30 (rename arc + BUG-002 docs) | ~70 (Arc C Phase 2/3) | ~5 (deploy/CI defensive scrubs — KEEP, not legacy) |
| `Universe Server` brand | 360 raw | ~10 (this audit + sibling audits + chat-intelligence reports) | 0 (no rename arc handles this) | **121 (canonical-tree user-facing — THE main rewrite dispatch per §2.2)** |

**Reading the table:**
- **Paper-trail counts are GOOD** (~390 total). They prove the rename arc has a record. Don't try to "clean them up."
- **Dispatch-volume counts (~910 total) are scoped by Arc B/C/Phase 6.** Already in flight; not new dispatch from this audit.
- **Hygiene-surface counts (~330 total) are THIS AUDIT's dispatch.** ~190 fantasy_author canonical-docs + 121 Universe Server canonical-user-facing + smaller tails.

**Highest-leverage dispatch targeting (per host's "weight = priority" framing):**

For `fantasy_author` rewrite (excluding test files which Arc B Phase 2 handles):
- `IMPORT_COMPATIBILITY.md` (33 refs) — folds into §2.1 retire-with-stamp
- `docs/plan_revalidation.md` (26 refs) — candidate for retire-with-stamp
- `docs/audits/2026-04-25-fantasy-shim-import-audit.md` (24 refs) — HISTORICAL CONTEXT (audit doc)
- `docs/specs/phase_d_preflight.md` (20 refs) — folds into §2.4-bis retire-with-stamp
- `docs/exec-plans/active/2026-04-26-engine-domain-coupling-inventory.md` (22 refs) — HISTORICAL CONTEXT (exec-plan tracking the rename)

Most "high-weight" doc files are either (a) folding into retire-with-stamp dispatches (§2.1, §2.4-bis) or (b) HISTORICAL CONTEXT (audit doc) — net new rewrite dispatch volume from §2.3 strategy/spec is much smaller than the raw 913 count suggests.

For `Universe Server` rewrite (the real bug class per §2.2):
- `.agents/skills/ui-test/SKILL.md` (6 refs, mirrors carry equal load) — HIGHEST LEVERAGE per `tests/test_vocabulary_hygiene.py` contract
- `docs/conway_readiness_strategy.md` (8 refs) — strategy-doc visibility
- Long tail of 2-4 ref docs across docs/specs/, docs/research/, docs/design-notes/, docs/planning/

---

## 2. LIVE INSTRUCTION bucket — dispatch-ready cleanup targets

### 2.1 Top-level repo-root architecture docs — RETIRE-WITH-STAMP (host directive 2026-04-27)

**Per host directive 2026-04-27** ("don't unplug, button up the edges"):

> **Default for these 7 docs = retire-with-stamp**, not rewrite, not delete. Add a one-block header at top:
> ```
> > **HISTORICAL — superseded.** This doc captured architecture intent as of <date>. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.
> ```
> That preserves the history (host's "thorough" rule + project's "no destructive ops") while killing the "competes with PLAN.md" failure mode.
>
> Exception: if a doc's content describes something that NEVER landed and was abandoned (not just superseded), classify as DELETE-CANDIDATE in the audit and surface to lead — host routes the call.

**The 7 docs:** Each is at the repo root. They predate every major rewrite (universe→workflow, author→daemon, storage-package split, universe_server.py decomp) and treat themselves as "the" architecture doc — competes with `PLAN.md` as a source of truth.

| File | Date stamp | Subject | Status |
|---|---|---|---|
| `ARCHITECTURE_PLAN.md` | 2026-04-02 (reframed 2026-04-11) | "Living reference — reflects what exists today" | **RETIRE-WITH-STAMP** — superseded by PLAN.md |
| `BUILD_PREP.md` | 2026-03-31 | "Companion to ARCHITECTURE_PLAN.md" — implementation guide for the Workflow extraction | **RETIRE-WITH-STAMP** — extraction shipped |
| `RESTRUCTURE_PLAN.md` | 2026-04-05 | "Detailed phase specs for Workflow Extraction" | **RETIRE-WITH-STAMP** — extraction shipped |
| `IMPORT_COMPATIBILITY.md` | (no date) | Describes `fantasy_author/` as live transitional package | **RETIRE-WITH-STAMP** — `fantasy_author/` is now a 2-file alias module pending Arc B Phase 3 deletion |
| `PHASE_3_5_6_IMPLEMENTATION.md` | 2026-04-06 | Phase 3.5/3.6 done snapshot | **RETIRE-WITH-STAMP** — work shipped; commit history is the record |
| `IMPLEMENTATION_SUMMARY_PHASE_3.md` | 2026-04-06 | Phase 3.1/3.2 done snapshot | **RETIRE-WITH-STAMP** — same |
| `AGENTIC_SEARCH_RESEARCH.md` | 2026-04-06 | Research compiled for Workflow Engine | **RETIRE-WITH-STAMP** — kept as research record; PLAN.md is the implementation truth |

**Exact header text to apply** (verbatim from lead directive):

```
> **HISTORICAL — superseded.** This doc captured architecture intent as of <date>. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.
```

`<date>` = the doc's existing date stamp (preserve exactly as it appears at the top).

**Dispatch shape:** one dev-2 task. Per file: prepend the header block (with file's existing date in the placeholder), do NOT touch any content below the header, do NOT move to `docs/historical/` (defer that to a separate housekeeping pass — keep churn minimal). Effort: ~30-45 min.

**DELETE-CANDIDATE flag check (per exception clause):** Reviewed each doc against the "never-landed-and-abandoned" criterion.
- `ARCHITECTURE_PLAN.md` — describes the actual as-built. Current code is the descendant. NOT abandoned. Retire-with-stamp.
- `BUILD_PREP.md` — describes the implementation that landed. NOT abandoned. Retire-with-stamp.
- `RESTRUCTURE_PLAN.md` — extraction shipped per `pyproject.toml` package list (`fantasy_author`, `workflow`, `domains`). NOT abandoned. Retire-with-stamp.
- `IMPORT_COMPATIBILITY.md` — every import path it describes still resolves (via shims) and is being unwound by Arc B. NOT abandoned. Retire-with-stamp.
- `PHASE_3_5_6_IMPLEMENTATION.md` — work landed; tests for Phase 3.5/3.6 still in tests/. NOT abandoned. Retire-with-stamp.
- `IMPLEMENTATION_SUMMARY_PHASE_3.md` — same.
- `AGENTIC_SEARCH_RESEARCH.md` — research, not implementation; conclusions informed `tinyassets/retrieval/agentic_search.py` etc. NOT abandoned. Retire-with-stamp.

**Result: zero DELETE-CANDIDATEs.** All 7 retire-with-stamp.

### 2.2 `Universe Server` brand hygiene — LOAD-BEARING (real bug class, not naming drift)

**Per host directive 2026-04-27 + `tests/test_vocabulary_hygiene.py` regression contract:** "Universe Server" appearing in chatbot-facing surfaces is a real bug class — chatbot memory absorbs it from any reachable surface and leaks it back into user conversations (LIVE-F7 from Devin Session 1). Every site is LIVE INSTRUCTION cleanup, not naming drift.

**Calibrated count:** 121 actionable refs in canonical-tree user-facing surfaces (after IGNORE-list filter — `output/*` chat traces 130, `.claude/agent-memory/*` persona memories, worktrees, `tests/test_vocabulary_hygiene.py` fixture intentional, `tests/test_pre_commit_invariant_*` fixture intentional).

| File | Refs | Surface class | Action |
|---|---|---|---|
| **Skills (HIGHEST LEVERAGE — chatbot-vendor agents read these)** | | | |
| `.agents/skills/ui-test/SKILL.md` | 6 | Canonical skill source | **REWRITE** — "TinyAssets MCP server" / "the daemon" |
| `.claude/skills/ui-test/SKILL.md` | 6 | Auto-mirror via `scripts/sync-skills.ps1` | Auto-syncs from `.agents/` |
| `.codex/skills/ui-test/SKILL.md` | 5 | Codex mirror | Edit + sync |
| **User-facing strategy/research docs** | | | |
| `docs/conway_readiness_strategy.md` | 8 | Strategy doc visible to contributors | REWRITE |
| `docs/plan_revalidation.md` | 4 | Plan-revalidation memo | REWRITE (or fold into §2.3 retire-with-stamp if memo is one-shot) |
| `docs/specs/tool_return_shapes.md` | 3 | Active spec | REWRITE |
| `docs/research/github_as_catalog.md` | 3 | Research doc | REWRITE (or retire-with-stamp if Phase 7 closed it out) |
| `docs/reality_audit.md` | 3 | Audit doc — historical | LEAVE (HISTORICAL CONTEXT) |
| `docs/planning_sweep_2026-04-08.md` | 3 | Planning sweep memo | retire-with-stamp |
| `docs/planning/goal_pool_conventions.md` | 3 | Active planning | REWRITE |
| `docs/design-notes/2026-04-20-wiki-bug-reports-convention.md` | 3 | Active design note | REWRITE |
| `docs/research/claude_ai_rendering_behaviors.md` | 2 | Research doc | REWRITE |
| `docs/phase7_repo_setup.md` | 2 | Phase 7 doc | retire-with-stamp (gate on R7 close) |
| `docs/design-notes/2026-04-20-wiki-bug-reports-seed-entries.md` | 2 | Active design note | REWRITE |
| `docs/design-notes/2026-04-19-wiki-known-issues-convention.md` | 2 | Active design note | REWRITE (BUG-002 ref is technical, can stay; brand mention is the issue) |
| `docs/design-notes/2026-04-19-universe-to-tinyassets-server-rename.md` | 2 | Rename arc doc | LEAVE (HISTORICAL CONTEXT — describes the rename) |
| `AGENTIC_SEARCH_RESEARCH.md` | 2 | Top-level research doc | Folds into §2.1 retire-with-stamp |
| **Canonical Python surfaces (sleep tracking — flag any UX strings)** | | | |
| `fantasy_daemon/__main__.py` | 4 | CLI entry | Inspect — likely product-voice strings; REWRITE |
| `tests/test_vocabulary_hygiene.py` | 5 | Regression test fixture | LEAVE (intentional — testing the contract) |
| `tests/test_claude_chat_inline_dismiss.py` | 3 | Test fixture | Inspect — may be intentional |
| `tests/test_universe_server_telemetry.py` | 1 | Test naming (covered by B.1) | RENAME with phase test pass |
| `tests/test_universe_server_framing.py` | 1 | Test naming | RENAME |
| `tests/test_branch_runner.py` | renamed 2026-05-02 from `test_community_branches_phase3.py` | Test fixture | Clean |
| `tests/test_branch_authoring_actions.py` | renamed 2026-05-02 from `test_community_branches_phase2.py` | Test fixture | Clean |
| `scripts/claude_chat.py` | 1 | User-sim script | REWRITE |
| `scripts/always_allow_watch.py` | 3 | Test/ops script | Inspect — may be intentional UI text |
| `docs/audits/user-chat-intelligence/2026-04-19-devin-session1.md` | 4 | Historical session intelligence | LEAVE (HISTORICAL CONTEXT) |
| `docs/specs/phase_h_preflight.md` | 1 | Folds into §2.4-bis | retire-with-stamp |
| **This audit + sibling audits (legitimate)** | | | |
| `docs/audits/2026-04-26-legacy-branding-comprehensive-sweep.md` | 12 | This audit | LEAVE (HISTORICAL CONTEXT — discussing the term) |

**Dispatch shape:** This is **THE main rewrite dispatch** of the audit.
- **Phase 1 (immediate, ~30 min):** Skills (`.agents/skills/ui-test/SKILL.md` source edit + sync via `scripts/sync-skills.ps1`). HIGHEST LEVERAGE.
- **Phase 2 (~1-2h):** User-facing canonical docs (conway_readiness_strategy.md + active design notes + active planning + active specs).
- **Phase 3 (~30 min):** Canonical Python UX strings (`fantasy_daemon/__main__.py` + `scripts/claude_chat.py` + always_allow_watch.py product-voice strings).
- **Phase 4 (gated):** files that fold into §2.1 retire-with-stamp (AGENTIC_SEARCH_RESEARCH.md) or §2.4-bis (phase test names).

**Why this matters:** the test_vocabulary_hygiene.py regression contract names exactly this class of bug. Every "Universe Server" mention reachable by Claude memory is a future LIVE-F7 incident waiting to happen. Skills are especially load-bearing — they teach future agents the wrong vocabulary.

### 2.3 Strategy / planning docs in `docs/`

| File | Refs | Recommended action |
|---|---|---|
| `docs/conway_readiness_strategy.md` | 8 "Universe Server" refs | Rewrite to "TinyAssets MCP server" / "the daemon" — strategy doc visible to contributors |
| `docs/plan_revalidation.md` | 26 fantasy_author + 4 "Universe Server" + 2 `.author_server.db` | Likely a one-shot plan-revalidation memo; check if still active or candidate for `docs/historical/` |
| `docs/reality_audit.md` | 12 fantasy_author refs | Same — point-in-time audit doc |
| `docs/planning/daemon_task_economy.md` | 15 fantasy_author + 4 author_server | Active planning doc — needs canonical rewrite |
| `docs/exec-plans/daemon_task_economy_rollout.md` | 11 fantasy_author + 3 author_server | Active rollout plan — needs canonical rewrite |
| `docs/specs/multi-provider-tray-runtime.md` | 10 fantasy_author refs | Spec doc — needs canonical rewrite |
| `docs/specs/community_branches_phase5.md` | 3 author_server + N fantasy_author | Phase 5 spec — check if shipped (likely yes) |
| `docs/specs/outcome_gates_phase6.md` | 3 author_server | Phase 6 outcome-gates spec |
| `docs/specs/taskproducer_phase_c.md` | 12 fantasy_author refs | Phase C spec |
| `docs/specs/phase_d_preflight.md` | 20 fantasy_author refs | Phase D preflight |
| `docs/specs/phase_h_preflight.md` | 71 phase refs (some N/A; 20+ legacy) | Phase H preflight |
| `docs/specs/phase_e_preflight.md` | 62 phase refs | Phase E preflight |
| `docs/specs/phase_f_preflight.md` | 42 phase refs | Phase F preflight |
| `docs/specs/phase_g_preflight.md` | 19 phase refs | Phase G preflight |

**Dispatch shape:** **NOT one task.** Two passes:
- Pass A (mechanical): scan each, classify ARCHIVED-MOVE-TO-HISTORICAL vs LIVE-NEEDS-REWRITE. ~1h.
- Pass B (per-doc rewrite): for LIVE docs, edit to canonical names. ~2-3h depending on count.

Most likely outcome: 50-70% of these are post-shipped phase summaries that should move to `docs/historical/`; 30-50% are still-active planning that needs in-place rewrite.

### 2.4 `docs/planning/phase7*.md` and `docs/research/phase7_*.md`

Phase 7 (storage-package split) shipped per STATUS Concerns and exec-plans. The `docs/planning/phase7_2_git_bridge_scope.md`, `phase7_3_cutover_scope.md`, `phase7_4_identity_wiring_scope.md`, `phase7_reconciliation.md` and `docs/research/phase7_*` files are PRE-PHASE-7 PLANNING docs.

Per `docs/exec-plans/active/2026-04-19-r7a-phase7-to-catalog.md` and STATUS row "R7 storage-split status confirmation," Phase 7 is host-decision-pending close-out. **Recommend:** don't apply retire-with-stamp YET — host wants R7 closure confirmed first. After R7 confirms, retire-with-stamp all `phase7_*.md` files (folds into §2.4-bis dispatch).

### 2.4-bis Shipped-phase artifacts surviving past their phase (per host directive 2026-04-27)

Per AGENTS.md "Full-platform architecture supersedes phased plan" + host directive 2026-04-27 (split docs from tests):

> **Shipped-phase docs → retire-with-stamp** (same header as §2.1, with date = doc's existing date stamp).
> **Shipped-phase tests → file rename** (test_phase_X_thing.py → test_thing.py). The test logic stays; only the file name (the artifact teaching wrong vocabulary) changes.
> **Shipped-phase module docstrings → rewrite opener** (drop "Phase X — " prefix).

**17 artifacts identified:**

#### A — Spec preflight docs (5 files) — RETIRE-WITH-STAMP

Per STATUS, Phases D / E / F / G / H all shipped. Phase 7 close-pending (gate on R7 confirm).

| File | Phase | Status |
|---|---|---|
| `docs/specs/phase_d_preflight.md` | Phase D | shipped → retire-with-stamp |
| `docs/specs/phase_e_preflight.md` | Phase E | shipped → retire-with-stamp |
| `docs/specs/phase_f_preflight.md` | Phase F | shipped → retire-with-stamp |
| `docs/specs/phase_g_preflight.md` | Phase G | shipped → retire-with-stamp |
| `docs/specs/phase_h_preflight.md` | Phase H | shipped → retire-with-stamp |
| `docs/specs/phase7_github_as_catalog.md` | Phase 7 | gate on R7 close → retire-with-stamp |

#### B — Test file names (11 files) — RENAME (file only; test logic stays)

| Current name | Recommended new name | Phase |
|---|---|---|
| `tests/test_phase7.py` | `tests/test_storage_split.py` (or check what it actually tests) | Phase 7 |
| `tests/test_phase7_h2_goals_cutover.py` | `tests/test_goals_cutover.py` | Phase 7 H2 |
| `tests/test_phase7_h3_branch_cutover.py` | `tests/test_branch_cutover.py` | Phase 7 H3 |
| `tests/test_unified_execution.py` | renamed 2026-05-02 from `test_phase_d_unified_execution.py` | Phase D |
| `tests/test_dispatcher_queue.py` | renamed 2026-05-02 from `test_phase_e_dispatcher.py` | Phase E |
| `tests/test_goal_pool.py` | renamed 2026-05-02 from `test_phase_f_goal_pool.py` | Phase F |
| `tests/test_node_bid.py` | renamed 2026-05-02 from `test_phase_g_node_bid.py` | Phase G |
| `tests/test_activity_log_parity.py` | renamed 2026-05-02 from `test_phase_h_activity_log_parity.py` | Phase H |
| `tests/test_node_bid_claim_stress.py` | renamed 2026-05-02 from `test_phase_h_claim_stress.py` | Phase H |
| `tests/test_daemon_dashboard.py` | renamed 2026-05-02 from `test_phase_h_dashboard.py` | Phase H |
| `tests/test_dashboard_panes.py` | renamed 2026-05-02 from `test_phase_h_panes.py` | Phase H |

**Note:** verify no name collision with existing test files before each rename. Mechanical sed; per-file targeted pytest after rename to confirm green. ~30-45 min.

#### C — Module docstrings (5 modules) — REWRITE OPENER

Per SWEEP 2 addendum heuristic 1:

| Module | Current docstring | Recommended |
|---|---|---|
| `tinyassets/bid/settlements.py` | "Phase G cross-host immutable settlement ledger." | "Cross-host immutable settlement ledger for the paid-market node-bid surface." |
| `tinyassets/branch_tasks.py` | "Phase E durable BranchTask queue." | "Durable BranchTask queue." |
| `tinyassets/catalog/__init__.py` | "Phase 7 — git-native storage backend." | "Git-native catalog backend (branch / node / goal / bid YAML)." |
| `tinyassets/dispatcher.py` | "Phase E tier-aware BranchTask dispatcher." | "Tier-aware BranchTask dispatcher." |
| `tinyassets/identity.py` | "Phase 7.4 v1 — git author identity for commits." | "Git-author identity for commits authored by daemons + users." |

**Note (false positives — KEEP):**
- `tinyassets/retrieval/agentic_search.py` "Phase-aware agentic search policy." — "Phase-aware" describes behavior pattern (story-phase-aware retrieval), not project phase.
- `tinyassets/retrieval/phase_context.py` "Phase-aware retrieval configuration." — same.

#### D — Phase-numbered exec-plans (3 files) — retire-with-stamp at landing

| File | Status |
|---|---|
| `docs/exec-plans/active/2026-04-17-author-rename-phase0-audit.md` | Author rename Phase 0 — superseded by current Arc B/C work |
| `docs/exec-plans/active/2026-04-18-uptime-phase-1a-static-landing.md` | Uptime Phase 1A — landed |
| `docs/exec-plans/active/2026-04-19-r7a-phase7-to-catalog.md` | R7a — host-decision-pending close-out, gates §2.4 above |

**Dispatch shape:** not part of §2.4-bis batch; folds into the broader exec-plans/active/ audit (architecture-edges sweep §D.4).

#### E — Phase-numbered planning docs (4 files) — gated on R7 close

| File | Action |
|---|---|
| `docs/planning/phase7_2_git_bridge_scope.md` | retire-with-stamp post-R7 |
| `docs/planning/phase7_3_cutover_scope.md` | retire-with-stamp post-R7 |
| `docs/planning/phase7_4_identity_wiring_scope.md` | retire-with-stamp post-R7 |
| `docs/planning/phase7_reconciliation.md` | retire-with-stamp post-R7 |

#### Dispatch sequence for §2.4-bis

1. **Phase A — docs retire-with-stamp** (5 spec preflights + AGENTIC_SEARCH_RESEARCH.md folds in §2.1). ~30-45 min, dev-2.
2. **Phase B — test file renames** (11 files). ~30-45 min mechanical + per-file targeted pytest verification. dev-2.
3. **Phase C — module docstring rewrites** (5 modules). ~25 min mechanical. dev-2.
4. **Phase D + E — gated** (R7 close + exec-plans/active/ audit lands first).

Total **§2.4-bis effort: ~1.5-2h** for Phases A+B+C combined.

### 2.5 Deploy / ops runbooks

| File | Line | Issue |
|---|---|---|
| `deploy/RESTORE.md` | 98 | Mentions `.author_server.db` as a specific file to back up. **Keep until Phase 6 lands**, then update to `.workflow.db`. |
| `deploy/README.md` | 114 | "Legacy `$UNIVERSE_SERVER_BASE` (deprecation warning)" — keep as DEPRECATION DOC for one release cycle, then delete with Arc C Phase 3. |
| `docs/ops/cloud-daemon-restart.md` | 151 | Lists `.author_server.db` in expected files. Same — update with Phase 6. |
| `docs/mcpb_packaging.md` | (check) | Check for `UNIVERSE_SERVER_BASE` / `Universe Server` refs |

**Dispatch shape:** updates gated on Arc C / Phase 6 landing. Not a current dispatch — list these in the post-Arc-C / post-Phase-6 doc-update task.

### 2.6 `.github/workflows/deploy-prod.yml` — protective workaround

Lines 127-135: live "Scrub legacy Windows WIKI_PATH env leakage" step. Documents BUG-002 (Windows host → Linux container env-leak). This is **NOT legacy** — it's a defensive workaround for a known live bug class. **KEEP.** Add a 1-line comment cross-referencing `docs/design-notes/2026-04-19-wiki-known-issues-convention.md` BUG-002 if not already there.

### 2.7 Top-level config files

| File | Issue |
|---|---|
| `pyproject.toml:93` | `packages = ["fantasy_author", "workflow", "domains"]` — lists `fantasy_author` as a package. Per Arc B, the `fantasy_author/` package is a 50-LOC alias module scheduled for deletion. **Update post-Arc-B-Phase-3.** |
| `pyproject.toml:98` | `extend-exclude = ["fantasy_author_original"]` — references the pre-rename snapshot. **Delete with `fantasy_author_original/` deletion** (separate housekeeping). |
| `.mcp.json:14` | `WIKI_PATH` env var. **NOT WORKFLOW** — this is a separate `wiki-mcp` Node.js server (different codebase, runs from `C:\\Users\\Jonathan\\Projects\\wiki-mcp\\server.js`). TinyAssets MCP server uses `TINYASSETS_UNIVERSE` (line 7). **No action — this is a different project.** |

### 2.8 Source-of-truth Markdown (AGENTS, README, INDEX, CLAUDE)

| File | Issue |
|---|---|
| `AGENTS.md:270` | `UNIVERSE_SERVER_BASE` "Deprecated. Legacy alias for `TINYASSETS_DATA_DIR`" — KEEP as DEPRECATION DOC; deletes with Arc C Phase 3. |
| `AGENTS.md:274` | `WIKI_PATH` "Deprecated." — same |
| `STATUS.md` | 3 author_server refs — all live (rename arc tracking rows) |
| `README.md` | (check — `grep` did not surface major refs) |
| `INDEX.md` | (check) |
| `CLAUDE.md` | (check — thin routing layer) |

**Dispatch shape:** none for AGENTS/STATUS — they're already correct (current deprecation docs). Audit recommends NO edits.

---

## 3. HISTORICAL CONTEXT bucket — keep + optional annotations

These contain legacy terms because they DOCUMENT the rename arcs. Editing them changes the historical record:

- `docs/audits/2026-04-27-project-wide-shim-audit.md` (11 author_server refs) — audit doc; records what was found pre-cleanup
- `docs/exec-plans/active/2026-04-15-author-to-daemon-rename.md` (16 fantasy_author + 7 author_server refs) — the rename arc itself
- `docs/exec-plans/active/2026-04-19-author-to-daemon-rename-status.md` (22 fantasy_author + 12 author_server refs) — status snapshot of the rename
- `docs/exec-plans/active/2026-04-26-decomp-arc-b-prep.md` (25 fantasy_author + 9 author_server refs) — current Arc B prep, my work
- `docs/exec-plans/active/2026-04-26-decomp-arc-c-prep.md` (26 UNIVERSE_SERVER_BASE + 19 WIKI_PATH refs) — current Arc C prep, my work
- `docs/exec-plans/active/2026-04-26-engine-domain-coupling-inventory.md` (22 fantasy_author refs) — coupling inventory across rename
- `docs/exec-plans/active/2026-04-19-rename-end-state.md` (3 author_server refs) — R7 end-state planning
- `docs/exec-plans/active/2026-04-19-universe-to-workflow-server-rename.md` (~5 refs) — universe→workflow rename arc
- `docs/exec-plans/active/2026-04-17-author-rename-phase0-audit.md` (8 fantasy_author refs) — phase 0 audit
- `docs/audits/2026-04-25-fantasy-shim-import-audit.md` (24 fantasy_author refs) — purpose-built shim audit
- `docs/audits/2026-04-18-rename-tree-consistency-audit.md` (6 author_server refs) — consistency audit
- `docs/audits/2026-04-19-project-folder-spaghetti.md` (4 author_server refs) — pre-cleanup audit
- `docs/audits/2026-04-19-dirty-tree-audit.md` (8 fantasy_author refs) — dirty-tree state
- `docs/design-notes/2026-04-27-author-server-db-filename-migration.md` (19 `.author_server.db` refs) — design note for THE rename. Keep — it explicitly discusses the legacy filename.
- `docs/design-notes/2026-04-19-universe-to-tinyassets-server-rename.md` — rename history
- `docs/design-notes/2026-04-19-wiki-known-issues-convention.md` (3 WIKI_PATH refs) — BUG-002 docs
- `docs/design-notes/2026-04-20-wiki-bug-reports-seed-entries.md` (8 WIKI_PATH refs) — same
- `docs/design-notes/2026-04-22-silent-deploy-damage-class.md` (2 WIKI_PATH refs) — incident class doc
- `docs/design-notes/2026-04-20-deploy-pipe-as-system-failure.md` (2 WIKI_PATH refs) — same
- `docs/audits/2026-04-25-etc-workflow-env-mode-flip.md` — references "Scrub legacy WIKI_PATH" as a step

**Action:** **NONE required.** These docs are doing their job — recording history. Optional 1-line annotation `> Historical record: documents the {rename arc / cleanup} that closed YYYY-MM-DD.` if a future contributor reading them in isolation might mistake them for live instruction.

---

## 4. DELETE-WITH-ARC-B-PHASE-3 bucket — already scoped

Caller migration sites scheduled for in-flight rename arcs. **No action by this audit; tracked elsewhere.**

| Surface | Sites | Tracked in |
|---|---|---|
| `tests/*.py` `from domains.fantasy_author.X import Y` | ~192 sites, 37 files | `docs/exec-plans/active/2026-04-26-decomp-arc-b-prep.md` Phase 2 |
| `tinyassets/api/runs.py:391,889,1236` `from domains.fantasy_author.phases._provider_stub` | 3 sites | Same prep doc (excluded from Arc B Phase 1 dispatch per dev/dev-2 collision boundary) |
| `tests/*.py` `monkeypatch.setenv("UNIVERSE_SERVER_BASE", ...)` | ~135 sites, 60 files | `docs/exec-plans/active/2026-04-26-decomp-arc-c-prep.md` Phase 2 |
| `tests/*.py` `monkeypatch.setenv("WIKI_PATH", ...)` | ~16 sites, 4 files | Same prep doc |
| `tinyassets/storage/__init__.py:210-222, 272-283` resolver branches | 2 blocks | Arc C Phase 3 |
| `tinyassets/_rename_compat.py` + 3 alias modules | 4 files / ~366 LOC | Arc B Phase 3 |
| `.author_server.db` filename + DB_FILENAME constant | ~20 doc/comment refs | Phase 6 |

---

## 5. OTHER bucket — skip / no action

| Surface | Why skip |
|---|---|
| `packaging/dist/tinyassets-universe-server-src/*` | Auto-built distribution mirror — re-generated by `python packaging/claude-plugin/build_plugin.py` after canonical changes |
| `packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/*` | Same — runtime mirror |
| `packaging/mcpb/server.py` (5 `UNIVERSE_SERVER_BASE` refs) | This IS canonical for the MCPB packager surface; updates gated on Arc C Phase 3 |
| `output/claude_chat_trace.md` (106 "Universe Server" refs) | Generated session log — chat artifact |
| `output/user_sim_session.md` (24 refs) | Generated session log |
| `output/*` (any other refs) | Generated artifacts |
| `.claude/agent-memory/*` (1194 fantasy_author refs) | Per-agent persistent memory — session context, not contributor docs |
| `tests/test_pre_commit_invariant_author_server.py` | Test fixture: literals are intentional (testing the invariant blocking new uses) |
| `tests/test_vocabulary_hygiene.py` | Test fixture: literals are intentional |
| `scripts/pre_commit_invariant_author_server.py` | The pre-commit invariant script — literals are the search pattern |
| `scripts/migrate_imports.py` (22 fantasy_author refs) | Migration tooling — references are the migration source |
| `scripts/build_shims.py` (17 fantasy_author refs) | Shim-build tooling — references are intentional (knows about the alias) |

---

## 6. Per-tree summary

| Tree | Total refs | LIVE INSTRUCTION sites | HISTORICAL CONTEXT sites | DELETE-WITH-ARC sites | OTHER |
|---|---|---|---|---|---|
| `/` (top-level docs + configs) | ~250 | 6 doc files (§2.1) + pyproject.toml (§2.7) | — | — | — |
| `AGENTS.md`, `STATUS.md`, `PLAN.md` | ~10 | 0 | All correct (deprecation docs) | — | — |
| `docs/audits/` | ~150 | 0 (all are audit docs) | All audit docs documenting cleanup | — | — |
| `docs/design-notes/` | ~80 | 0 | Almost all historical/design discussion | — | — |
| `docs/exec-plans/` | ~150 | 0 (all rename-arc planning) | All historical/active rename planning | — | — |
| `docs/specs/` | ~250 | All — most are pre-rename spec docs that need rewrite or archive | — | — | — |
| `docs/planning/` | ~80 | 2-3 active planning docs (daemon_task_economy.md, etc.) | Older phase7_* planning awaiting R7 close | — | — |
| `docs/research/` | ~30 | 0 | All historical research | — | — |
| `docs/ops/` | ~10 | `cloud-daemon-restart.md` (post-Phase-6 update) | — | — | — |
| `docs/concerns/` | ~5 | 0 | Historical concerns | — | — |
| `.claude/skills/`, `.agents/skills/`, `.codex/skills/` | ~25 | 17 ("Universe Server" in ui-test SKILL.md per §2.2) | — | — | — |
| `deploy/` | ~5 | `RESTORE.md` (Phase 6 update), `README.md` (Arc C Phase 3 delete) | — | — | — |
| `.github/workflows/` | ~5 | 0 (deploy-prod.yml scrub step is intentional defense) | — | — | — |
| `packaging/mcpb/` | ~10 | 0 | — | All bound to Arc C Phase 3 | — |
| `packaging/claude-plugin/` mirror | ~50 | 0 | — | — | All auto-rebuild |
| `packaging/dist/` mirror | ~50 | 0 | — | — | All auto-rebuild |
| `tinyassets/` (canonical Python) | ~30 | Various docstring/comment refs (§7) | — | All bound to Arc B/C/Phase 6 | — |
| `domains/` | ~15 | 0 | — | All bound to Arc B Phase 3 | — |
| `tests/` | ~600 | 0 | — | All bound to Arc B/C Phase 2 | — |
| `scripts/` | ~80 | 0 | — | — | All migration/invariant tooling — intentional refs |
| `output/` | ~150 | 0 | — | — | Generated artifacts |
| `.claude/agent-memory/` | ~1200 | 0 (per-agent memory) | — | — | Session context |

---

## 7. Workflow Python source — comments/docstrings to update post-rename-arcs

Mostly handled by the rename arcs themselves, but flagging the docstring + comment refs that survive the mechanical sed:

- `tinyassets/api/branches.py` — DB filename comments now reference `.workflow.db`; helper symbol still waits on the evaluation API lane.
- `tinyassets/payments/escrow.py` — docstring: "SQLite table escrow_locks lives in the same .author_server.db as ...". Update with Phase 6.
- `tinyassets/api/engine_helpers.py` — docstring mentions "Tests using `UNIVERSE_SERVER_BASE=<tmp_path>/output`". Update with Arc C Phase 3.
- `tinyassets/api/helpers.py` — docstring mentions legacy alias. Update with Arc C Phase 3.

---

## 8. Recommended dispatch sequence

| Order | Surface | Action | Effort | Dispatch-ready |
|---|---|---|---|---|
| 1 | Top-level docs (§2.1) | Move 6 → `docs/historical/` with archive headers | 1-2h | YES, dev-2 |
| 2 | Skill files (§2.2) | Edit `.agents/skills/ui-test/SKILL.md` + sync | 30 min | YES, dev-2 |
| 3 | Strategy docs (§2.3) | 2-pass classify + rewrite | 3-5h | YES, dev-2 (after dev #18 ships if any tests/* touched) |
| 4 | (gate) Phase 7 planning docs (§2.4) | Move to `docs/historical/` | 1h | NO — wait for R7 close |
| 5 | (gate) Deploy runbooks (§2.5) | Update `.author_server.db` + `UNIVERSE_SERVER_BASE` refs | 30 min | NO — gated on Phase 6 + Arc C Phase 3 |
| 6 | (gate) Top-level config (§2.7) | Update `pyproject.toml` post-Arc-B-Phase-3 | 5 min | NO — gated |
| 7 | Workflow Python docstrings (§7) | Update inline | 30 min | NO — gated on Phase 6 / Arc C Phase 3 |
| 8 | Historical-context annotations (§3) | Optional 1-line "historical record" headers | 1-2h | LOW PRIORITY, defer indefinitely |

**Total LIVE-INSTRUCTION dispatch effort: ~5-8h spread across 3 dispatchable tasks.**

---

## 9. Decision asks for the lead → host

1. **Approve §2.1 dispatch** (move 6 top-level docs to `docs/historical/`)? Recommend yes — this is the highest-leverage cleanup. Concrete archive-header text proposed in §2.1.
2. **Approve §2.2 dispatch** (skill-file vocabulary scrub)? Recommend yes — vocabulary-hygiene contract violation, small task.
3. **Approve §2.3 dispatch** (strategy/spec doc 2-pass classify + rewrite)? Recommend yes, but requires per-doc judgment — propose dispatching to dev-2 with "classify each doc as ARCHIVED-or-LIVE; surface findings; you'll commit only after navigator review" rather than autonomous rewrite.
4. **§2.4 Phase 7 docs** — wait for R7 close-out, OR allow batch-archive now with R7 confirmation in same commit?
5. **AGENTIC_SEARCH_RESEARCH.md fate** (§2.1) — full retire, or keep with date-stamp + cross-link to PLAN.md sections it informed? Recommend keep + date-stamp; it has standalone research value.
6. **`fantasy_author_original/` directory deletion** (referenced by `pyproject.toml:98` extend-exclude). Pre-rename snapshot. Per `docs/audits/2026-04-19-project-folder-spaghetti.md`. Outside this audit's scope but worth flagging — when's the right time?

---

## 10. Cross-references

- `docs/audits/2026-04-27-project-wide-shim-audit.md` — sibling audit (code shim layer, this audit catches doc/comment/instruction layer)
- `docs/exec-plans/active/2026-04-26-decomp-arc-b-prep.md` — Arc B (code rename) scope
- `docs/exec-plans/active/2026-04-26-decomp-arc-c-prep.md` — Arc C (env-var rename) scope
- `docs/design-notes/2026-04-27-author-server-db-filename-migration.md` — Phase 6 (data rename) scope
- `tests/test_vocabulary_hygiene.py` — regression contract for chatbot-facing vocabulary
- `tests/test_pre_commit_invariant_author_server.py` — pre-commit invariant blocking new `workflow.author_server` imports
- `feedback_no_shims_ever` (memory) — host directive driving the 5 cleanup arcs
- `AGENTS.md` — "Architecture truth lives in PLAN.md"
- `PLAN.md` — current canonical architecture
- `STATUS.md` — current cleanup-arc tracking + concerns

---

## Addendum 2026-04-26 — extended vocabulary sweep (per lead directive)

After the audit landed, lead added vocabulary I should also sweep. Re-ran with the extended list. **Verdict: no major new dispatch targets surfaced; canonical tree is cleaner than expected on these axes.**

### A1. DB filename constants (`DB_FILENAME = ".author_server.db"`)

| File | Line | Status |
|---|---|---|
| `tinyassets/storage/__init__.py` | 47 | Canonical — Phase 6 spec target |
| `packaging/claude-plugin/.../runtime/tinyassets/storage/__init__.py` | 47 | Auto-rebuild mirror |
| `packaging/dist/.../tinyassets/storage/__init__.py` | 47 | Auto-rebuild mirror |

**No new finding.** Already scoped by Phase 6 design note.

### A2. Hyphenated `fantasy-author` form

Canonical-tree refs (excluding worktrees + originals): zero in canonical .py / .md / config. Persona memory file (`ilse_marchetti/sessions.md:38` — "fantasy-authoring") is OTHER bucket (agent memory).

### A3. Hyphenated `author-server` form

- Audit/design-note FILE NAMES (e.g. `docs/design-notes/2026-04-27-author-server-db-filename-migration.md`) — INTENTIONAL, HISTORICAL CONTEXT.
- `docs/design-notes/2026-04-27-...migration.md:123` — "author-server data" inside the rejected Option C narrative — HISTORICAL CONTEXT.
- **NEW LIVE-INSTRUCTION FINDING: `docs/planning/daemon_task_economy.md:31`** — "author-server DB" in current planning prose. Should be "TinyAssets daemon DB" or "the SQLite catalog DB." Bundle into §2.3 dispatch.

### A4. Capitalized product voice ("The Author", "Fantasy Author", "Author daemon")

**Canonical tinyassets/ Python code: ZERO matches.** Excellent — chatbot-facing strings have already been scrubbed of capitalized "Author" product voice in canonical code.

Doc references:
- `docs/planning/daemon_task_economy.md:58` — quotes the LEGACY user-facing message "The Author will consider it at the next review gate." HISTORICAL CONTEXT (the doc explains the bug got fixed in commit `590d11a`).
- `docs/exec-plans/active/2026-04-19-steering-md-removal.md:15` — quotes git commit message "Initial commit: Workflow engine + Fantasy Author daemon." HISTORICAL CONTEXT.

**No new dispatch target on this axis.**

### A5. Service / URL / path: `workflow-author*`, `tinyassets-author*`, `/author/`

- `.github/workflows/*.yml` — all use `tinyassets.io` and `tinyassets-mcp-proxy` (Worker name). NO `tinyassets-author*` or `/author/` URL paths. **Clean.**
- `docs/reality_audit.md:87` + `AGENTIC_SEARCH_RESEARCH.md:267` — "session/author/branch/runtime/ledger" as memory-scope dimension list (legitimate per A6 below), not a service URL.

### A6. Soul / ledger / attribution copy — `author_id` vs `daemon_id`

**KEY FINDING — explicit canonical rule.** `PLAN.md:87` documents:
> "Author" → "daemon" rename in flight; agent-runtime concept is `daemon_id`, content-authorship concept stays `author_id` + `author_kind` discriminator.

So `author_id` is **NOT a rename leftover** — it's the canonical content-authorship field. The MemoryScope / attribution surface uses `author` legitimately (the entity that authored the content), not as the runtime daemon name.

**No action needed on `author_id` / `author_kind` surfaces.** Worth recording so future audits don't try to "fix" it.

### A7. GitHub Actions job names + backup script outputs

**Canonical tree: ZERO matches** for `author-server` / `fantasy-author` / `author_server` in `.github/workflows/*.yml`, `deploy/backup*.sh`, `scripts/backup*.py`. **Clean.**

### A8. Acceptance probe + canary names — `docs/ops/`

- `docs/ops/cloud-daemon-restart.md:151` — `.author_server.db` ref already cataloged in main audit §2.5 (LIVE INSTRUCTION, gated on Phase 6).
- `docs/ops/acceptance-probe-catalog.md` — only `author: navigator` frontmatter. **Clean.**
- All other `docs/ops/*.md` — clean.

### A9. PLAN.md design language

Single match at L87 — and it's the EXPLICIT canonical rule. Working as intended. AGENTS.md / README.md / INDEX.md / CLAUDE.md — zero "Author daemon" / "Author server" matches. **Clean.**

### A10. `pyproject.toml`

Already cataloged in main audit §2.7 (lines 93 + 98). No additional refs.

---

### SWEEP 2 extra heuristics — extended vocabulary edition

#### B1. Modules with docstrings starting "Phase X..." in canonical tinyassets/

| Module | Docstring opening | Phase status |
|---|---|---|
| `tinyassets/bid/settlements.py` | "Phase G cross-host immutable settlement ledger." | Phase G shipped |
| `tinyassets/branch_tasks.py` | "Phase E durable BranchTask queue." | Phase E shipped |
| `tinyassets/catalog/__init__.py` | "Phase 7 — git-native storage backend." | Phase 7 host-decision-pending close-out |
| `tinyassets/dispatcher.py` | "Phase E tier-aware BranchTask dispatcher." | Phase E shipped |
| `tinyassets/identity.py` | "Phase 7.4 v1 — git author identity for commits." | Phase 7.4 shipped |
| `tinyassets/retrieval/agentic_search.py` | "Phase-aware agentic search policy." | "Phase-aware" — describes a behavior pattern, not a project phase. KEEP. |
| `tinyassets/retrieval/phase_context.py` | "Phase-aware retrieval configuration." | Same — KEEP. Module name is also "phase_context" describing the behavior. |

**5 new dispatch targets — module docstrings to rewrite.** Each docstring should describe what the module DOES, not which project phase shipped it. Mechanical edits, ~5 min each = 25 min total. Bundle as one dev-2 task.

Recommendation: dev-2 rewrites each docstring to:
- `tinyassets/bid/settlements.py` → "Cross-host immutable settlement ledger for the paid-market node-bid surface."
- `tinyassets/branch_tasks.py` → "Durable BranchTask queue."
- `tinyassets/catalog/__init__.py` → "Git-native catalog backend (branch / node / goal / bid YAML)."
- `tinyassets/dispatcher.py` → "Tier-aware BranchTask dispatcher."
- `tinyassets/identity.py` → "Git-author identity for commits authored by daemons + users."

#### B2. `from X import *` patterns

**Canonical tree: ZERO matches.** TinyAssets/ + domains/ are clean of grab-bag glob imports. Excellent.

#### B3. util/helpers/common modules

- `tinyassets/api/helpers.py` (142 LOC, 15 callers) — has clear "Public surface (stable contract)" docstring listing 9 helpers: `_base_path`, `_universe_dir`, `_default_universe`, `_read_json`, `_read_text`, `_wiki_root`, `_wiki_pages_dir`, `_wiki_drafts_dir`, `_find_all_pages`. Narrow purpose (path resolvers + safe I/O). **NOT a grab-bag.** No refactor needed.
- `tinyassets/utils/__init__.py` (1 LOC), `tinyassets/utils/json_parsing.py` (70 LOC) — minimal namespace. Clean.

**No grab-bag findings.**

---

### Net additions to dispatch

**6 NEW LIVE-INSTRUCTION findings from addendum:**

1. **A3.1** — `docs/planning/daemon_task_economy.md:31` "author-server DB" → "TinyAssets daemon DB". Bundle into §2.3 strategy/spec doc rewrite dispatch.
2. **B1.1** — `tinyassets/bid/settlements.py` docstring rewrite (drop "Phase G").
3. **B1.2** — `tinyassets/branch_tasks.py` docstring rewrite (drop "Phase E").
4. **B1.3** — `tinyassets/catalog/__init__.py` docstring rewrite (drop "Phase 7").
5. **B1.4** — `tinyassets/dispatcher.py` docstring rewrite (drop "Phase E").
6. **B1.5** — `tinyassets/identity.py` docstring rewrite (drop "Phase 7.4 v1").

Items B1.1-B1.5 bundle as one ~25-min dev-2 dispatch (sed-style docstring edits in 5 files).

### Confidence

Extended-vocab sweep CONFIRMS the original audit's classification. **No missed major cleanup target.** The original audit's recommendations stand. Net new effort: ~30 min additional dev-2 work (5 docstring rewrites + 1 prose ref).
