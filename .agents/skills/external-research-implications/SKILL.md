---
name: external-research-implications
description: Research an external project, repository, paper, article, or codebase and derive TinyAssets implications, plans, and integration slices. Use when the user points an agent at outside work and asks what we should learn, copy, avoid, integrate, or build from it.
---

# External Research Implications

## Overview

Turn outside work into durable TinyAssets learning. Use this when the user points
at a project, repo, research paper, article, architecture, product, benchmark,
or competitor and asks for implications, integration, comparison, or a plan.

The output is not a generic summary. The job is to understand the outside work,
map it against TinyAssets, decide what matters for our users, and leave behind a
reusable plan or artifact that future sessions can act on.

## Boundary with implementation precedent

Use this skill when a named outside source or a research finding may change
TinyAssets direction, ADRs or OpenSpec specs, capability direction, or
cross-task architecture. Use a repo search for precedent instead for a
bounded search for external code examples that informs one implementation
decision inside an already-authorized lane.

A scout source map is task-scoped implementation evidence covered by normal
code review. If the scout would broaden scope or change accepted design, promote
the finding into this workflow rather than deciding it inline. This
skill may invoke the scout for adjacent implementations, but remains
responsible for durable implications, authority changes, and review.

## Trigger Examples

- "Study this repo and tell us what it means for TinyAssets."
- "Read this paper and build an implementation plan."
- "Compare our architecture to this project."
- "What should we learn from this?"
- "Turn this research into steps toward integration."
- "Anytime I point an agent at a project/research paper/repo."
- "Find the most important frontier project for our direction."
- "Scan for paradigm shifts and tell us what to align with early."

## Core Stance

TinyAssets users are people with MCP-connected chatbots. They should be able to
steer, redesign, fork, evolve, and improve branches through conversation. The
project should become more community-evolvable and self-improving, not more
dependent on maintainers or local research harnesses.

When importing outside ideas:

- adapt them into TinyAssets primitives;
- preserve MCP/chatbot-first user surfaces;
- preserve community remix, lineage, attribution, and branch evolution;
- preserve uptime with zero hosts online where applicable;
- reject sidecars that bypass TinyAssets' control plane, privacy model,
  evaluator evidence, host pool, or provenance chain.

## TinyAssets

### 1. Orient And Claim

1. Run `python scripts/openspec_flow.py audit` for the live work queue, and
   skim `docs/concerns/README.md` if the area has known-unresolved findings.
2. Run `python scripts/provider_context_feed.py --provider <provider> --phase claim`
   once, so prior-provider memories and pending implications are visible before
   scoping. That is the only scan; there is no per-phase repeat.
4. If you will write a durable artifact, check no open PR or active branch
   already owns those files (`python scripts/worktree_status.py`).
6. Read `README.md` § Direction, `docs/architecture.md`, and the ADRs and
   specs relevant to the study.

### 2. Canonicalize The Outside Source

Record what makes the claim checkable later: canonical URL, commit or version,
date, and license. Clone read-only or use official APIs, and **never vendor
outside code as part of a study** unless the user asks for it.

### 3. Map Both Systems

Map the outside system module by module:

- entrypoints and user surface;
- execution loop;
- storage/state;
- evaluation/metrics;
- memory/retrieval;
- provider/model usage;
- concurrency/distribution;
- safety/sandbox/privacy;
- artifacts and provenance;
- tests, demos, and operational gaps.

Map the relevant TinyAssets modules the same way:

- use `rg --files`, AST summaries, tests, and targeted `docview.py`;
- connect claims to ADRs, specs, design notes, and current code;
- note where TinyAssets already has a stronger primitive;
- note where the outside system exposes a real gap.

### 4. Research Adjacent Work

Look beyond the named source when it improves judgment:

- sibling repos and implementations;
- cited papers;
- independent replications or critiques;
- current best practice for the pattern;
- security/safety warnings;
- licensing and adoption constraints.

Separate evidence from inference. Cite sources in the durable artifact.

### 5. Frontier Radar Mode

Use this mode when the user asks which outside project, paper, repo, or
movement matters most, rather than naming a single source.

When the user asks for another, next, or second project/repo, first scan
existing `docs/audits/`, `ideas/PIPELINE.md`, and open `openspec/changes/` so
the result does not simply rediscover an already-promoted concept. A repeated
concept is acceptable only when the new source materially changes the
implementation path; otherwise choose a distinct frontier axis and state how it
relates to prior picks.

Scan for frontier candidates across primary sources, recent papers, official
repos, and reputable technical writeups. Do not optimize for launch noise,
stars, funding, or flashy demos. Rank candidates by:

- paradigm shift: does it change the unit of design or improvement?
- TinyAssets fit: does it map to MCP users, branches, evaluators, host capacity,
  provenance, privacy, and community evolution?
- implementation gravity: is the industry likely to rediscover this direction
  within 6 months to 3 years?
- evidence quality: primary paper/repo, benchmark signal, reproducibility,
  and visible limitations;
- integration leverage: can TinyAssets adopt the underlying primitive without
  copying a sidecar platform?
- anti-hype discipline: penalize projects that are only wrappers, demos,
  ungrounded agent claims, or local-only workflows with no durable state.

Expected output:

- short candidate table;
- one chosen frontier bet;
- why it beats the runner-up choices;
- what to adopt, adapt, avoid, defer, and watch;
- first TinyAssets-native integration slice;
- what this should change in this skill or project docs.

### 6. Derive TinyAssets Implications

Classify each implication:

- `Adopt`: fits TinyAssets and should become a native primitive.
- `Adapt`: useful idea, but needs TinyAssets-specific shape.
- `Avoid`: tempting but conflicts with users, uptime, privacy, or architecture.
- `Defer`: good idea, wrong time or blocked by other work.
- `Watch`: promising but too unproven or unstable.

For each material implication, include:

- why it matters for MCP-chatbot users;
- which TinyAssets primitive it maps to;
- smallest credible integration slice;
- risks and failure modes;
- verification needed;
- whether an ADR or an OpenSpec spec must change.

When the outside work is a trace, data-flywheel, observability, or training-data
project, also evaluate:

- capture surfaces and supported agents/clients;
- schema shape for steps, tools, observations, outcomes, artifacts, and cost;
- privacy, redaction, review, consent, and export gates;
- attribution links from trace to commit, branch, node, evaluator, or user;
- whether the data should remain private, become a community artifact, or feed
  evaluator/training datasets.

Never recommend automatic public trace upload as a first slice. Start with
private-by-default capture and explicit review.

### 7. Leave Durable Artifacts

Default artifact location:

`docs/audits/YYYY-MM-DD-<source>-architecture-implications.md`

Use a design note instead when the user accepts a direction:

`docs/design-notes/YYYY-MM-DD-<topic>.md`

If implementation should follow, create a narrow OpenSpec change with
specific files and dependencies. Do not bury active work only in the report.

When the concept is approved or likely to become implementation, land the
implication into the repo's worktree discipline as a worktree-ready handoff.
The Claude Code team may continue improving the automation around worktrees,
but this skill must leave enough structure for any provider to pick it up.

Add a `Worktree Landing Packet` to the report and mirror its essentials into
`openspec/changes/` or `ideas/PIPELINE.md`. Include:

- proposed branch name, using the provider convention when known
  (`codex/<slug>`, `claude/<slug>`, or the eventual team convention);
- proposed worktree directory, normally `../wf-<slug>` unless the local
  worktree manager says otherwise;
- relevant ADRs and specs reviewed or needing review before build;
- idea feed refs from `ideas/INBOX.md`, if loose captured ideas should be
  remembered at the bottom of the lane;
- GitHub fold-back object: draft PR while blocked/reviewing, ready PR only
  after verification gates pass;
- prior-provider memory refs from the session that produced the finding or
  preceding work, such as `.claude/agent-memory/<role>/<file>.md`;
- related implication refs that must be cross-considered during planning,
  build, and review;
- base branch or dependency, including any review artifact that must land
  first;
- exact write-set for the change's task list;
- read dependencies that should be rechecked after upstream work lands;
- first implementation slice small enough to commit independently;
- expected verification gates before commit, before push, and before live
  acceptance;
- fold-back path: PR/merge target, STATUS row retirement, and follow-up row or
  `ideas/PIPELINE.md` update if work remains.

Research does not gate build work. Land the implementation lane into the
git/worktree discipline immediately, and let review scope follow `AGENTS.md`
§ *Working Norms*: a review is owed for floor-class changes and gate-defining
files, one round, dispatched once the PR is open. Record the `initial_provider`
in the durable artifact so a later reader knows whose finding it was.

### 8. Review Scope Lives In AGENTS.md

A research finding needs no review of its own to be built. When one IS owed
(floor-class change or gate file, per `AGENTS.md` § *The loop* item 4),
dispatch it to the other family via `peer-agents` and keep the verdict artifact
with the change: it is a real gate, not a rubber stamp, and the reviewer may
change the plan.

### 9. Leave The Next Step Somewhere Real

A study is not finished while its next step exists only in a report or a chat.
Put it in the home that fits — an OpenSpec change when there is an actionable
step now, `ideas/PIPELINE.md` when it should not be forgotten yet, a design note
only once design truth is accepted — and name the concept, its source URLs, the
exact next action, and the files it may touch. For a cross-cutting concept, say
what it applies to so a future builder notices it while working elsewhere. If you
create no entry, say why.

### 10. Self-Iterate The Skill

At the end of any substantial external-research implications study, ask:

- Did this reveal a repeatable step future agents should perform?
- Did the agent miss a source type, artifact type, or verification step?
- Did the user explicitly say "make this process repeatable"?

If yes, update this skill immediately, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync-skills.ps1
python scripts/validate_skills.py
git diff --check -- .agents/skills .claude/skills .codex/skills
```

## Output Shape

For a substantial study, produce:

1. executive judgment;
2. source freshness stamp;
3. module-by-module outside-system map;
4. module-by-module TinyAssets comparison;
5. adjacent research summary;
6. adopted/adapted/avoided/deferred implications;
7. recommended implementation roadmap;
8. review verdict, when one is owed;
9. pickup packet;
10. worktree landing packet;
11. open questions and verification gaps.

For a quick user-facing answer, summarize only the highest-level implications
and link to the durable artifact.

## Verification

- live work has an OpenSpec change, and any new host decision is in
  `docs/host-actions.md`.
- External source URL, commit/version/date, and license are recorded.
- Claims that depend on current outside facts are web-verified.
- Large local docs were read with `scripts/docview.py`.
- The output distinguishes evidence from inference.
- Suggested integration slices preserve MCP-chatbot-first users.
- Suggested integration slices preserve community evolvability.
- Adopt/adapt concepts have a pickup packet in `openspec/changes/` or `ideas/PIPELINE.md`.
- Cross-cutting findings include "applies when touching" cues for future builders.
- Skill changes, if any, were synced to provider mirrors and validated.
