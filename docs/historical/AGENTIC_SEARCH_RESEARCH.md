> **HISTORICAL — superseded.** This doc captured architecture intent as of 2026-04-06. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Agentic Search: Industry Convergence Research

Research compiled 2026-04-06. Intended audience: any agent working on this
project. Covers the state of agentic search, memory systems, and context
management as of early 2026, with implications for the TinyAssets Engine.

---

## What the Industry Has Converged On

Five patterns now appear across nearly every serious agent framework. These
are no longer experimental — they are production baseline.

**1. Tool-driven context, not pre-assembly.** Agents invoke retrieval as
tool calls at reasoning time rather than having context stuffed into prompts
upfront. LangGraph's dynamic tool calling (August 2025) formalized this:
different tools available at different workflow stages, selected by the agent
based on what it needs. A-RAG (February 2026) showed that exposing
hierarchical retrieval interfaces — keyword, semantic, and chunk-level —
directly as tools lets models naturally adopt diverse search strategies
without explicit routing prompts.

**2. Hybrid multi-backend retrieval.** No single backend wins. The proven
stack is vector similarity for semantic recall, knowledge graph traversal for
entity relationships and multi-hop reasoning, BM25/lexical search for exact
matches, and SQL or structured queries for operational data. Routing happens
at query time based on inferred intent — not as a fixed pipeline. GraphRAG
benchmarks show 81.67% accuracy on complex queries vs. 57.50% for
vector-only; hybrid approaches push above 90%.

**3. Temporal truth as a first-class concept.** Every major memory system
released in 2025+ includes validity windows on facts, supersession tracking,
and bitemporal reasoning (separating "when did this happen" from "when did
we learn it"). Zep's Graphiti formalized this with temporal directed labeled
graphs. Supermemory uses explicit UPDATE relations linking new memories to
the ones they supersede. This is no longer optional infrastructure.

**4. Scoped memory with actor provenance.** Memory isolation across
user/session/project/organization scopes is standard. Mem0's Group-Chat v2
(June 2025) added actor-aware tagging to distinguish what users said vs.
what agents inferred — critical in multi-agent settings where one agent's
inference should not become another agent's ground truth.

**5. Context compaction over context expansion.** Context drift, not context
exhaustion, is the primary failure mode. ACON (October 2025) showed 26-54%
token reduction while maintaining reasoning quality by treating compaction as
an optimization problem. Anthropic shipped production-grade automatic
compaction in November 2025. The pattern is: write artifacts to disk after
each phase, hand off structured state, start fresh with loaded state.

---

## Projects Worth Studying

### A-RAG — Hierarchical Retrieval Tools for Agents

Exposes three tool granularities to the agent: keyword_search (broad
recall), semantic_search (vector similarity), chunk_read (passage-level
refinement). The agent chooses which tool at each step. Results show it
outperforms fixed pipelines with comparable or lower token cost.

- Paper: arxiv.org/abs/2602.03442
- GitHub: github.com/Ayanami0730/arag
- Relevance: Validates your story_search tool design. The multi-granularity
  pattern maps to your scene/chapter/book/universe timescales.

### Graphiti (Zep's Open-Source Core) — Temporal Knowledge Graphs

Purpose-built for agents on evolving data. Facts carry validity windows.
Supports both prescribed ontology (schema-defined entity types) and learned
ontology (inferred relationships). Semantic + keyword + graph traversal
search in sub-100ms. Outperforms MemGPT on Deep Memory Retrieval benchmark
(94.8% vs 93.4%).

- GitHub: github.com/getzep/graphiti
- Paper: arxiv.org/abs/2501.13956
- Relevance: Your temporal truth tracking (tinyassets/memory/temporal.py) is
  on the right path. Graphiti is the cleanest open-source reference for how
  validity windows and supersession should work at the graph level.

### A-MEM — Zettelkasten-Inspired Agentic Memory

When a new memory is added, the system generates a structured note (context,
keywords, tags), analyzes historical memories for connections, establishes
links, and continuously refines existing memories when new ones arrive.
Memories dynamically trigger updates to contextual representations of
related memories.

- Paper: arxiv.org/abs/2502.12110
- GitHub: github.com/agiresearch/A-mem
- Relevance: Your notes system already works this way conceptually. A-MEM
  validates the approach of memories as interconnected living documents
  rather than flat stores.

### MemOS — Memory Operating System

Three-layer system: interface, operations, infrastructure. Distinguishes
textual memory, activation memory (KV cache), and parametric memory (LoRA
weights, skill parameters). Ranks #1 across all LongMemEval categories.
Available as both standalone service (MemOS NEO) and MCP plugin (MemOS MCP).

- GitHub: github.com/MemTensor/MemOS
- Paper: arxiv.org/pdf/2507.03724
- Relevance: The MCP plugin deployment model is interesting for your
  Universe Server. The parametric memory concept (storing learned model
  adaptations) is a future direction for author soul files.

### MemoRAG — Global Memory-Enhanced Retrieval

Dual-system architecture: a light long-range system creates global memory
via KV compression (30x context pre-fill speedup), while an expensive
expressive system generates answers from retrieved information. Uses
Reinforcement Learning from Generation Feedback (RLGF) to reinforce
memorization capacity.

- Paper: arxiv.org/abs/2409.05591
- GitHub: github.com/qhjqhj00/MemoRAG
- Relevance: The dual-system architecture — cheap global overview plus
  expensive targeted retrieval — maps to your universe-level vs. scene-level
  context assembly.

### Cognee — Extract-Cognify-Load Pipeline

Memory engine for AI agents. ECL pipeline: Extract (parse docs/URLs),
Cognify (structure into knowledge graph), Load (make searchable). 500x
scale growth in 2025. Integrates with Claude Agent SDK, LangGraph, Neo4j.

- GitHub: github.com/topoteretes/cognee
- Relevance: Your upload synthesis pipeline (canon ingestion -> KG ->
  searchable) follows the same ECL pattern. Cognee's ontology resolver
  is worth studying for your entity extraction.

### NexusRAG — Vector + KG + Cross-Encoder Hybrid

Combines vector search, LightRAG knowledge graphs, and cross-encoder
re-ranking with Docling document parsing and visual intelligence.

- GitHub: github.com/LeDat98/NexusRAG
- Relevance: Reference implementation for the hybrid retrieval stack your
  project targets.

### Semantic Router — Query-Level Routing

Operates on semantic similarity in vector space to route queries to the
right backend. 30-40% infrastructure cost savings without accuracy loss.
Most requests route via fast semantic matching; only complex queries use
full LLM reasoning.

- GitHub: github.com/aurelio-labs/semantic-router
- Relevance: Your story_search tool currently routes internally. Semantic
  Router provides a clean, tested library for the routing policy layer.

---

## Research Papers to Track

### Agentic RAG Survey (January 2025, updated April 2026)
Comprehensive survey embedding agents into RAG pipelines. Covers
reflection, planning, tool use, multi-agent collaboration. Distinguishes
simple RAG (blind fetch-once) from agentic RAG (iterative reasoning).
- arxiv.org/abs/2501.09136

### ACON: Optimizing Context Compression for Long-Horizon Agents
Treats compression as an optimization problem. Finds cases where full
context succeeded but compressed context failed, uses a capable LLM to
identify what information was lost, and revises compression prompts.
26-54% token reduction while maintaining quality.
- arxiv.org/abs/2510.00615

### DAAO: Difficulty-Aware Agentic Orchestration (February 2026)
Dynamically generates query-specific multi-agent workflows based on
predicted difficulty. Uses VAE for difficulty estimation, modular operator
allocation, cost-aware LLM routing. Surpasses prior systems on six
benchmarks.
- arxiv.org/abs/2509.11079

### AFLOW: Automating Agentic TinyAssets Generation
Represents workflow nodes and edges as code to explore the full range of
possible workflows. Uses Monte Carlo Tree Search to optimize designs.
Demonstrates weaker models can outperform stronger ones when the workflow
is well-optimized.
- arxiv.org/pdf/2410.10762

### Graph-Based Self-Healing Tool Routing (March 2026)
Treats agent control-flow decisions as routing, not reasoning. Uses
parallel health monitors and cost-weighted tool graphs. Dijkstra's
algorithm performs deterministic shortest-path routing. Balances
reliability-cost tradeoff.
- arxiv.org/abs/2603.01548v1

### Memory in the Age of AI Agents (December 2025)
Landscape survey of agent memory. Distinguishes formation, evolution, and
retrieval as separate lifecycle concerns. Argues that tool-selection policy
itself is an adaptation surface.
- arxiv.org/abs/2512.13564

### Hindsight: Multi-Strategy Retrieval (December 2025)
Four parallel retrieval strategies with cross-encoder reranking: semantic
similarity, BM25, graph traversal, temporal search. Results merged and
reranked. Captures strengths of each modality.
- arxiv.org/html/2512.12818v1

---

## Mem0 vs. Letta vs. Zep: Where They Stand

Three architectural philosophies, each with tradeoffs:

**Mem0** — Memory layer you bolt onto existing frameworks. Framework-agnostic.
Multi-store architecture (Qdrant, Chroma, Milvus, pgvector, Redis). Graph
tier for entity relationships. Actor-aware tagging for multi-agent provenance.
48K GitHub stars, Series A October 2025. Now supports Cassandra and Valkey
for production scale.

Strength: Plugs into anything. Weakness: Not an agent runtime — just memory.

**Letta (formerly MemGPT)** — Full agent runtime modeled on OS memory
management. Core memory (always in context, like RAM), recall memory
(searchable overflow, like disk cache), archival memory (long-term, like
cold storage). Agent state persists in databases, not Python variables.
Letta V1 removed heartbeat/send_message tools in favor of native model
reasoning.

Strength: Tightest integration between memory and agent loop. Weakness:
Must run inside Letta — not a library you compose.

**Zep** — Temporal knowledge graph architecture. Graphiti engine (open-source)
builds temporal directed labeled graphs. Facts as edges, entities as nodes,
validity windows on everything. Three retrieval modalities: temporal search,
entity traversal, semantic similarity. Sub-100ms queries. YC-backed, $24M
Series A.

Strength: Best temporal reasoning. Weakness: Community edition deprecated —
production is paid-only. Graphiti (the core) remains open source.

**For this project:** None of these should be adopted wholesale (per PLAN.md
Design Decisions). The useful takeaways are: Zep/Graphiti's temporal truth
model, Mem0's actor-aware provenance tagging, and Letta's principle that
memory management is part of the agent control loop (not a side channel).

---

## What This Validates in the Current Architecture

The project's existing design decisions align with 2026 industry consensus
on nearly every major axis:

**Tool-driven context (PLAN.md cross-cutting principle):** Now the industry
default. A-RAG and LangGraph's dynamic tool calling prove the pattern. The
remaining pre-assembly in the codebase is correctly identified as
transitional tension.

**Hybrid search is memory (PLAN.md cross-cutting principle):** The routing
policy matters more than any single backend. This is exactly what hybrid
retrieval benchmarks show — 90%+ accuracy requires graph + vector + lexical
working together.

**Query semantics over tier names (D3 resolution):** The industry is moving
the same direction. Zep's temporal query surface, Mem0's faceted search, and
the broader shift toward intent-driven retrieval all support "memory queries
should feel like faceted search, not storage-tier addressing."

**Temporal truth tracking (Phase 3 work):** Now standard infrastructure.
tinyassets/memory/temporal.py is architecturally correct. Graphiti is the best
open-source reference for validation and potential integration.

**Memory scoping (Phase 3 work):** Universe/branch/author/user/session
scopes match the multi-level isolation pattern that Mem0, Microsoft Foundry,
and CrewAI all converged on independently.

**Compaction services (Phase 3 work):** ACON and Anthropic's production
compaction validate the approach. The handoff artifact pattern
(HandoffArtifact, CompactionService, HandoffStore) is the right abstraction.

**Universe Server as MCP (Phase 6):** MCP is now the standard protocol.
6,400+ registered servers as of February 2026. The project's direction of
exposing tools via MCP rather than custom API is validated by industry
adoption.

---

## What the Architecture Should Consider Next

**1. Semantic routing as an explicit layer.** The project routes queries
inside story_search, but routing policy deserves its own testable,
configurable component. Semantic Router (aurelio-labs) provides a clean
library. The routing layer should be evaluable independently of the backends
it routes to.

**2. Cross-encoder reranking after multi-backend fusion.** When results come
from KG, vector, and notes simultaneously, a reranking step significantly
improves relevance. The Hindsight paper's four-strategy + cross-encoder
pattern is the current best practice.

**3. Memory consolidation as a scheduled operation.** The consolidation
module exists (tinyassets/memory/consolidation.py) but should run on a
schedule — not just on demand. Mem0's production deployments show that
unbounded memory growth degrades retrieval quality within weeks.

**4. Learned routing policies.** AFLOW and DAAO show that routing can be
optimized through search (MCTS) or learned difficulty estimation. The
project's eval layer could drive routing improvements: which backend
contributed most to good outcomes?

**5. Procedural memory.** MemOS and A-MEM are beginning to store learned
procedures and skills alongside facts. For the project, this means: can the
daemon store and retrieve "how I handled this kind of scene before" as a
reusable procedure, not just as a fact about what happened?

**6. Production memory backends.** SQLite is correct for development and
single-host deployment. Mem0's 2025 migration to Cassandra/Valkey signals
that production multi-author deployments will need distributed storage.
The Postgres migration path mentioned in STATUS.md is the right next step.

---

## Curated Resource Index

### Surveys and Benchmarks
- Agentic RAG Survey: arxiv.org/abs/2501.09136
- Memory in the Age of AI Agents: arxiv.org/abs/2512.13564
- Agent Memory Paper List: github.com/Shichun-Liu/Agent-Memory-Paper-List
- Awesome GraphRAG: github.com/DEEP-PolyU/Awesome-GraphRAG
- GraphRAG vs Vector RAG Benchmark: arxiv.org/html/2507.03608v1
- LongMemEval Benchmark: used by MemOS, Zep, Mem0, Supermemory
- MemoryAgentBench: 17 datasets across 4 competencies

### Open-Source Projects
- A-RAG: github.com/Ayanami0730/arag
- Graphiti: github.com/getzep/graphiti
- A-MEM: github.com/agiresearch/A-mem
- MemOS: github.com/MemTensor/MemOS
- MemoRAG: github.com/qhjqhj00/MemoRAG
- Cognee: github.com/topoteretes/cognee
- NexusRAG: github.com/LeDat98/NexusRAG
- Semantic Router: github.com/aurelio-labs/semantic-router
- Awesome LangGraph: github.com/von-development/awesome-LangGraph

### Architecture References
- Anthropic Context Engineering: anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangGraph Dynamic Tool Calling: changelog.langchain.com/announcements/dynamic-tool-calling-in-langgraph-agents
- MCP Specification (Nov 2025): modelcontextprotocol.io/specification/2025-11-25
- MCP 2026 Roadmap: blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/
