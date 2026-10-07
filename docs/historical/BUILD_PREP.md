> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-03-31. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# TinyAssets — Build Preparation Document

**Date:** 2026-03-31
**Purpose:** One-shot build session reference. Everything you need to ship shippable code without re-researching.

This document is a **companion to ARCHITECTURE_PLAN.md**. Read ARCHITECTURE_PLAN.md first for design rationale. Read this document for implementation details, gotchas, module ordering, and the exact patterns to use.

---

## Table of Contents

1. [Exact Dependencies (pyproject.toml ready)](#1-exact-dependencies-pyprojecttoml-ready)
2. [Incremental Build Phases (MVP-First)](#2-incremental-build-phases-mvp-first)
3. [Per-Module Implementation Notes with Gotchas](#3-per-module-implementation-notes-with-gotchas)
4. [Golden Test Strategy](#4-golden-test-strategy)
5. [Critical Research Insights for the Builder](#5-critical-research-insights-for-the-builder)
6. [File-by-File Implementation Order](#6-file-by-file-implementation-order)
7. [FactWithContext Dataclass (Fiction-Specific)](#7-factwithcontext-dataclass-fiction-specific)
8. [Phase-Aware Retrieval Router Pattern](#8-phase-aware-retrieval-router-pattern)

---

## 1. Exact Dependencies (pyproject.toml ready)

Use this **exactly**. Test versions against the latest PyPI data (as of 2026-03-31).

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "fantasy-author"
version = "0.1.0"
description = "Neural-symbolic fiction generation system for long-form storytelling"
requires-python = ">=3.11"
authors = [
    {name = "Jonathan", email = "jonathan@example.com"}
]
dependencies = [
    # Core orchestration
    "langgraph>=0.4,<0.5",
    "langchain-core>=0.3,<0.4",

    # Vector & KG storage
    "lancedb>=0.15,<1.0",
    "igraph>=0.11,<1.0",

    # Symbolic solving & planning
    "clingo>=5.7,<6.0",

    # Desktop app
    "pystray>=0.19,<1.0",
    "pillow>=10.0",

    # NLP (entity extraction, coreference)
    "spacy>=3.7,<4.0",

    # Async file I/O
    "aiofiles>=24.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "pytest-cov>=5.0",
]
gemini = [
    "google-genai>=1.0",
]
groq = [
    "groq>=0.9",
]

[tool.hatch.build.targets.wheel]
packages = ["fantasy_author"]
```

### Dependency Notes

- **langgraph**: Use 0.4.x for StateGraph, Send API, Annotated reducers. 0.5 introduces breaking changes.
- **langchain-core**: Only needed for base types. No langchain package itself.
- **lancedb**: In-process, serverless. No separate daemon.
- **igraph**: C bindings. Requires `igraph-core` system library on Linux/Mac (usually pre-installed).
- **clingo**: ASP solver. Fast grounding. Optional for Phase 4+, but include in base deps.
- **pystray**: For system tray icon. Requires Pillow for icon images.
- **spacy**: Use model `en_core_web_sm` (download post-install: `python -m spacy download en_core_web_sm`).
- **aiofiles**: For non-blocking file I/O in async nodes.

### Post-Install Steps

```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
# Optional (Phase 3+):
pip install -e ".[gemini,groq]"
```

---

## 2. Incremental Build Phases (MVP-First)

**Philosophy:** Ship a working end-to-end loop as fast as possible. Stub
non-critical components. Add real implementation in phases.

**Note on ordering:** ARCHITECTURE_PLAN.md Section 11 describes a
science-first 9-phase build (KG in Phase 2 as foundational). This document
intentionally reorders to MVP-first 8 phases (KG in Phase 2 but after real
nodes in Phase 1) based on research findings: 200K context may be sufficient
for single books, and shipping an end-to-end loop fast lets us validate the
graph topology before layering complexity. The KG remains foundational — it
just enters after we prove the skeleton works. Both orderings converge on the
same final system.

### Phase 0: LangGraph Skeleton + Basic Checkpointing

**Goal:** One scene flows end-to-end with checkpointing. No KG, no ASP, no evaluation ensemble.

**What to build:**
- Graph topology (Scene, Chapter, Book, Universe). All 5 nodes exist but most are pass-throughs.
- SqliteSaver checkpointer with WAL mode.
- TypedDict state definitions for all levels.
- Mock provider that returns fixed text (no real LLM calls).

**What to stub:**
- Orient node: return empty warnings
- Plan node: return trivial beat sheet
- Draft node: return placeholder prose
- Evaluate node: accept everything, return no quality feedback
- Knowledge graph: SKIP entirely
- Constraint synthesis: SKIP entirely
- ASP/HTN/DOME: SKIP entirely

**Success criteria:**
- Graph compiles
- Scene runs to completion with 5 node calls
- State persists to checkpoint.db
- Can pause/resume from checkpoint
- No external API calls

**Estimated time:** 2-3 hours

---

### Phase 1: Real Orient/Plan/Draft/Commit Nodes + Deterministic Evaluation

**Goal:** Nodes produce real output. Structural evaluation works. SQLite fact storage works.

**What to build:**
- **Orient node (real):** Query promise DB for overdue promises, pacing flags, character gaps. Return structured warnings.
- **Plan node (real):** Call a single provider (claude -p) to generate 3-5 beat alternatives. Score deterministically on: contradiction risk, promise alignment, pacing. Return beat sheet + done_when.
- **Draft node (real):** Call provider to write prose from beat sheet. Optional mid-scene refresh.
- **Commit node (real):** Deterministic structural evaluation only: word count, pacing rhythm (TAACO), promise coverage, timeline consistency checks.
- **Fact extraction:** Simple regex/LLM extraction of facts from prose. Store in SQLite fact_store.
- **World state tracker:** SQLite schema for character states, location states, timeline events, promise states.

**What to stub:**
- Knowledge graph: Still skip
- Multi-judge ensemble: Use single provider for now
- Constraint synthesis: Stub, accept premise as-is
- ASP/HTN/DOME: Skip

**Success criteria:**
- One complete chapter (3-5 scenes) generates with real prose
- No contradictions in character/location names across scenes
- Promise tracking works (promise added in scene 1, resolved in scene 3)
- Deterministic evaluation catches obvious errors (character in two places simultaneously)
- Word counts are stable (±5% between runs with same seed)

**Estimated time:** 4-6 hours

---

### Phase 2: Knowledge Graph (Entity Extraction + Leiden + HippoRAG) + LanceDB Vectors

**Goal:** Real retrieval for orient node. Vector search for draft node tone-matching.

**What to build:**
- **Entity extraction pipeline:** LLM-based extraction from prose. Store entities, relationships, access tiers in SQLite.
- **igraph Leiden clustering:** Build entity graph from relationships. Run Leiden algorithm to detect character groups, plot threads.
- **HippoRAG implementation:** Personalized PageRank for entity-relationship queries during orient.
- **LanceDB vector store:** Index prose chunks for tone/style similarity. Vector search during draft node for "find prose with this tone".
- **RAPTOR trees:** Recursive summarization for global context queries (optional, Phase 2.5).

**What to stub:**
- Epistemic filtering: Mark access_tier on edges but don't enforce filtering yet
- Constraint synthesis: Still skip
- ASP/HTN: Skip

**Success criteria:**
- Entity graph builds from 1+ chapter of prose
- Leiden finds 3+ communities
- HippoRAG returns relevant character relationships for a given query
- LanceDB returns prose chunks with matching tone
- Orient node retrieves character warnings from KG instead of DB queries

**Estimated time:** 5-8 hours

---

### Phase 3: Multi-Provider Routing + Judge Ensemble

**Goal:** Fallback chains work. Quality evaluation has 2-3 judges from different families.

**What to build:**
- **Provider router:** Implement FALLBACK_CHAINS for writer, judge, extract roles. Handle quota tracking, cooldown, activity timeouts.
- **Judge ensemble:** Fan out commit node evaluation to 2-3 judges. Enforce model family diversity. Merge verdicts via consensus.
- **Degraded-mode operation:** If multiple providers down, system continues with reduced judge count or quality floor.
- **Subprocess patterns:** Implement `claude -p` and `codex exec` subprocess calls with proper error handling.

**What to stub:**
- Epistemic filtering: Still not enforced
- Constraint synthesis: Skip
- ASP/HTN: Skip

**Success criteria:**
- Router falls back to next provider when one is unavailable
- 2-3 judges score same scene with different verdicts; system merges via majority
- Quota cooldown prevents hammering unavailable providers
- Activity timeout kills hung subprocess after 120s
- Scene production continues even if primary provider fails

**Estimated time:** 3-4 hours

---

### Phase 4: ASP Constraint Engine (Clingo) + HTN Planning Scaffolding + DOME Outline

**Goal:** Formal planning and validation work. Sparse prompts get constraint-rich setup.

**What to build:**
- **Constraint synthesis subgraph:** Takes premise → applies EXTRACT or GENERATE mode → outputs ConstraintSurface.
  - EXTRACT mode: decompose rich canon, validate with ASP, index into KG.
  - GENERATE mode: HTN decompose premise → DOME expand outline → ASP validate → iterate.
- **ASP rule engine:** Encode world rules (no FTL, character knowledge boundaries, timeline constraints) as Clingo rules. Query solver to validate scene plans.
- **HTN decomposition:** Goal (e.g., "character discovers secret") → acts → beats. Produces multi-level outline.
- **DOME outline expansion:** Recursive outline generator. Uses KG feedback to deepen sparse outlines.

**What to stub:**
- Epistemic filtering: Mark but don't enforce
- Full RAPTOR trees: Skip

**Success criteria:**
- Sparse prompt (2 sentences) generates equivalent constraint surface as rich canon
- ASP solver catches timeline violations before writing
- HTN produces decomposed outline from high-level goal
- DOME expands outline to scene level with KG guidance
- First 2-3 chapters write with constraint-backed scaffold

**Estimated time:** 6-10 hours

---

### Phase 5: Full Memory System (Core/Episodic/Archival + Promotion Gates) + Reflexion Learning

**Goal:** Long-term coherence across 10+ chapters. Learning from evaluation mistakes.

**What to build:**
- **Memory hierarchy:** Core (active context, current chapter) → episodic (recent scene summaries) → archival (full KG + vectors).
- **Promotion gates:** 3+ scene evidence promotes fact to episodic. Persistent violations promote to ASP rule. Calibration signals → style rules.
- **Reflexion node:** After commit node verdict, if revert, self-critique prose, write verbal reflection, update memory weights.
- **Sliding window memory:** Manage context pressure. As chapters grow, demote older facts to archival, keep only hot facts in core.

**What to stub:**
- Nothing major; all pieces exist from earlier phases

**Success criteria:**
- 10+ chapter run maintains character consistency (voice, knowledge, emotional state)
- Reflexion prevents repeated mistakes (same judge failure twice → style rule generated)
- Core memory holds ~2-3 chapters; archival holds full history
- Long-form story shows stable quality across chapters 1-10

**Estimated time:** 4-6 hours

---

### Phase 6: Desktop App (pystray Daemon, Streaming Dashboard)

**Goal:** User-facing interface. System tray control, real-time progress display.

**What to build:**
- **pystray daemon wrapper:** Start/pause/resume universe graph. Stream status to tray icon.
- **System tray icon:** Show current phase, word count, accept rate. Pause/resume buttons.
- **Toast notifications:** Chapter complete, book complete, stuck recovery initiated.
- **Dashboard (optional Phase 6.5):** Web dashboard (FastAPI + WebSocket) for detailed metrics. Stream judge verdicts, quality traces, chapter outlines.

**What to stub:**
- Settings dialog (Phase 7)
- Multi-universe selection (Phase 7)

**Success criteria:**
- System tray appears on startup
- Can pause/resume from tray without losing state
- Status updates in real-time as chapters write
- Toast appears on chapter completion
- User can open output folder from tray

**Estimated time:** 2-3 hours

---

### Phase 7: Polish, Long Runs, Cross-Book Coherence

**Goal:** Production-ready system for extended storytelling.

**What to build:**
- **Progressive ingestion:** User drops canon files, system ingests to KG + indexes progressively (non-blocking).
- **Cross-book promise tracking:** Promises that span books are promoted to series-level state.
- **Series-wide character arcs:** Characters evolve across books; memory persists.
- **Stuck recovery (advanced):** Diagnose stuck states, apply targeted worldbuilding, replan.
- **Output versioning:** Save all drafts, allow rollback, track quality metrics per version.

**What to stub:**
- Nothing; system should be production-ready by end of Phase 6

**Success criteria:**
- One full 50K-word book writes end-to-end without human intervention
- Multi-book series (2+ books) shows coherent character arcs and promise resolution
- User can drop new canon at any time and system ingests + re-retrieves
- Long runs (8+ hours) show stable performance, no memory leaks

**Estimated time:** 3-4 hours

---

## 3. Per-Module Implementation Notes with Gotchas

### 3.1 LangGraph Patterns

#### StateGraph and Edges

```python
# Use this pattern for every graph:
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

scene_graph = StateGraph(SceneState)
scene_graph.add_node("orient", orient_node)
scene_graph.add_node("plan", plan_node)
scene_graph.add_node("draft", draft_node)
scene_graph.add_node("commit", commit_node)

scene_graph.set_entry_point("orient")
scene_graph.add_edge("orient", "plan")
scene_graph.add_edge("plan", "draft")
scene_graph.add_edge("draft", "commit")

def route_after_commit(state: SceneState) -> str:
    if state["verdict"] == "accept":
        return END
    elif state["verdict"] == "second_draft" and not state["second_draft_used"]:
        return "draft"
    else:
        return END

scene_graph.add_conditional_edges("commit", route_after_commit, {
    "draft": "draft",
    "end": END,
})

# Compile WITH checkpointer
with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
    compiled_scene = scene_graph.compile(checkpointer=checkpointer)
```

**Gotcha:** Use `SqliteSaver`, NOT `AsyncSqliteSaver`. AsyncSqliteSaver is not production-safe per LangGraph docs (can lose checkpoints under concurrent writes). SYNCHRONOUS SqliteSaver with WAL mode is sufficient.

**Gotcha:** Always use `with SqliteSaver.from_conn_string(...)` context manager pattern. Don't create SqliteSaver without context manager.

---

#### Invoke and Stream

```python
# Stream mode for progress tracking
config = {
    "configurable": {
        "thread_id": f"scene-{chapter_id}-{scene_id}"
    }
}

async for event in compiled_scene.astream(
    input_state,
    config,
    stream_mode=["updates", "custom"]
):
    if event["type"] == "updates":
        print(f"Node {event['node']}: {event['values'].keys()}")
    elif event["type"] == "custom":
        # Custom emit() calls from nodes
        print(f"Custom: {event['data']}")

# Or blocking invoke for testing:
result = compiled_scene.invoke(input_state, config)
```

---

#### TypedDict State with Annotated Reducers

```python
from typing_extensions import TypedDict, Annotated
import operator

class SceneState(TypedDict):
    # Regular fields (merge by overwrite)
    universe_id: str
    book_number: int
    chapter_number: int
    scene_number: int

    # Fields that ACCUMULATE (use Annotated[list, operator.add])
    # When parallel nodes both return a fact, they merge via operator.add
    extracted_facts: Annotated[list, operator.add]
    style_observations: Annotated[list, operator.add]
    quality_trace: Annotated[list, operator.add]
```

**Gotcha:** Parallel nodes can ONLY return dicts with list-typed Annotated fields. Non-list fields from parallel nodes will conflict. For SceneState, only orient returns to plan, etc. — no parallelism at scene level. Parallelism happens in commit node (evaluation_channel_1, evaluation_channel_2, evaluation_channel_3) and chapter node (multiple scenes).

---

#### Send API for Fan-Out

```python
from langgraph.types import Send

def commit_node(state: SceneState) -> Send:
    """Fan out to 3 evaluators in parallel."""
    return [
        Send("evaluate_structural", {"state": state, "evaluator_id": 0}),
        Send("evaluate_judge_1", {"state": state, "evaluator_id": 1}),
        Send("evaluate_judge_2", {"state": state, "evaluator_id": 2}),
    ]

# In graph:
def route_commit(state: SceneState) -> str:
    if all(v["completed"] for v in state["evaluation_results"]):
        return "merge_verdicts"
    return "wait"

graph.add_conditional_edges("commit", route_commit, {
    "wait": "commit",  # No-op, just wait for all evaluators
    "merge": "merge_verdicts"
})
```

---

### 3.2 SqliteSaver Checkpointer

**Pattern:**
```python
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

# Create/open checkpoints.db with WAL mode
conn = sqlite3.connect("checkpoints.db")
conn.execute("PRAGMA journal_mode=WAL;")  # Write-ahead logging
conn.close()

# Use in graph compilation
with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
    compiled_graph = graph.compile(checkpointer=checkpointer)

# Stream with config thread_id
config = {"configurable": {"thread_id": "unique-id"}}
for output in compiled_graph.stream(input_state, config):
    ...

# Resume from checkpoint
checkpoint_id = "some-checkpoint-id"
resume_config = {"configurable": {"thread_id": "...", "checkpoint_id": checkpoint_id}}
for output in compiled_graph.stream({}, resume_config):
    ...
```

**Gotcha:** WAL mode requires `pragma journal_mode=WAL` on the connection that creates the DB, not inside the SqliteSaver. Set it once at init.

**Gotcha:** Checkpoints are identified by `thread_id` + `checkpoint_id`. If you don't provide checkpoint_id, it resumes from the latest. To resume from a specific checkpoint, query the checkpoints table first or track checkpoint IDs in your logs.

---

### 3.3 LanceDB

**Pattern:**
```python
import lancedb

# SINGLETON CONNECTION — reuse it
db = lancedb.connect("data/")

# Create/open table
table = db.create_table("prose_chunks", data=[
    {"chunk_id": "1", "text": "...", "embedding": [...], "scene_id": "1-1-1"},
], mode="overwrite")

# Insert
table.add([{"chunk_id": "2", "text": "...", "embedding": [...]}])

# Search (vector similarity)
results = table.search([embedding_vector]).limit(5).to_list()

# Filter + search
results = table.search([embedding]).where("scene_id = '1-1-1'").limit(10).to_list()
```

**Gotcha:** LanceDB is in-process, serverless. `db.connect("path")` returns a connection object. Don't create it repeatedly in context managers. Store it as a singleton in your provider/retrieval layer and reuse across the session.

**Gotcha:** Embeddings must be pre-computed. LanceDB doesn't call an embedding model; you must provide numpy arrays. Use a local embedding model (e.g., `sentence-transformers`) or pre-compute at ingestion time.

---

### 3.4 igraph Leiden Community Detection

**Pattern:**
```python
import igraph as ig

# Build graph from relationships
edges = [(source_id, target_id) for source_id, target_id, weight in relationships]
weights = [weight for _, _, weight in relationships]

graph = ig.Graph.TupleList(edges, directed=False, weights=weights)

# Apply Leiden
partition = graph.community_leiden(
    objective_function="modularity",
    resolution=1.0,  # Tunable per universe (higher = more, smaller communities)
    n_iterations=10,
)

# Extract communities
communities = []
for cluster_indices in partition:
    entities = [graph.vs[i]["name"] for i in cluster_indices]
    communities.append({"entities": entities})
```

**Gotcha:** Use igraph's built-in `community_leiden()`. If you need advanced Leiden (with `resolution_parameter` tuning per iteration), you'd need `pip install leidenalg` separately. For MVP, built-in is sufficient and 20x faster than Louvain.

**Gotcha:** igraph graphs use integer vertex indices, not names. When building from entity relationships, use `TupleList` and ensure vertex IDs are stable.

---

### 3.5 Clingo ASP Solver

**Pattern:**
```python
from clingo import Control

# Build program (rules + facts)
program = """
% Facts (from world state)
character("Ryn").
character("Ashwater").
location("Northern_Pass").

% Rules (constraints)
can_be_in(C, L) :- character(C), location(L), not imprisoned(C).
imprisoned(C) :- captured(C, _).

% Denial of service
:- visited(C, L), visited(C, L2), L != L2, time(T), time(T2), T < T2 < T + 10.
    % Character cannot be in two places within 10 time units

% Query
#show can_be_in/2.
#show imprisoned/1.
"""

# Solve
ctl = Control()
ctl.add("base", [], program)
ctl.ground([("base", [])])

with ctl.solve(yield_=True) as handle:
    for model in handle:
        # model.atoms(shown=True) returns the #show results
        facts = model.atoms(shown=True)
        print(facts)
```

**Gotcha:** Grounding is the bottleneck. For large rule sets (100+ rules), grounding can take seconds. Use incremental multi-shot solving if you're validating multiple scenes: `ctl.ground([("step", [1])])` then `ctl.ground([("step", [2])])` adds facts incrementally.

**Gotcha:** Clingo syntax is strict. Variables are capitalized, predicates lowercase. Comments are `%`. Test your program with `clingo` CLI first before embedding.

**Gotcha:** If no model exists (constraints unsatisfiable), `solve()` returns UNSAT. Handle both cases:

```python
with ctl.solve(yield_=True) as handle:
    if handle.get().satisfiable:
        for model in handle:
            facts = model.atoms(shown=True)
    else:
        # Unsatisfiable — constraints violated
        print("Scene plan violates constraints")
```

---

### 3.6 pystray System Tray

**Pattern:**
```python
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw

# Create icon image
image = Image.new("RGB", (64, 64), color=(73, 109, 137))
draw = ImageDraw.Draw(image)
draw.text((10, 10), "FA", fill=(255, 255, 255))

# Build menu
menu = Menu(
    MenuItem("Status: Idle", lambda: None),
    MenuItem("Pause", pause_handler, enabled=paused_event),
    MenuItem("Resume", resume_handler, enabled=not paused_event),
    MenuItem("Open Output", open_folder_handler),
    MenuItem("Settings", settings_handler),
    MenuItem("Quit", quit_handler),
)

# Create icon
icon = Icon("Fantasy Author", image, menu=menu)

# Run detached (non-blocking)
icon.run_detached()

# Later: update status
icon.update_menu(
    Menu(MenuItem(f"Status: {current_chapter}", lambda: None), ...)
)

# On quit:
icon.stop()
```

**Gotcha:** Use `icon.run_detached()`, NOT `icon.run()`. The blocking version locks the event loop. For async integration with LangGraph, you need detached so the daemon thread can yield to the main async loop.

**Gotcha:** pystray needs Pillow for icon images. The image must be a PIL.Image.Image object, not a path string.

**Gotcha:** Menu updates are not real-time while the menu is open. Update before the user opens the menu again.

---

### 3.7 Provider Router & CLI Subprocesses

#### claude -p Pattern

```python
import asyncio
import json

async def call_claude_prose(prompt: str, system: str) -> str:
    """Call claude -p subprocess for prose generation."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full_input = f"SYSTEM: {system}\n\nUSER: {prompt}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(full_input.encode()),
            timeout=300  # 5 minutes
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise ProviderTimeoutError("claude -p hung for 5 minutes")

    if proc.returncode == 1:
        # Usually means API unavailable; apply sticky cooldown
        raise ProviderUnavailableError("claude -p returned exit code 1 (likely API unreachable)")

    if proc.returncode != 0:
        raise ProviderError(f"claude -p exit {proc.returncode}: {stderr.decode()}")

    return stdout.decode()

async def call_claude_json(prompt: str, system: str) -> dict:
    """Use claude -p with --output-format json for structured output."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--output-format", "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full_input = f"SYSTEM: {system}\n\nUSER: {prompt}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(full_input.encode()),
            timeout=300
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise ProviderTimeoutError("claude -p hung")

    if proc.returncode != 0:
        raise ProviderError(f"claude -p failed: {stderr.decode()}")

    return json.loads(stdout.decode())
```

**Gotcha:** Use `asyncio.create_subprocess_exec`, NOT `subprocess.run`. The async version doesn't block the event loop.

**Gotcha:** Exit code 1 in <5 seconds usually means API unavailable (rate limit, auth failure, network down). Treat as a provider cooldown trigger, not a task failure. The router will retry with the next provider.

**Gotcha:** `--output-format json` works but may require wrapping the prompt to instruct the model to output valid JSON. Better to parse structured responses from text.

---

#### codex exec Pattern

```python
async def call_codex_refactor(prompt: str, system: str) -> str:
    """Call codex exec for code/outline tasks."""
    proc = await asyncio.create_subprocess_exec(
        "codex", "exec", "--full-auto",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full_input = f"SYSTEM: {system}\n\nUSER: {prompt}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(full_input.encode()),
            timeout=300
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise ProviderTimeoutError("codex exec hung")

    # Progress streams to stderr, final result to stdout
    if proc.returncode != 0:
        raise ProviderError(f"codex exec failed: {stderr.decode()}")

    return stdout.decode()
```

---

#### Provider Router with Fallback Chains

```python
class ProviderRouter:
    FALLBACK_CHAINS = {
        "writer": ["claude-code", "codex", "gemini-free", "groq-free", "ollama-local"],
        "judge": ["codex", "gemini-free", "groq-free", "ollama-local"],
        "extract": ["ollama-local", "groq-free", "gemini-free"],
        "embed": ["ollama-local"],  # No remote embeddings
    }

    def __init__(self):
        self.quota = QuotaTracker()
        self.cooldowns = {}
        self.last_activity = {}

    async def call(self, prompt: str, system: str, role: str = "writer") -> str:
        """Route call through fallback chain until success."""
        chain = self.FALLBACK_CHAINS.get(role, self.FALLBACK_CHAINS["writer"])

        for provider_name in chain:
            if self.quota.is_cooldown(provider_name):
                continue
            if not self.quota.available(provider_name):
                continue

            try:
                result = await self._call_provider(provider_name, prompt, system)
                self.quota.record_success(provider_name)
                return result
            except ProviderUnavailableError as e:
                # Sticky cooldown: don't retry this provider for 30 minutes
                self.cooldowns[provider_name] = time.time() + 1800
                continue
            except ProviderTimeoutError:
                # Activity timeout: try next provider
                continue
            except Exception as e:
                # Other errors: log and continue
                continue

        # All providers exhausted
        if role == "judge":
            # Never block for judges; return degraded
            return ProviderResponse(text="", degraded=True)

        raise AllProvidersExhaustedError(f"All {role} providers failed")

    async def _call_provider(self, provider: str, prompt: str, system: str) -> str:
        if provider == "claude-code":
            return await call_claude_prose(prompt, system)
        elif provider == "codex":
            return await call_codex_refactor(prompt, system)
        elif provider == "gemini-free":
            return await call_gemini_free(prompt, system)
        elif provider == "groq-free":
            return await call_groq_free(prompt, system)
        elif provider == "ollama-local":
            return await call_ollama_local(prompt, system)
        else:
            raise ValueError(f"Unknown provider: {provider}")
```

**Gotcha:** Track which provider you used for which call. For judge ensemble diversity, enforce that judges come from different families (Anthropic, Google, Groq, Open Source). Otherwise, fallback chains cause all judges to use the same family when one is unavailable.

---

### 3.8 LangGraph Store API (Optional, Phase 5+)

**Pattern:**
```python
# Store is a persistent key-value store, namespaced
# Invoke with: store=RxStore (injected by LangGraph)

def consolidate_node(state: ChapterState, *, store) -> ChapterState:
    """Store facts in memory hierarchy."""

    # Store core memory (active context)
    store.put(
        ("memory", "core", "characters"),
        key=state["active_character"],
        value={
            "name": state["active_character"],
            "emotional_state": state["character_emotion"],
            "location": state["character_location"],
        }
    )

    # Store episodic memory (scene summaries)
    for fact in state["extracted_facts"]:
        if fact["confidence"] > 0.8:  # Only confident facts
            store.put(
                ("memory", "episodic", "facts"),
                key=f"{fact['id']}",
                value=fact
            )

    # Retrieve core memory for next chapter
    core_chars = store.search(
        ("memory", "core", "characters"),
        limit=5
    )

    return {
        **state,
        "core_memory": core_chars,
    }
```

**Gotcha:** Store API is optional. Use SQLite or LanceDB if simpler for your use case. Store adds a managed namespace layer but requires LangGraph's store implementation.

---

## 4. Golden Test Strategy

**Principle:** Establish a reference output early. As you add components, regenerate and diff against reference.

### 4.1 Reference Chapter

Create a **reference chapter** from a known premise BEFORE building any components:

1. **Premise:** "A scout discovers a hidden waterfall in a cursed forest. She must decide whether to report it to her faction."

2. **Rich source:** 2-3 pages of world-building (faction rules, curse mechanics, character voice samples).

3. **Hand-written reference:** Write ~1,500 words of how you'd expect this scene to unfold (or use a published passage of similar quality).

4. **Metrics to track:**
   - **Word count:** Reference = 1,500 words. After each phase, new generation should be ±10%.
   - **Fact consistency:** List all stated facts (location, character knowledge, timeline). New generation should match or explain divergence.
   - **Character voice:** Collect dialogue/internal monologue from reference. New generation should echo same voice patterns.
   - **Structural quality:** TAACO readability score (should be >0.5 for prose). Pacing rhythm (dialogue:narration ratio, description depth).

### 4.2 Regeneration After Each Phase

After phase completion, regenerate the reference chapter and diff:

```python
def test_golden_chapter_phase_N():
    """Regenerate reference chapter after each phase."""
    input_state = {
        "universe_id": "golden-test",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": 1,
        "scene_prompt": golden_reference_prompt,
        "world_source": golden_reference_source,
    }

    result = compiled_scene.invoke(input_state, config)

    # Compare against reference
    gold_output = load_golden_output("phase_N")

    assert len(result["draft_output"]["prose"]) > 1000, "Prose too short"

    # Fact consistency
    gold_facts = extract_facts(gold_output)
    new_facts = extract_facts(result["draft_output"]["prose"])
    assert fact_overlap(gold_facts, new_facts) > 0.8, "Facts drifted"

    # Voice consistency
    gold_voice = voice_profile(gold_output)
    new_voice = voice_profile(result["draft_output"]["prose"])
    assert voice_similarity(gold_voice, new_voice) > 0.7, "Voice changed"

    # Save for next phase comparison
    save_golden_output(f"phase_{N+1}", result["draft_output"]["prose"])
```

### 4.3 Test Fixtures

Maintain 3 fixtures:

1. **Rich source:** Multi-page canon (world rules, character profiles, writing samples)
   - Use: EXTRACT mode constraint synthesis testing, KG ingest testing

2. **Sparse prompt:** 2-3 sentences
   - Use: GENERATE mode constraint synthesis, minimal context retrieval

3. **Mid-length premise:** 1 page (outline + character sketch + key tension)
   - Use: Typical user input; test default path

### 4.4 Unit Tests Per Module

Before integration testing, unit test each component:

**Graph topology:**
```python
def test_scene_graph_compiles():
    """Verify graph has all nodes, edges, valid entry point."""
    assert scene_graph.nodes == {"orient", "plan", "draft", "commit"}
    assert scene_graph.edges  # Check edges exist
    compiled = scene_graph.compile()
    assert compiled is not None
```

**Node contracts:**
```python
def test_orient_node_returns_state_with_warnings():
    """Verify orient output structure."""
    state = {"universe_id": "test", ...}
    result = orient_node(state)
    assert "warnings" in result
    assert isinstance(result["warnings"], list)
    for w in result["warnings"]:
        assert "type" in w and "text" in w
```

**Provider fallback:**
```python
@pytest.mark.asyncio
async def test_router_falls_back_to_next_provider():
    """Verify provider router skips unavailable providers."""
    router = ProviderRouter()
    router.cooldowns["claude-code"] = time.time() + 1000  # Simulate cooldown

    result = await router.call("test prompt", "system", role="writer")
    # Should use codex (second in chain)
    assert result is not None
```

**Leiden communities:**
```python
def test_leiden_finds_communities():
    """Verify community detection works on test graph."""
    edges = [("A", "B"), ("B", "C"), ("C", "A"), ("D", "E")]
    partition = detect_communities_leiden(edges)
    assert len(partition) >= 2  # At least 2 communities
    assert all(len(c) > 0 for c in partition)  # Non-empty
```

**ASP validation:**
```python
def test_asp_validates_scene_plan():
    """Verify ASP catches constraint violations."""
    program = """
    character("Ryn").
    location("Pass").
    :- visited("Ryn", "Pass"), visited("Ryn", "Castle"), time_diff < 60.
    """

    # Plan that violates constraint
    plan_violating = {"visited": [("Ryn", "Pass"), ("Ryn", "Castle")], "time_diff": 30}
    assert not asp_validate(program, plan_violating), "Should catch violation"

    # Plan that satisfies
    plan_ok = {"visited": [("Ryn", "Pass")], "time_diff": 100}
    assert asp_validate(program, plan_ok), "Should accept valid plan"
```

---

## 5. Critical Research Insights for the Builder

These findings affect implementation decisions and must be understood before you code.

### 5.1 ASP Is Research-Stage, Not Production-Critical

- **ASP (Clingo)** is powerful for validation but adds complexity. It's scaffolding for quality, not a blocker.
- In Phase 4, build it but mark as optional. If ASP grounding becomes a bottleneck, the system continues with LLM-only planning.
- **Decision:** If ASP validation catches zero violations in the first 10 chapters, consider it nice-to-have. Don't optimize prematurely.

### 5.2 HTN Planning Doesn't Scale to Prose Generation

- HTN is excellent for decomposing goals → acts → beats, but it's **planning scaffolding, not text generation**.
- Use HTN to generate a beat outline from a high-level goal. Feed that outline to the LLM for prose.
- Don't try to make HTN output prose directly; the symbolic layer should inform the neural layer, not replace it.

### 5.3 200K Context Windows Are Sufficient for Single Books Without Full KG

- Tests show 200K contexts can hold ~50K words of prose + world state + recent facts without retrieval.
- KG becomes **essential** at 5+ chapters or multi-book series (when context doesn't fit all canon + recent facts).
- **Decision:** In Phase 0-2, don't optimize for large context. Ship Phase 1 with simple retrieval (last N scenes). KG retrieval (Phase 2) is an optimization, not a necessity.

### 5.4 LLMs Are Good Judges for Mechanical Coherence, Terrible for Nuanced Creativity

- LLMs can evaluate: word count stability, timeline consistency, character name consistency, dialogue realism.
- LLMs are weak at: emotional arc pacing, thematic depth, originality, voice consistency across long passages.
- **Decision:** Build deterministic evaluation (TAACO, Chekhov checks) first. Add LLM judges for secondary signals. Treat LLM judgments as signals, not verdicts.

### 5.5 Voice Consistency and Tension Curves Over 100K+ Words Are Unsolved Problems

- No research shows a system that maintains consistent voice across 100K+ words of neural generation.
- Tension curves (emotional pacing) degrade noticeably after chapter 5 without explicit scaffolding (outline beats, milestone checks).
- **Decision:** Accept graceful degradation. After chapter 5, offer "second draft" pass for chapters 1-2 to reinforce voice. Use Reflexion to catch drift and auto-correct.

### 5.6 Fiction KGs Need Truth-Value Typing

- A naive knowledge graph stores all extracted facts as equal. For fiction, this fails: character lies, prophecies that seem true but aren't, hidden facts the narrator doesn't know.
- Every extracted fact needs: `source_type` (narrator_claim | author_fact | character_belief | world_truth), `narrator_reliability` (0.0-1.0), `truth_value_initial/final/revealed`.
- **Decision:** Implement FactWithContext (see section 7) from Phase 1. Don't extract to a plain fact store.

### 5.7 Phase-Aware Retrieval Is Non-Negotiable

- The **orient** node needs character states and relationships (KG query).
- The **plan** node needs outline context and style rules (archival query).
- The **draft** node needs tone/voice examples and sensory details (vector search).
- The **evaluate** node needs canon facts and constraints (KG + ASP query).
- **Decision:** Build a PhaseAwareRouter (see section 8) that changes retrieval strategy per node. Don't use one-size-fits-all RAG.

### 5.8 Context Contamination (Epistemic Filtering) Is the #1 Failure Mode

- Without epistemic filtering, the draft node writes a character knowing information they shouldn't (they haven't encountered that fact yet).
- Example: A secret is revealed in chapter 5. The draft node in chapter 3 pulls "character learned about secret" from the vector index and writes it.
- **Decision:** Implement access_tier on every KG edge and fact. In Phase 2, enforce epistemic filtering: `if knowledge not in pov_character.accessible_facts, don't retrieve it`.

### 5.9 Unreliable Narrator Handling Requires Explicit Tracking

- A character lies in their internal monologue. Naive extraction treats it as world truth.
- Solution: Tag every fact with `narrator` (who stated it) and `narrator_reliability`. Queries filter based on POV character's belief in the narrator.
- Example: Ryn's narration says "the Glass Treaty is sacred law." Ashwater's character sheet says it's "a tool for political control." Both are true (from different POVs), but the draft node should use Ryn's version if Ryn is POV, Ashwater's version if Ashwater is POV.

### 5.10 Metaphor and Language-Type Tagging

- "Her heart was stone" should NOT become a fact that the character's heart is literally stone.
- Solution: Tag facts with `language_type` (literal | metaphorical | symbolic | ironic). Don't use metaphorical facts in world consistency checks.

### 5.11 Relationship Stasis Causes Failures

- A relationship is stored as a static edge: "allies: True". When characters become enemies, the graph is not updated.
- Solution: Every relationship edge needs `valid_from_chapter` and `valid_to_chapter`. Queries should filter by current chapter: `show only relationships where chapter >= valid_from AND chapter <= valid_to (or NULL)`.

### 5.12 Lost-in-Middle Problem at 100+ Chapters

- Attention mechanisms (in LLMs and in vector similarity) have "lost in middle" bias: the first 20% and last 20% of context get disproportionate attention.
- For a 100-chapter book, middle chapters get lowest attention in long contexts.
- Solution: Use three-tier memory (hot/warm/cold) from Phase 5. Hot = last 2 chapters. Warm = chapters 1-20 + chapters 80-100 (critical early/late chapters). Cold = full index (retrieve only on demand).

---

## 6. File-by-File Implementation Order

This table specifies **every file** you'll create, in the order to create it. Columns:

- **File:** Absolute path (relative to project root `fantasy_author/`)
- **Phase:** Which build phase (0-7)
- **Priority:** Within phase (1=first, 2=second, etc.)
- **Dependencies:** What must exist first
- **Type:** Stub (pass-through) or Real (production code)
- **Est. LOC:** Estimated lines of code
- **Notes:** Gotchas, patterns to use

| File | Phase | Priority | Dependencies | Type | Est. LOC | Notes |
|------|-------|----------|--------------|------|---------|-------|
| `__init__.py` | 0 | 1 | — | Stub | 5 | Version + exports |
| `exceptions.py` | 0 | 1 | — | Real | 30 | Define ProviderError, AllProvidersExhausted, ProviderTimeoutError, etc. |
| `config.py` | 0 | 2 | — | Real | 50 | Paths, settings, logging config. Mock provider chain. |
| `state/types.py` | 0 | 3 | — | Real | 200 | SceneState, ChapterState, BookState, UniverseState TypedDicts. All Annotated reducers. |
| `models.py` | 0 | 4 | state/types.py | Real | 150 | ProviderResponse, Fact, FactWithContext, NarrativePromise, ConstraintSurface. |
| `persistence/schema.py` | 0 | 5 | — | Real | 150 | SQLite schema: fact_store, promise_store, world_state, character_states. CREATE TABLE + init logic. |
| `persistence/__init__.py` | 0 | 5 | persistence/schema.py | Stub | 10 | Exports |
| `graphs/scene.py` | 0 | 6 | state/types.py, config.py | Real | 80 | StateGraph topology: orient → plan → draft → commit. No node impls yet. |
| `graphs/chapter.py` | 0 | 7 | graphs/scene.py | Real | 60 | StateGraph: run_scene → consolidate → learn. Conditional edges. |
| `graphs/book.py` | 0 | 8 | graphs/chapter.py | Real | 70 | StateGraph: run_chapter → diagnose → book_close. Stuck detection. |
| `graphs/universe.py` | 0 | 9 | graphs/book.py | Real | 90 | StateGraph: select_task → (run_book \| worldbuild \| reflect). Task queue logic. |
| `graphs/__init__.py` | 0 | 10 | All graphs | Stub | 10 | Exports compiled graphs |
| `nodes/orient.py` | 0 | 11 | state/types.py, persistence/schema.py | Stub | 20 | Stub: return empty warnings |
| `nodes/plan.py` | 0 | 11 | state/types.py | Stub | 20 | Stub: return trivial beat sheet |
| `nodes/draft.py` | 0 | 11 | state/types.py | Stub | 20 | Stub: return placeholder prose |
| `nodes/commit.py` | 0 | 11 | state/types.py, models.py | Stub | 20 | Stub: accept everything, no evaluation |
| `nodes/__init__.py` | 0 | 12 | All nodes | Stub | 10 | Exports |
| `__main__.py` | 0 | 13 | graphs/universe.py, config.py | Real | 50 | CLI entry: load universe path, compile graphs, invoke with checkpoint, print progress |
| `tests/__init__.py` | 0 | 14 | — | Stub | 5 | — |
| `tests/test_graphs/test_scene_topology.py` | 0 | 15 | graphs/scene.py | Real | 80 | Test: scene graph compiles, nodes exist, edges valid, entry point set |
| `tests/test_graphs/test_full_flow.py` | 0 | 16 | All nodes, graphs | Real | 100 | Test: full scene runs end-to-end, state merges, checkpoint saves |
| | | | | | | |
| `providers/__init__.py` | 1 | 1 | — | Stub | 10 | Exports |
| `providers/base.py` | 1 | 2 | models.py, exceptions.py | Real | 80 | BaseProvider class, call contract |
| `providers/mock.py` | 1 | 3 | providers/base.py | Real | 50 | MockProvider for testing (no API calls) |
| `providers/router.py` | 1 | 4 | providers/base.py, exceptions.py | Real | 120 | ProviderRouter: fallback chains, quota tracking, provider selection |
| `nodes/orient.py` (real) | 1 | 5 | providers/router.py, persistence/schema.py | Real | 100 | Real: query DB for promises/pacing, return warnings. No LLM. Deterministic. |
| `nodes/plan.py` (real) | 1 | 6 | providers/router.py, models.py | Real | 150 | Real: call provider for 3-5 beat alternatives, score deterministically, return beat sheet |
| `nodes/draft.py` (real) | 1 | 7 | providers/router.py | Real | 150 | Real: call provider for prose from beat sheet. Optional refresh. |
| `extraction/extractor.py` | 1 | 8 | models.py, providers/router.py | Real | 120 | Fact extraction from prose: LLM-based with fallback regex. Store to FactWithContext. |
| `nodes/commit.py` (real) | 1 | 9 | extraction/extractor.py, models.py | Real | 200 | Real: deterministic evaluation (TAACO, pacing, timeline, promise coverage). Extract facts. Return verdict. |
| `persistence/promise_store.py` | 1 | 10 | persistence/schema.py, models.py | Real | 80 | Promise DB: add, query by status, resolve, expire. |
| `persistence/__init__.py` (update) | 1 | 11 | All persistence modules | Stub | 20 | Exports |
| `tests/test_providers/test_router.py` | 1 | 12 | providers/router.py | Real | 100 | Test: fallback chains, quota, provider selection |
| `tests/test_nodes/test_orient.py` | 1 | 13 | nodes/orient.py | Real | 80 | Test: orient returns warnings structure |
| `tests/test_nodes/test_plan.py` | 1 | 14 | nodes/plan.py | Real | 100 | Test: plan generates beat sheet with alternatives |
| `tests/test_nodes/test_draft.py` | 1 | 15 | nodes/draft.py | Real | 100 | Test: draft generates prose with word count stability |
| `tests/test_extraction/test_extractor.py` | 1 | 16 | extraction/extractor.py | Real | 80 | Test: extract facts from prose, FactWithContext structure |
| | | | | | | |
| `retrieval/__init__.py` | 2 | 1 | — | Stub | 10 | Exports |
| `retrieval/kg_builder.py` | 2 | 2 | models.py, providers/router.py | Real | 150 | Entity extraction + relationship building. Build igraph. |
| `retrieval/leiden.py` | 2 | 3 | retrieval/kg_builder.py | Real | 80 | Leiden community detection. Query communities. |
| `retrieval/hipporag.py` | 2 | 4 | retrieval/leiden.py | Real | 100 | Personalized PageRank on KG. Entity-relationship queries. |
| `retrieval/vector_store.py` | 2 | 5 | models.py | Real | 120 | LanceDB wrapper. Insert/search prose chunks. BM25 + dense embeddings. |
| `retrieval/embedder.py` | 2 | 6 | retrieval/vector_store.py | Real | 60 | Local embedding model (sentence-transformers). Batch embed. |
| `retrieval/agentic_router.py` | 2 | 7 | retrieval/hipporag.py, retrieval/vector_store.py | Real | 140 | Decompose compound queries. Route to HippoRAG / vector / RAPTOR. Merge results. |
| `nodes/orient.py` (update) | 2 | 8 | retrieval/agentic_router.py | Real | 150 | Update orient to use agentic retrieval instead of DB queries |
| `nodes/draft.py` (update) | 2 | 9 | retrieval/agentic_router.py | Real | 130 | Update draft to retrieve tone examples from vector store |
| `tests/test_retrieval/test_kg_builder.py` | 2 | 10 | retrieval/kg_builder.py | Real | 100 | Test: entity graph builds, relationships form |
| `tests/test_retrieval/test_leiden.py` | 2 | 11 | retrieval/leiden.py | Real | 80 | Test: communities detect, are non-empty |
| `tests/test_retrieval/test_hipporag.py` | 2 | 12 | retrieval/hipporag.py | Real | 100 | Test: PPR queries return relevant entities |
| `tests/test_retrieval/test_vector_store.py` | 2 | 13 | retrieval/vector_store.py | Real | 80 | Test: insert chunks, search by similarity |
| | | | | | | |
| `symbolic/__init__.py` | 3 | 1 | — | Stub | 10 | Exports |
| `symbolic/quota.py` | 3 | 1 | config.py | Real | 100 | QuotaTracker: track rate limits, cooldowns, availability per provider |
| `providers/router.py` (update) | 3 | 2 | symbolic/quota.py | Real | 160 | Integrate quota tracking. Judge ensemble diversity. |
| `nodes/commit.py` (update) | 3 | 3 | providers/router.py | Real | 250 | Parallel judges via Send API. Merge verdicts. Fallback to quality floor. |
| `tests/test_providers/test_quota.py` | 3 | 4 | symbolic/quota.py | Real | 80 | Test: quota tracking, cooldown, availability |
| `tests/test_nodes/test_commit_ensemble.py` | 3 | 5 | nodes/commit.py | Real | 120 | Test: multiple judges, diversity enforcement, consensus |
| | | | | | | |
| `symbolic/asp_engine.py` | 4 | 1 | models.py | Real | 150 | Clingo wrapper. Encode rules, ground, solve, validate. |
| `symbolic/htn_planner.py` | 4 | 2 | models.py, symbolic/asp_engine.py | Real | 140 | HTN goal decomposition. Output beat outlines. |
| `symbolic/dome.py` | 4 | 3 | retrieval/agentic_router.py, models.py | Real | 160 | DOME outline expansion. Recursive deepening with KG feedback. |
| `symbolic/constraint_synthesizer.py` | 4 | 4 | symbolic/asp_engine.py, symbolic/htn_planner.py, symbolic/dome.py | Real | 180 | EXTRACT / GENERATE modes. Output ConstraintSurface. |
| `graphs/universe.py` (update) | 4 | 5 | symbolic/constraint_synthesizer.py | Real | 120 | Add constraint_synthesis subgraph before first outline node. |
| `nodes/plan.py` (update) | 4 | 6 | symbolic/asp_engine.py | Real | 140 | Validate plan beats against ASP rules. Reject unsatisfiable plans. |
| `tests/test_symbolic/test_asp_engine.py` | 4 | 7 | symbolic/asp_engine.py | Real | 100 | Test: program grounds, solves, catches violations |
| `tests/test_symbolic/test_htn_planner.py` | 4 | 8 | symbolic/htn_planner.py | Real | 100 | Test: decomposes goals, produces outlines |
| `tests/test_symbolic/test_dome.py` | 4 | 9 | symbolic/dome.py | Real | 100 | Test: expands sparse outline, deepens iteratively |
| `tests/test_symbolic/test_constraint_synthesizer.py` | 4 | 10 | symbolic/constraint_synthesizer.py | Real | 120 | Test: EXTRACT / GENERATE modes, output structure |
| | | | | | | |
| `memory/__init__.py` | 5 | 1 | — | Stub | 10 | Exports |
| `memory/store.py` | 5 | 2 | state/types.py, persistence/schema.py | Real | 150 | Core/episodic/archival memory. Promotion gates. |
| `memory/reflexion.py` | 5 | 3 | memory/store.py, models.py | Real | 120 | Self-critique on revert. Verbal reflection. Memory weight updates. |
| `nodes/consolidate.py` | 5 | 4 | memory/store.py, memory/reflexion.py | Real | 140 | Fact promotion, memory consolidation, sliding window cleanup. |
| `graphs/chapter.py` (update) | 5 | 5 | nodes/consolidate.py, memory/reflexion.py | Real | 100 | Add reflexion to chapter flow. Update consolidate logic. |
| `tests/test_memory/test_store.py` | 5 | 6 | memory/store.py | Real | 100 | Test: core/episodic/archival separation, promotion gates |
| `tests/test_memory/test_reflexion.py` | 5 | 7 | memory/reflexion.py | Real | 100 | Test: self-critique generates reflection, updates memory |
| | | | | | | |
| `app/__init__.py` | 6 | 1 | — | Stub | 10 | Exports |
| `app/daemon.py` | 6 | 2 | graphs/universe.py, config.py | Real | 150 | FantasyAuthorDaemon: run loop, checkpointing, event emission. |
| `app/tray.py` | 6 | 3 | app/daemon.py | Real | 120 | pystray system tray. Menu, status updates, notifications. |
| `app/main.py` | 6 | 4 | app/daemon.py, app/tray.py | Real | 100 | CLI launcher. Daemon + tray initialization. |
| `tests/test_app/test_daemon.py` | 6 | 5 | app/daemon.py | Real | 100 | Test: daemon starts, pauses, resumes cleanly |
| `tests/test_app/test_tray.py` | 6 | 6 | app/tray.py | Real | 80 | Test: tray icon creates, menu appears |
| | | | | | | |
| `ingest/__init__.py` | 7 | 1 | — | Stub | 10 | Exports |
| `ingest/progressive.py` | 7 | 2 | retrieval/kg_builder.py, retrieval/vector_store.py | Real | 130 | Progressive canon ingestion. Non-blocking file watch. |
| `ingest/validator.py` | 7 | 3 | models.py, symbolic/asp_engine.py | Real | 100 | Validate ingested canon against ASP rules. |
| `nodes/book_close.py` | 7 | 4 | memory/store.py, state/types.py | Real | 120 | Book-level consolidation, cross-book promises, arc closure. |
| `nodes/diagnose.py` | 7 | 5 | state/types.py, nodes/plan.py | Real | 130 | Stuck recovery: replan, worldbuild intro, constraint deepening. |
| `graphs/book.py` (update) | 7 | 6 | nodes/book_close.py, nodes/diagnose.py | Real | 100 | Integrate book_close, diagnose. Stuck recovery paths. |
| `tests/test_ingest/test_progressive.py` | 7 | 7 | ingest/progressive.py | Real | 100 | Test: canon files ingest, KG updates, vectors index |
| `tests/test_end_to_end/test_full_book.py` | 7 | 8 | All modules | Real | 200 | Test: full 50K-word book generates end-to-end with metrics |

---

## 7. FactWithContext Dataclass (Fiction-Specific)

**This replaces naive Fact extraction.** Implement from Phase 1.

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class SourceType(str, Enum):
    """Who stated this fact and in what context?"""
    NARRATOR_CLAIM = "narrator_claim"      # Character's internal monologue/narration
    AUTHOR_FACT = "author_fact"            # Objectively true in the world
    CHARACTER_BELIEF = "character_belief"  # What a character thinks, may be wrong
    WORLD_TRUTH = "world_truth"            # Locked facts from worldbuilding docs

class LanguageType(str, Enum):
    """How is this fact expressed?"""
    LITERAL = "literal"                    # "The sky is red" means the sky is red
    METAPHORICAL = "metaphorical"          # "Her heart was stone" ≠ literal stone heart
    SYMBOLIC = "symbolic"                  # "The white raven" = purity + isolation
    IRONIC = "ironic"                      # "Great choice!" means bad choice

class NarrativeFunction(str, Enum):
    """What role does this fact play in the story?"""
    WORLD_FACT = "world_fact"              # Unchanging world property
    FORESHADOWING = "foreshadowing"        # Hints at future event
    MISDIRECTION = "misdirection"          # Suggests one outcome, subverts expectation
    CHARACTER_DEVELOPMENT = "character_dev" # Shows character growth/change

class TruthValue(str, Enum):
    """Has this fact's truth been revealed?"""
    INITIAL = "initial"                    # What it seemed at first encounter
    FINAL = "final"                        # What it actually is
    REVEALED = "revealed"                  # Chapter where truth was revealed

@dataclass
class FactWithContext:
    """Fiction-aware fact with truth-value typing and temporal bounds."""

    # Identity
    fact_id: str                           # Unique ID (e.g., "fact_1_2_3")
    text: str                              # The fact itself

    # Truth-value typing (CRITICAL FOR FICTION)
    source_type: SourceType                # Who stated this?
    narrator: Optional[str] = None         # If narrator_claim, who narrated?
    narrator_reliability: float = 1.0      # 0.0 (always lies) to 1.0 (always truthful)

    # Temporal bounds
    valid_from_chapter: Optional[int] = None  # First chapter where true
    valid_to_chapter: Optional[int] = None    # Last chapter where true (None = still valid)

    # Truth evolution
    truth_value_initial: Optional[str] = None    # What it seemed at first
    truth_value_final: Optional[str] = None      # What it turned out to be
    truth_value_revealed: Optional[int] = None   # Chapter where truth revealed

    # Expression
    language_type: LanguageType = LanguageType.LITERAL
    narrative_function: NarrativeFunction = NarrativeFunction.WORLD_FACT

    # Metadata
    importance: float = 0.5                 # 0.0 (color detail) to 1.0 (plot-critical)

    # Knowledge graph integration
    weight: str = "color"                   # "causal"|"promise"|"identity"|"rule"|"state"|"color"
    hardness: str = "soft"                  # "hard" (unchanging) | "soft" (can change)
    horizon: str = "scene"                  # "scene"|"chapter"|"book"|"series"

    # Attribution & confidence
    provenance: str = "generated"           # "author_stated"|"inferred"|"generated"
    confidence: float = 0.5                 # 0.0-1.0 extraction confidence
    seeded_scene: str = ""                  # Which scene generated this

    # Access control (for retrieval filtering)
    access_tier: int = 0                    # 0=public, 1=faction, 2=inner_circle, 3=secret
    pov_characters: list[str] = field(default_factory=list)  # Who can know this? Empty=everyone

    def is_accessible_to(self, character_id: str, character_knowledge_level: int) -> bool:
        """Check if a character should be able to know this fact."""
        # Access by knowledge level
        if character_knowledge_level >= self.access_tier:
            # Also check POV restriction (if list is non-empty, only these chars know)
            if self.pov_characters and character_id not in self.pov_characters:
                return False
            return True
        return False

    def is_valid_at_chapter(self, chapter_number: int) -> bool:
        """Check if this fact is valid at a given chapter."""
        if self.valid_from_chapter and chapter_number < self.valid_from_chapter:
            return False
        if self.valid_to_chapter and chapter_number > self.valid_to_chapter:
            return False
        return True
```

### Usage in Extraction

```python
async def extract_facts_from_prose(prose: str, scene_id: str,
                                   pov_character: str) -> list[FactWithContext]:
    """Extract facts from generated prose using LLM."""

    prompt = f"""Extract facts from this prose. For each fact, determine:
    - What is stated? (text)
    - Who's narrating? ({pov_character})
    - Is it literal, metaphorical, symbolic, or ironic?
    - Is it foreshadowing, misdirection, world fact, or character development?
    - How important? (0.0-1.0)
    - What's the confidence? (0.0-1.0)

    Return JSON list of facts with these fields.

    Prose:
    {prose}"""

    response = await router.call(prompt, EXTRACTION_SYSTEM, role="extract")
    facts_raw = json.loads(response)

    facts = []
    for i, fact_raw in enumerate(facts_raw):
        fact = FactWithContext(
            fact_id=f"{scene_id}_fact_{i}",
            text=fact_raw["text"],
            source_type=SourceType(fact_raw.get("source_type", "narrator_claim")),
            narrator=pov_character,
            narrator_reliability=0.8,  # Tune per character
            language_type=LanguageType(fact_raw.get("language_type", "literal")),
            narrative_function=NarrativeFunction(fact_raw.get("narrative_function", "world_fact")),
            importance=fact_raw.get("importance", 0.5),
            confidence=fact_raw.get("confidence", 0.5),
            seeded_scene=scene_id,
            access_tier=0,  # Default public
            valid_from_chapter=scene_id.split("_")[1],
        )
        facts.append(fact)

    return facts
```

---

## 8. Phase-Aware Retrieval Router Pattern

**Key principle:** The retrieval strategy MUST change based on which graph node is calling. Don't use one-size-fits-all RAG.

```python
class PhaseAwareRouter:
    """Routes retrieval queries based on which graph node is calling.

    Each phase needs different signals:
    - orient: What's happened? Who's where? What are we waiting for?
    - plan: What's the outline context? What are the style rules?
    - draft: What's the voice/tone? Show me similar prose.
    - evaluate: Is this consistent with canon? Do rules hold?
    """

    def __init__(self, kg, vector_store, raptor_tree, asp_engine):
        self.kg = kg              # HippoRAG
        self.vector_store = vector_store  # LanceDB
        self.raptor_tree = raptor_tree
        self.asp_engine = asp_engine

    def retrieve_for_orient(self, state: SceneState) -> dict:
        """Orient phase: Character states, relationships, active promises.

        Orient needs to know:
        - Where is each character? (state)
        - What relationships are active? (KG)
        - What promises are overdue? (DB)
        - Are there timeline warnings? (deterministic check)

        Do NOT retrieve: tone/voice, full prose, plot structure.
        """

        context = {}

        # 1. Query KG for active relationships + community membership
        for char in state.get("pov_characters", []):
            relationships = self.kg.query(
                entity=char,
                query_type="relationships",
                temporal_filter=state["chapter_number"],
                access_tier=state.get(f"{char}_knowledge_level", 0),
            )
            context[f"{char}_relationships"] = relationships

        # 2. Query promise DB (deterministic — no vector search)
        overdue_promises = self._query_promise_db(state)
        context["overdue_promises"] = overdue_promises

        # 3. Deterministic timeline/pacing checks
        pacing_warnings = self._check_pacing(state)
        context["pacing_warnings"] = pacing_warnings

        # 4. Character emotional states from episodic memory
        char_states = self._query_memory_core(state)
        context["character_states"] = char_states

        return context

    def retrieve_for_plan(self, state: SceneState) -> dict:
        """Plan phase: Outline context, style rules, constraints.

        Plan needs to know:
        - What's the outline for this chapter? (archival)
        - What style rules apply? (learned rules DB)
        - What constraints are active? (ASP rules)
        - What warnings from orient? (already in state)

        Do NOT retrieve: character tone, prose examples, vector searches.
        """

        context = {}

        # 1. Retrieve outline from archival memory (RAPTOR for long summaries)
        outline = self.raptor_tree.query(
            query=f"Chapter {state['chapter_number']} outline and beats",
            level="chapter"
        )
        context["outline"] = outline

        # 2. Retrieve style rules that apply to this chapter
        style_rules = self._query_style_rules(state["chapter_number"])
        context["style_rules"] = style_rules

        # 3. Retrieve ASP constraints (not executed yet, just available)
        constraints = self.asp_engine.get_active_constraints(state)
        context["constraints"] = constraints

        # 4. Extract orient warnings from state
        context["orient_warnings"] = state.get("warnings", [])

        return context

    def retrieve_for_draft(self, state: SceneState) -> dict:
        """Draft phase: Voice/tone, sensory details, dialogue patterns.

        Draft needs to know:
        - What does this character sound like? (voice profile)
        - What's the tone for this scene? (examples from similar scenes)
        - What sensory details belong in this location? (episodic)
        - What dialogue patterns does this character use? (archival)

        Do NOT retrieve: plot structure, world rules, constraint data.
        """

        context = {}
        pov_char = state.get("pov_character")
        location = state.get("location")

        # 1. Vector search for tone/voice similarity
        similar_prose = self.vector_store.search(
            query=f"Prose in {pov_char}'s voice, {location} setting, mood: {state.get('mood')}",
            filter={"character": pov_char, "location": location},
            limit=5
        )
        context["similar_prose"] = similar_prose

        # 2. Retrieve voice profile for POV character
        voice_profile = self._query_voice_profile(pov_char)
        context["voice_profile"] = voice_profile

        # 3. Retrieve sensory details for location
        sensory_details = self._query_sensory_details(location)
        context["sensory_details"] = sensory_details

        # 4. Retrieve dialogue patterns for this character
        dialogue_patterns = self._query_dialogue_patterns(pov_char)
        context["dialogue_patterns"] = dialogue_patterns

        return context

    def retrieve_for_evaluate(self, state: SceneState) -> dict:
        """Evaluate phase: Canon facts, world rules, character knowledge boundaries.

        Evaluate needs to know:
        - Is every fact consistent with canon? (KG canon facts)
        - Are timeline constraints satisfied? (deterministic)
        - Do characters know what they should? (epistemic filtering)
        - Are world rules violated? (ASP validation)

        Do NOT retrieve: tone examples, style references, dialogue patterns.
        """

        context = {}

        # 1. Retrieve canon facts for truth verification
        canon_facts = self.kg.query(
            query_type="canon_facts",
            hardness="hard",  # Only hard facts
            temporal_filter=state["chapter_number"],
        )
        context["canon_facts"] = canon_facts

        # 2. Retrieve world rules (ASP)
        rules = self.asp_engine.get_all_rules()
        context["world_rules"] = rules

        # 3. Retrieve character knowledge boundaries (epistemic)
        for char in state.get("characters", []):
            boundaries = self.kg.get_epistemic_access(
                character=char,
                chapter=state["chapter_number"],
            )
            context[f"{char}_knowledge_boundaries"] = boundaries

        # 4. Deterministic timeline check
        timeline_violations = self._check_timeline(state)
        context["timeline_violations"] = timeline_violations

        return context

    # Helper methods

    def _query_promise_db(self, state: SceneState) -> list:
        """Query promise database for overdue promises."""
        # Deterministic SQL query, not vector search
        ...

    def _check_pacing(self, state: SceneState) -> list:
        """Deterministic pacing checks (TAACO, dialogue ratio, etc.)."""
        ...

    def _query_memory_core(self, state: SceneState) -> dict:
        """Retrieve current chapter's core memory (active context)."""
        ...

    def _query_style_rules(self, chapter_number: int) -> list:
        """Retrieve learned style rules for a chapter."""
        ...

    def _query_voice_profile(self, character_id: str) -> dict:
        """Build voice profile from past prose (tone, vocabulary, patterns)."""
        ...

    def _query_sensory_details(self, location_id: str) -> dict:
        """Retrieve sensory details: colors, textures, sounds for location."""
        ...

    def _query_dialogue_patterns(self, character_id: str) -> dict:
        """Extract dialogue patterns: contractions, speech tags, word choice."""
        ...

    def _check_timeline(self, state: SceneState) -> list:
        """Deterministic timeline consistency checks."""
        ...
```

### Usage in Nodes

```python
async def orient_node(state: SceneState, *, router: PhaseAwareRouter) -> dict:
    """Inject router and retrieve phase-aware context."""
    context = router.retrieve_for_orient(state)

    # Assemble warnings from context
    warnings = []
    if context.get("overdue_promises"):
        warnings.append({
            "type": "promise",
            "text": f"{len(context['overdue_promises'])} unresolved promises"
        })

    return {
        "warnings": warnings,
        "retrieved_context": context,
    }

async def plan_node(state: SceneState, *, router: PhaseAwareRouter) -> dict:
    """Route and generate beat sheet."""
    context = router.retrieve_for_plan(state)

    prompt = f"""Given outline and constraints, generate 3-5 beat alternatives.

    Outline:
    {context['outline']}

    Style rules:
    {context['style_rules']}

    Orient warnings:
    {context['orient_warnings']}

    Generate beats."""

    beat_sheet = await router.router.call(prompt, PLANNING_SYSTEM, role="writer")

    return {
        "plan_output": {"beat_sheet": beat_sheet},
    }

async def draft_node(state: SceneState, *, router: PhaseAwareRouter) -> dict:
    """Route and generate prose."""
    context = router.retrieve_for_draft(state)

    prompt = f"""Write this scene in {context['voice_profile']['name']}'s voice.

    Similar prose for tone:
    {context['similar_prose']}

    Sensory details:
    {context['sensory_details']}

    Beat sheet:
    {state['plan_output']['beat_sheet']}

    Write the scene."""

    prose = await router.router.call(prompt, WRITING_SYSTEM, role="writer")

    return {
        "draft_output": {"prose": prose},
    }

async def commit_node(state: SceneState, *, router: PhaseAwareRouter) -> dict:
    """Route and evaluate."""
    context = router.retrieve_for_evaluate(state)

    # Structural evaluation
    timeline_ok = not context["timeline_violations"]
    knowledge_ok = _check_epistemic_consistency(state, context)
    rules_ok = _validate_asp_rules(state, context)

    if timeline_ok and knowledge_ok and rules_ok:
        verdict = "accept"
    else:
        verdict = "second_draft"

    return {
        "verdict": verdict,
        "extracted_facts": [],  # Extract from prose
    }
```

---

## Summary

This BUILD_PREP.md is your single source of truth for implementation. Read it front-to-back before starting Phase 0. Keep it open during coding. When you hit a decision point, search for the relevant section (dependencies, gotchas, patterns) and follow the guidance exactly.

**Next steps after reading this:**
1. Review ARCHITECTURE_PLAN.md for design rationale
2. Create `pyproject.toml` from section 1
3. Create initial directory structure
4. Implement Phase 0 files in order from section 6
5. Write golden test fixture before any node implementation
6. For each phase, read the relevant gotcha section before coding

Good luck, builder.

