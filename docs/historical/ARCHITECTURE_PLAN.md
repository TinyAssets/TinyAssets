> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-02 (reframed 2026-04-11). Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# TinyAssets — Architecture (As-Built)

**Last updated:** 2026-04-02 (reframed 2026-04-11)
**Status:** Living reference — reflects what exists today, not aspirations

---

## 1. System Overview

TinyAssets is a goal-agnostic daemon engine. The daemon drives autonomous long-horizon tasks using LLM providers, with knowledge retrieval, constraint checking, multi-tier evaluation, and hierarchical memory keeping output coherent. Fantasy authoring is the first domain — the daemon writes a novel scene by scene. A FastAPI server exposes the daemon as an HTTP API. The Universe Server MCP is the primary user interface.

```
User <-> Custom GPT <-> FastAPI (30 endpoints) <-> Daemon (LangGraph)
                                                      |
                                    +---------+-------+-------+---------+
                                    |         |       |       |         |
                                 Providers  Memory  Eval   Knowledge  Constraints
```

**How it works:** The user describes a story premise through the GPT. The daemon writes autonomously — looping through orient, plan, draft, evaluate, commit for each scene. The user steers by posting directives (picked up at scene boundaries), reading output, and editing prose through the GPT. The daemon never blocks on human input.

**Communication model:** File-based. The daemon writes to disk (output/, status.json, progress.md, activity.log, world state DB). The API reads those files. The GPT calls the API. No direct daemon-GPT connection.

---

## 2. Graph Architecture

Four nested LangGraph StateGraphs map to fiction's creative hierarchy:

```
Universe (daemon entry point — runs indefinitely)
  -> Book (chapter sequencing, stuck recovery, book closure)
    -> Chapter (scene loop, consolidation, learning)
      -> Scene (orient -> plan -> draft -> commit)
```

### 2.1 Scene Graph (core creative loop)

```
orient -> plan -> draft -> commit -> [accept: END | second_draft: draft]
```

| Node | What it does | Status |
|------|-------------|--------|
| **orient** | Reads premise, steering, canon files, retrieval context, memory. Builds workflow instructions and world context for the scene. | Wired. Reads canon/*.md directly, calls retrieval router (KG + vectors via runtime singletons), assembles memory context. All three context sources flow through to plan and draft prompts. |
| **plan** | Generates beat alternatives, scores them, selects best. Includes canon context, retrieved facts, memory, and HTN/DOME structural guidance when available. | Wired. Uses provider for generation. Canon + retrieved + memory context injected into prompt. HTN/DOME partially wired (runs when goal is extractable). |
| **draft** | Writes scene prose from the plan with full world context. | Wired. Uses writer provider chain. Canon + retrieved + memory context + recent prose (2000 chars) injected into prompt. |
| **commit** | Evaluates draft (3-tier), extracts facts/promises/characters, updates world state DB, writes prose to disk. Returns verdict. | Wired. Structural eval + judge ensemble + debate. |

The conditional edge `route_after_commit` allows at most one revision (second_draft). After that, it accepts regardless — never blocks.

### 2.2 Chapter Graph

```
run_scene -> [more: run_scene | done: consolidate] -> learn -> END
```

- **run_scene**: Compiles and invokes the Scene subgraph. Falls back to direct node calls on failure.
- **consolidate**: Summarizes chapter prose via LLM call. Returns `chapter_summary` to the controller, which triggers the creative story briefing generation in progress.md.
- **learn**: Runs style rule observation, craft card generation.
- **Hardcoded**: `scenes_target: 3` per chapter. Candidate for flattening — let the model decide when a chapter is complete.

### 2.3 Book Graph

```
run_chapter -> [more: run_chapter | stuck: diagnose | done: book_close] -> END
```

- **run_chapter**: Compiles and invokes the Chapter subgraph.
- **diagnose**: Stuck recovery — analyzes quality trend, suggests course corrections.
- **book_close**: Wraps up the book arc.
- **Hardcoded**: `chapters_target: 1` per book. Same flattening candidate as chapter.

### 2.4 Universe Graph (daemon loop)

```
select_task -> [write: run_book | worldbuild | reflect] -> universe_cycle -> [continue: select_task | stopped: END]
```

- **select_task**: Reads world state, chooses next task (write, worldbuild, reflect). Defaults to write.
- **worldbuild**: Autonomously generates canon documents when creative triggers fire (new facts, gaps, contradictions).
- **reflect**: Self-assessment and direction adjustment.
- **universe_cycle**: Loops back to select_task unless explicit stop signal received (API call, SIGINT, .pause file).

---

## 3. Provider Routing

Six providers, zero API credits. All use CLI subscriptions (claude, codex), free tiers, or free credits.

| Provider | Interface | Models |
|----------|-----------|--------|
| **claude-code** | `claude -p` subprocess | Opus 4.6 |
| **codex** | `codex exec` subprocess | Codex |
| **gemini-free** | REST API (free tier) | Gemini |
| **groq-free** | REST API (free tier) | Groq models |
| **grok-free** | REST API (xAI free credits) | grok-4.1-fast |
| **ollama-local** | Local HTTP | qwen3.5-nothink, nomic-embed-text |

### Role-Based Fallback Chains

| Role | Chain (in order) |
|------|-----------------|
| **writer** | claude-code -> codex -> gemini-free -> groq-free -> grok-free -> ollama-local |
| **judge** | codex -> gemini-free -> groq-free -> grok-free -> ollama-local |
| **extract** | ollama-local -> groq-free -> gemini-free |
| **embed** | ollama-local |

The router tracks quota and cooldowns per provider. If a provider is rate-limited or unavailable, it's put on cooldown and the chain advances. The system never stops due to provider unavailability — the chain always terminates at ollama-local.

For judge ensemble, three chains with rotated priority ensure family diversity (different models evaluate from different perspectives).

---

## 4. Retrieval Stack

Three retrieval layers plus an agentic router. Architecture validated by industry convergence (2026 — KG + vectors + agentic routing is the emerging standard).

| Layer | Implementation | What it does | Status |
|-------|---------------|-------------|--------|
| **Knowledge Graph** | HippoRAG + Leiden clustering | Entity/relationship tracking, epistemic filtering (who knows what), community detection | Built. **Not yet populated in production** — needs E2E daemon run to generate data. |
| **Vector Embeddings** | LanceDB + nomic-embed-text | Semantic similarity search, tone matching | Built. **Not yet populated in production.** |
| **Hierarchical Summaries** | RAPTOR tree | Multi-level abstractive summaries, navigates content at different abstraction levels | **Wired.** `rebuild_raptor_from_canon` runs at daemon startup and after each worldbuild cycle. Orient node reads RAPTOR tree from runtime singletons. Needs content to produce meaningful summaries. |
| **Agentic Router** | LLM-based query decomposition | Decides which retrieval backend(s) to query, breaks compound questions into sub-queries | **Wired.** Orient node constructs `RetrievalRouter` from runtime singletons (KG, vector store, RAPTOR tree) and queries it. Needs E2E verification that it routes to different backends vs defaulting to one. |

### Epistemic Layer

`FactWithContext` carries metadata on every extracted fact:
- `source_type` (dialogue, narration, author_note)
- `narrator_reliability` (float 0.0-1.0 — confidence in the source)
- `access_tier` (int — higher values = more restricted knowledge)
- `pov_characters` (which characters know this fact)
- `temporal_bounds` (when the fact is valid)

This is the fiction-specific differentiator — a flat vector store treats all canon equally, but the epistemic layer enforces that a commoner character can't reference Lattice mechanics.

### Phase-Aware Context

Different graph phases get different retrieval context:
- **orient**: Full context — world state, recent events, overdue promises
- **plan**: Relevant precedents, character arcs, foreshadowing opportunities
- **draft**: Prose samples for tone matching, relevant canon details
- **evaluate**: Canon facts for consistency checking, established rules

---

## 5. Evaluation Pipeline

Three-tier evaluation, inspired by the principle that generators should not grade their own output.

### Tier 1: Structural (deterministic — no LLM)

Eight checks running on every commit, no API calls required:

| Check | What it catches |
|-------|----------------|
| TAACO coherence | Low lexical overlap between paragraphs (incoherent prose) |
| Readability | Flesch-Kincaid outliers (too simple or too complex) |
| Pacing | Word count deviation from chapter average |
| Chekhov | Introduced elements without payoff (promise tracking) |
| Timeline | Temporal inconsistencies |
| Character voice | Dialogue consistency per character |
| Canon breach | Contradictions with established canon facts |
| ASP constraint | Formal constraint violations (when Clingo available; passes if unavailable) |

Optional dependency on spaCy (`en_core_web_sm`) — gracefully degrades if not installed.

### Tier 2: Judge Ensemble (LLM-based)

Three judges from different model families evaluate each scene. Uses swap-and-verify protocol (judge evaluates, then positions are swapped to debias). Circuit breakers prevent cascading failures — if a judge errors repeatedly, it's taken offline.

- **Wired.** Async fix applied — uses ThreadPoolExecutor pattern for LangGraph compatibility. Uses the judge provider chain (codex -> gemini -> groq -> ollama).
- Judges are tuned for creative ambition, not just correctness: "Is this scene doing something interesting?" not just "Is this scene coherent?"

### Tier 3: Debate Escalation

When judge ensemble consensus is below threshold, enters a structured debate between two LLM advocates (one argues accept, one argues reject). A moderator synthesizes.

- **Wired.** Async fix applied — uses ThreadPoolExecutor pattern for LangGraph compatibility. Triggers when ensemble consensus < 70%.

### Verdict Routing

```
Canon breach or ASP violation -> revert (provably wrong)
Ensemble verdict "fail" -> revert
Ensemble verdict "weak" + no second draft yet -> second_draft (one revision)
Otherwise -> accept (never block)
```

### Aspirational (built, not yet earning its keep)

- **Judge calibration** — anchor set testing and drift detection. Needs curated anchor passages.
- **Criteria discovery** — regex-based extraction of new quality dimensions from judge rationales. Wired in the learn node (called after each chapter), but downstream of judges producing rationale data to extract from.

---

## 6. Memory & Learning

### Hierarchical Memory (Letta-inspired)

| Tier | Scope | Implementation |
|------|-------|---------------|
| **Core** | Active context — current chapter, active characters, recent decisions | In-memory dict, rebuilt per scene |
| **Episodic** | Recent events — scene summaries, recent facts, sliding window | SQLite-backed, windowed by chapter count |
| **Archival** | Full universe — all facts, all prose, all relationships | LanceDB vectors + Knowledge Graph + ASP rules |

**MemoryManager** coordinates all three tiers. Assembles phase-specific context bundles within a ~15K token budget.

**Promotion gates** move facts between tiers: core -> episodic (after scene commit) -> archival (after chapter consolidation). Gates apply relevance scoring and deduplication.

### Learning System

| Component | What it does | Status |
|-----------|-------------|--------|
| **Style rules** | Observe patterns, cluster, promote to persistent rules | Built, wired in learn node |
| **Craft cards** | Generate reusable creative techniques from successful scenes | Built, wired in learn node |
| **Reflexion** | Self-critique -> verbal reflection -> sliding window memory | Built, in reflexion engine |
| **Criteria discovery** | Extract new eval dimensions from judge rationales | Built, **wired in learn node** (learn.py:19,170). Needs judge rationale data to produce results. |

---

## 7. Constraints & Planning

### ASP Engine

Formal constraint checking via Clingo (Answer Set Programming). Encodes world rules as logic programs, validates scenes against them.

- **Wired in structural eval** (Tier 1, check #8). Gracefully passes if Clingo is not installed.
- World rules live in `data/world_rules.lp`. **Note:** This is generic boilerplate (institution rules, character consistency), not per-universe rules. Per-universe constraint synthesis (via ConstraintSynthesis) would generate universe-specific rules from the premise and canon, but is not yet wired.

### Constraint Synthesis

Full neurosymbolic pipeline: classify premise richness -> HTN decomposition -> DOME expansion -> ASP validation -> iterate until constraint surface is ready.

- **Wired in plan node** (plan.py:62,581). `_try_constraint_synthesis` runs when premise and canon are available, feeding the constraint surface into beat scoring. Gracefully skips when ConstraintSynthesis dependencies are unavailable.

### HTN Planner & DOME Expansion

- **HTN**: Hierarchical task network decomposition of story goals into acts, chapters, scenes.
- **DOME**: Multi-level outline expansion with knowledge graph feedback.
- Both built and **partially wired** into plan.py. When a goal is extractable from state, HTN decomposes it and DOME expands the current scene's beats. Plan scoring boosts alternatives that align with structural guidance. Runs silently (graceful skip) when no goal is available.

---

## 8. API & GPT Interface

### FastAPI Server

30 endpoints, running on port 8321. Exposed via Cloudflare tunnel for Custom GPT access.

**Universe management:**
- `GET /v1/universes` — list universes
- `POST /v1/universes` — create universe
- `PATCH /v1/universes/{uid}` — rename universe

**Story content:**
- `GET /v1/universes/{uid}/premise` / `POST` — read/set premise
- `GET /v1/universes/{uid}/directives` / `POST` — read/add steering directives
- `GET /v1/universes/{uid}/output` — list chapters/scenes
- `GET /v1/universes/{uid}/output/{path}` — read scene file
- `PUT /v1/universes/{uid}/output/{path}` — edit scene file
- `GET /v1/universes/{uid}/canon` — list canon files
- `GET /v1/universes/{uid}/canon/sources` — list original source files
- `GET /v1/universes/{uid}/canon/{filename}` — read canon file
- `POST /v1/universes/{uid}/canon` — add worldbuilding document
- `POST /v1/universes/{uid}/canon/upload` — file upload (binary)
- `POST /v1/universes/{uid}/canon/batch` — batch upload multiple files

**Workspace:**
- `GET /v1/universes/{uid}/workspace` — list workspace files
- `GET /v1/universes/{uid}/workspace/{filename}` — read workspace file
- `POST /v1/universes/{uid}/workspace` — create workspace file
- `DELETE /v1/universes/{uid}/workspace/{filename}` — delete workspace file

**Daemon monitoring:**
- `GET /v1/universes/{uid}/status` — daemon phase, word count, provider, verdict
- `GET /v1/universes/{uid}/activity` — activity log tail
- `GET /v1/universes/{uid}/progress` — creative story briefing
- `GET /v1/universes/{uid}/overview` — combined status + progress + recent activity

**Daemon internals:**
- `GET /v1/universes/{uid}/facts` — extracted world facts from DB
- `GET /v1/universes/{uid}/characters` — tracked character states
- `GET /v1/universes/{uid}/promises` — open/resolved narrative promises

**Control:**
- `POST /v1/daemon/{action}` — start, stop, pause
- `POST /v1/config/providers` — configure provider settings
- `GET /v1/health` — health check

### Custom GPT

Primary user interface. System prompt instructs the GPT to always use API Actions, never search the web. Handles:
- Creating universes and setting up stories (multi-step action sequence)
- Reading output and summarizing what's happening
- Posting steering directives
- Editing prose
- Adding worldbuilding canon
- File upload -> universe creation workflow

The GPT is a creative collaborator, not a command-line interface. It discusses, suggests, and steers — the daemon does the writing.

---

## 9. World State Database

SQLite database (`story.db`) per universe, tracking:

| Table | What it stores |
|-------|---------------|
| `scene_history` | Scene metadata — chapter, verdict, word count, timestamp |
| `extracted_facts` | Extracted facts with FactWithContext metadata |
| `character_states` | Character location, emotional state, knowledge facts |
| `promises` | Narrative promises — setup, payoff status, deadline |

Updated by the commit node after each scene. Queried by orient for context and by the API for the facts/characters/promises endpoints.

---

## 10. What's Aspirational

Clearly separating what works from what's built-but-unverified:

| Component | Status | What's needed |
|-----------|--------|---------------|
| Knowledge graph population | **Wired** | indexer.py extracts entities/edges/facts from canon, adds to KG via runtime singletons. worldbuild._trigger_kg_reindex() indexes all canon/*.md. |
| Vector store population | **Wired** | indexer.py embeds text chunks via Ollama, indexes into LanceDB VectorStore via runtime singletons. |
| RAPTOR tree | **Wired** | `rebuild_raptor_from_canon` runs at daemon startup and after worldbuild cycles. Orient reads tree from runtime singletons. Needs canon content to produce meaningful summaries. |
| Retrieval router | **Wired**, untested E2E | orient.py constructs RetrievalRouter from runtime singletons (KG, vectors, RAPTOR), queries it. Results flow to plan/draft prompts. Needs live E2E verification. |
| Epistemic filtering | **In progress** | Knowledge graph carries FactWithContext with access_tier metadata. Filtering logic built. Needs E2E run with facts to verify POV-based filtering. |
| Constraint synthesis | **Wired** | `_try_constraint_synthesis` in plan.py runs when premise + canon available. Feeds constraint surface into beat scoring. Graceful skip when dependencies unavailable. |
| HTN/DOME | **Partially wired** | Runs in plan.py when a goal is extractable from state. Graceful skip otherwise. |
| Criteria discovery | **Wired** | Called in learn node after each chapter (learn.py:170). Needs judge rationale data to produce results. |
| Judge calibration | Built, not wired | Needs curated anchor passages and working ensemble |
| Graph flattening | Not started | After E2E: should chapter/book counters be replaced by model judgment? |
| Multi-universe isolation | **Fixed** | `_stop_current_daemon()` calls `runtime.reset()` to clear all singletons. `DaemonController._cleanup()` closes KG connection. New controller creates fresh backends for the new universe path. |

### Session Learnings (2026-04-02)

Two bugs invalidated all prior E2E data:
1. **`--system-prompt` fix**: Claude provider was not passing system prompts to `claude -p`, so the daemon was writing with no creative instructions. All prior output was produced without the orient/plan context reaching the model.
2. **Worldbuild loop**: `select_task` chose worldbuild repeatedly because world_state_stale was always true (no facts in DB on first run). The daemon never reached the write path. Fixed by defaulting to write when no scenes exist.

Any E2E observations from before this session should be disregarded — the daemon was not functioning as designed.

---

## 11. Project Structure

```
fantasy_author/
  graphs/          Universe, book, chapter, scene graph definitions
  state/           TypedDict state definitions (Annotated reducers)
  nodes/           Node implementations (orient, plan, draft, commit, etc.)
  providers/       6 LLM providers + router + quota tracking
  knowledge/       Entity extraction, HippoRAG, Leiden clustering, RAPTOR
  retrieval/       LanceDB vectors, agentic router, phase context
  constraints/     ASP engine, constraint surface, constraint synthesis
  planning/        HTN planner, DOME expansion
  evaluation/      Structural checks, ensemble, debate, verdict
  judges/          Judge config, swap-verify, calibration, circuit breakers
  learning/        Style rules, craft cards, criteria discovery
  memory/          Core/episodic/archival memory, promotion, reflexion
  desktop/         Tkinter launcher, system tray, dashboard
  api.py           FastAPI server (30 endpoints)
  mcp_server.py    MCP server for Claude Code integration
  __main__.py      DaemonController — daemon entry point
```

---

## 12. Key Design Rules

1. **SqliteSaver only** — not AsyncSqliteSaver (not production-safe).
2. **LanceDB singleton** — reuse connection objects, never recreate.
3. **No API SDKs for LLM calls** — providers use subprocess (`claude -p`, `codex exec`).
4. **Never block on human input** — every gate has an autonomous default.
5. **TypedDict + Annotated reducers** — `Annotated[list, operator.add]` for accumulating fields.
6. **FactWithContext with epistemic metadata** — every fact carries source_type, reliability, access_tier.
7. **The daemon IS Opus** — design for a smart model. Components should be available, not constraining.
8. **Every component is an assumption** — stress-test before adding, strip when models improve.
9. **Separate worker from evaluator** — the generator should not grade its own output.
10. **Iterative feedback drives creative risk** — without evaluation loops, the model defaults to safe/generic.
