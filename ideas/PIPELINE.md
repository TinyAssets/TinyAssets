# Idea Pipeline

This file turns captured ideas into deliberate outcomes instead of leaving them
as orphaned notes.

## States

- `captured`: recorded in `ideas/INBOX.md`, not yet clarified.
- `triaged`: deduplicated, sized, and given a next home.
- `promoted`: moved into a concrete surface such as `STATUS.md`,
  `docs/design-notes/`, `docs/exec-plans/active/`, an ADR, or a spec.
- `landed`: implemented and recorded in `ideas/SHIPPED.md`.
- `dropped`: intentionally declined or deferred with a reason.
- `reframed-community-build`: feature was approved as a concept but declined as a platform primitive per `project_community_build_over_platform_build` / `project_minimal_primitives_principle`; intent lives on as a community-build pattern (wiki rubric, chatbot composition, remixable node template) rather than platform code. Capture the composition path so the user-facing intent isn't lost.

## Promotion Paths

| Idea Shape | Next Home | Why |
|------------|-----------|-----|
| Small, ready, clearly bounded | `STATUS.md` `Work` | It is actionable now. |
| New truth, risk, or contradiction | `STATUS.md` `Concerns` | It changes what is currently true. |
| Needs reasoning or tradeoff analysis | `docs/design-notes/` | It needs durable thinking before build work. |
| Changes direction or a standing decision | `README.md` § Direction (founder) or a new ADR in `docs/decisions/` | The direction or a recorded decision changes. |
| Multi-step delivery with checkpoints | `docs/exec-plans/active/` | It is too large for one board row. |
| Already landed | `ideas/SHIPPED.md` | Keep the idea-to-shipping trail. |

## Session Triage Rule

1. Scan the oldest untriaged inbox items.
2. Merge duplicates and add links.
3. Promote at least one real item when the inbox is non-empty.
4. If nothing should move yet, record the blocker instead of leaving silence.

## Active Promotions

| Idea | State | Next Home | Owner | Notes |
|------|-------|-----------|-------|-------|
| Main-account cloud OpenSpec drain | promoted — Ringer-informed implementation active | `docs/design-notes/2026-07-29-main-account-cloud-spec-drain.md`; `openspec/changes/archive/2026-08-26-activate-main-universe-spec-drain/`; `docs/audits/2026-07-30-ringer-production-orchestration-implications.md`; `docs/audits/2026-07-30-ringer-drain-hotpath-reevaluation.md` | Codex research → independent review → OpenSpec/runtime lane | Host approved 2026-07-29: one cloud-owned loop, tray stopped at cutover, phone-chatbot management/repair/evolution with the computer fully off, BYOC before market compute. The 2026-07-30 re-evaluation preserves the generic user-owned GitHub→spec target and promotes only a measured local-bridge hot-path repair before cloud work resumes. |
| Fully customizable universe agents | promoted — implementation PR ready; live proof pending | PR #1985; `PLAN.md` custom-agent corollary; `openspec/changes/universe-custom-agents/`; active `STATUS.md` acceptance lane | codex-gpt5-desktop | Host approved 2026-07-30 and continued 2026-07-30. The first slice now supplies public immutable component compositions, multi-parent lineage, private bindings, interchange, and existing-handle targets. Deployment plus rendered/organic connector evidence remains; Slack effects and managed-cloud arbitrary code stay gated by their security dependencies. |
| Context-engineering comprehensive revision | promoted — implementation landed; host review pending | Review the landed PR #1838 files and `docs/audits/2026-07-28-context-engineering-*.md`; decide adopt/adapt/revert in a dedicated lane | future review owner | Host clarified 2026-07-28 that this is unrelated to the current task and belongs in a near-term implementation review. PR #1838 landed before that clarification was folded back; its presence on `main` is implementation evidence, not host adoption or permission to mix it into the active deploy-recovery lane. |
| Open compute/model/task/fabrication market | promoted | `docs/audits/2026-07-21-compute-llm-market-architecture-implications.md`; any successor must re-check current owners/specs and obtain its own gates | none (research archived) | Historical Codex research; direct Claude verdict **ADAPT**, with five report corrections folded. The later current-main source-context **APPROVE** covers only the corrected paid-market consumer, not this report, PLAN ratification, or general implementation. |
| Zapier-equivalent automation outcomes | promoted | `docs/audits/2026-07-21-zapier-automation-platform-implications.md`; future OpenSpec exploration only after fresh host/spec review | none (research archived) | Historical Codex research; direct Claude verdict **ADAPT**, with webhook-ingress and §14-source corrections folded. Host approval and scheduler/boundary exactly-once contradiction resolution still block build. |
| User growth and concurrent-user scalability | promoted | `docs/audits/2026-07-21-user-growth-concurrency-scalability-implications.md`; future capacity work requires fresh runtime evidence and current authority dependencies | none (research archived) | Historical Codex research; direct Claude verdict **ADAPT**, with provider-route corrections folded. The workload envelope is a hypothesis, not a current scale proof; PLAN authority, R2-1a/R2-1b, live BYOC, and §14 evidence remain gates. |
| Organizational universes / company brains | promoted | `docs/audits/2026-07-21-organizational-universe-company-brain-implications.md`; future OpenSpec requires current host/privacy/identity review | none (research archived) | Historical Codex research; combined Claude verdict **ADAPT**. Slack/Teams remain interaction surfaces, not authority or canonical storage. The private-data PLAN decision, daemon-memory posture, consent-path bug, provider authority, tenant identity, and visibility remain unresolved. |
| Regulated-industry policy/evidence profiles | promoted | `docs/audits/2026-07-21-regulated-industry-compliance-architecture-implications.md`; future work requires fresh authoritative sources, host decision, counsel/assessor input, and independent review | none (research archived) | Historical Codex research; combined Claude verdict **ADAPT**, not legal, compliance, certification, or build authority. Private-data, consent, provider, identity, retention, and evidence gates remain open. |
| Tiny/TinyAssets naming and Workflow retirement | promoted | `docs/design-notes/2026-06-27-tinyassets-hard-rename.md`; `docs/exec-plans/active/2026-06-27-tinyassets-hard-rename.md`; `PLAN.md` Canonical Naming Boundary | codex-gpt5-desktop | Promoted 2026-06-27 from host directive. Boundary: Tiny = personified intelligence; TinyAssets = platform/site/repo/distribution brand; Workflow/workflow is retired now, not transitional naming. |
| ChatGPT Apps as first-class host + live-state surface | promoted | `docs/exec-plans/active/2026-05-01-host-discoverability-and-onboarding-rollout.md`; future design note should still check Apps SDK/MCP Apps compatibility against `PLAN.md` API/MCP Interface, Live State Shape, Distribution + Discoverability, and full-platform architecture sections 15/17/20/26/28/29. | navigator + codex-gpt5-desktop | Promoted 2026-05-01 into the host discoverability/onboarding rollout. Treat Apps SDK as a chatbot host/UI wrapper over the same daemon + durable-state contract, not a replacement architecture. |
| Daemon mini OpenBrain + observable memory | promoted | `docs/design-notes/2026-05-02-daemon-mini-openbrain.md`; `PLAN.md` Retrieval And Memory. | codex-gpt5-desktop | Promoted 2026-05-02 from host request. Direction: daemon wiki remains curated self; daemon-scoped atomic brain handles capture/search/review/promote; memory query/retrieve/inject/write/promote/compact must be observable. Implementation waits for #18 locks to clear. |
| Runtime fiction memory graph | promoted | `docs/design-notes/2026-04-09-runtime-fiction-memory-graph.md`, `docs/exec-plans/active/2026-04-09-runtime-fiction-memory-graph.md`, `docs/design-notes/2026-04-09-memory-graph-research-brief.md`, `docs/exec-plans/active/2026-04-27-runtime-fiction-memory-graph-restart-cards.md`, `docs/specs/2026-04-27-runtime-memory-graph-minimal-schema-v1.md`, `docs/notes/2026-04-27-runtime-memory-graph-contradiction-policy.md` | future session | Turn worldbuilding/docs output into typed world truth, event, epistemic, and narrative-debt memory with scene packets and generated indexes. Research brief added 2026-04-09 with frontier validation (DOME, SCORE, StoryWriter, A-Mem, LightRAG, MemOS) and concrete gap analysis. Restart cards + v1 schema + contradiction policy added 2026-04-27 to reduce cold-start and ambiguity for implementation. |
| Methods-prose evaluator (Priya signal #2 / Proposal C) | reframed-community-build | `docs/design-notes/2026-04-27-methods-prose-evaluator.md` + `docs/notes/2026-04-27-methods-prose-rubric-starter-pack.md` (composition doctrine + publish-ready rubric content) | navigator + codex-gpt5-desktop | **REFRAMED 2026-04-26 per host directive** (memory: `project_community_build_over_platform_build`): platform will NOT ship methods-prose evaluator as a primitive. Chatbot composes from existing evaluator surface + wiki rubrics. Header/body reframe is reflected and starter rubric content now exists for wiki publication. No `EvaluatorKind` extension. INBOX provenance: 2026-04-27 entry. |
| Recency primitives — retired `extensions action=my_recent_runs` + `goals action=my_recent` (Priya signal #1) | retired-community-composition | `docs/specs/2026-04-27-recency-and-continue-branch-primitives.md` records supersession; future wiki/page can document query-run composition if users need examples. | none | F2 accepted 2026-04-28 and freshness-checked 2026-05-01: Recency is not a platform primitive. Use existing query-run + optional goal/branch lookup composition. |
| Continue-run resume primitive — `extensions action=run_branch resume_from=...` (Priya signal #6 + Devin Session 2 + 2026-04-24 extend-run dup) | dev-ready after #18 | `docs/specs/2026-04-27-recency-and-continue-branch-primitives.md` + `docs/specs/2026-04-27-recency-continue-fixture-pack.md` + `docs/exec-plans/active/2026-04-27-post-18-recency-continue-implementation-cards.md` | dev post-#18 | F2 accepted 2026-04-28 and freshness-checked 2026-05-01: no standalone `continue_branch` verb; add only `resume_from=<run_id>` to existing `run_branch`. |
| Cross-algorithm methodological-parity guidance — `node action=compatibility_with` or wiki concept page (Priya signal #4) | promoted | `docs/design-notes/2026-04-27-cross-algorithm-methodological-parity-guidance.md` + `docs/notes/2026-04-27-cross-algorithm-parity-wiki-template.md` + `docs/notes/2026-04-27-cross-algorithm-parity-publication-checklist.md` | codex-gpt5-desktop | INBOX provenance: 2026-04-27 (cross-algorithm-parity). Promoted 2026-04-27: design decision landed; publish template + publication checklist + RF-vs-MaxEnt seed are ready for wiki publication and user-sim validation. |
| Trust-graduation observability — "% users skipping dry-inspect on session N" (Priya signal #7) | promoted | `docs/design-notes/2026-04-27-trust-graduation-observability-metric.md` + `docs/notes/2026-04-27-trust-graduation-query-pack.md` (metric contract + query/dashboard pack implementation-ready). | codex-gpt5-desktop | INBOX provenance: 2026-04-27 (trust-graduation). Promoted 2026-04-27 as docs-ready instrumentation slice; query examples and dashboard sketch now land with the metric contract. |
| CONTRIBUTORS.md authoring surface (Co-Authored-By attribution) | promoted | `docs/design-notes/2026-04-27-contributors-authoring-surface.md` + `docs/notes/2026-04-27-contributors-maintenance-runbook.md` (decision + maintenance/merge hygiene runbook). | codex-gpt5-desktop | INBOX provenance: 2026-04-25. Anchored in `AGENTS.md` Hard Rule #10 (read CONTRIBUTORS.md → emit Co-Authored-By). Promotion landed 2026-04-27 with decision, escalation criteria, and maintenance rules. |
| `hyperparameter_importance` evaluator node (Priya W&B signal #4) | promoted | `docs/catalogs/scientific-computing-domain-catalog.md` + `docs/specs/2026-04-27-hyperparameter-importance-evaluator-node.md` + `docs/specs/2026-04-27-hyperparameter-importance-fixture-pack.md` + `docs/exec-plans/active/2026-04-27-hyperparameter-importance-implementation-cards.md` | codex-gpt5-desktop | INBOX provenance: 2026-04-24 (Priya-W&B-trial). Domain-specific (scientific-computing skill, NOT engine). Contract + fixtures + implementation cards are now prebuilt for immediate lane-open execution; SCI-EVAL-001 parks it in the science-domain catalog. |
| Agent-teams-on-TinyAssets (open-source-Claude-Code-analog as a user project) | promoted | `docs/notes/2026-04-20-agent-teams-on-tinyassets-research.md` + `docs/notes/2026-04-27-agent-teams-post-uptime-scoping-checklist.md` (post-unblock checklist now execution-ready). | codex-gpt5-desktop + navigator-followup | INBOX provenance: 2026-04-20 (host-source). Research note maps 11-seam gaps + viral-moment considerations + foundation/UX/commons rankings; recommends nano-claude-code as Python reference base. Promoted 2026-04-27 by adding a concrete entry-gates + phase checklist artifact so work starts immediately when unblock criteria are met. |
| Universe-scoped custom agents + remixable common configurations | promoted | `PLAN.md` Daemon Platform, Providers, Distribution & Discoverability, Evolution & Evaluation, and Design Decisions; future OpenSpec change before implementation | codex-gpt5-desktop | Host approved 2026-07-30: public/forkable definitions and lineage; private universe bindings/state; full component-level customization with import/export and no artificial power-user ceiling. TinyAssets wins through commons, evidence, cloud/hostless uptime, plug-and-play setup, collaboration, and existing-subscription bindings — not lock-in. Extends rather than replaces Agent-teams-on-TinyAssets. |

## Backlog Burn-Down Queue (2026-04-28)

Ordered for fastest de-risking while #18/#23 remain in flight. Before moving
any row to `STATUS.md`, run
`python scripts/claim_check.py --provider <name> --check-files "<Files>"`.

No claim-ready burn-down rows remain. Add new rows here only after
`claim_check.py --check-files` confirms a non-overlapping write set and the row
has a concrete exit check.

## Archive

- [2026-05-01] F2 recency/continue backlog rows retired. Recency actions are superseded by existing query-run composition; continuation is the dev-ready `run_branch resume_from=<run_id>` row in `STATUS.md`.
- [2026-05-01] Burn-down rows 1, 2, 3, and 6 retired after freshness check: methods-prose reframe, cross-algorithm parity template, CONTRIBUTORS file-first decision, and trust metric/query pack already exist under their 2026-04-27 artifact names.
- [2026-05-01] `hyperparameter_importance` burn-down row retired: `docs/catalogs/scientific-computing-domain-catalog.md` now has SCI-EVAL-001 and keeps v1 out of engine scope.
- [2026-05-01] Agent-teams burn-down row retired: Active Promotions plus `docs/notes/2026-04-27-agent-teams-post-uptime-scoping-checklist.md` already hold the post-unblock gates; still blocked on uptime-track close + daemon-economy first draft.
- [YYYY-MM-DD] TinyAssets seed-style retrofit initialized.
# 2026-04-09

- Research pipeline item: translate BettaFish's strongest pattern set into TinyAssets-native architecture.
- Source document: `docs/bettafish-refactor-research-2026-04-09.md`.
- Target outcome: narrative IR, staged drafting, typed deliberation bus, durable run ledger, and hard validation gates without adopting BettaFish's log-scraping transport, framework sprawl, or GPL-covered code.
