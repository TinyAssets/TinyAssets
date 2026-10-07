---
status: historical
---

> **HISTORICAL — superseded.** This doc captured architecture intent as of Phase 7 era. Current architecture lives in docs/architecture.md (PLAN.md was retired 2026-10-06). Kept for git/decision history. Do not edit, do not extend, do not cite as live.

# Phase 7 — GitHub as Canonical Shared State

**Status:** executable.
**Depends on:** Phases 2–5 landed (build/run/judge/Goals all shipping).
**Source materials:** `docs/research/phase7_local_only_audit.md` (dev-2, local-only chokepoints), `docs/research/phase7_github_as_catalog_audit.md` (dev-2, per-table git-vs-local mapping).
**PLAN.md anchor:** Design Decisions "GitHub as the canonical shared state" + "Local-first execution, git-native sync."

## Thesis

Goals, Branches, Nodes, notes, canon, prose, scene packets become git-tracked YAML/Markdown files in the repo. SQLite stays as a local cache + index for fast read; it is no longer the source of truth for any shared artifact. Run state stays purely local (SqliteSaver checkpoints, run rows, telemetry — runs are user-private execution traces). GitHub is the always-on layer: clone it, run locally, PR to contribute. Three SQLite tables retire entirely because git does them better (action ledger → git log; branch heads/snapshots → git refs/tags; node edit audit → git history per file).

## What stays local vs. moves to git

Per dev-2's per-table audit. Summary:

**Migrate to git-tracked files** (13 mutation handlers in `universe_server.py` need a YAML-emit + git-stage seam — 3 goals-cluster, 5 single-entity branch, 5 composite branch per `docs/planning/phase7_3_cutover_scope.md`. Note: `goals.bind` is a goals action that mutates a branch file — dirty-check targets the branch path):
- `branch_definitions` → `branches/<slug>.yaml`
- `goals` → `goals/<slug>.yaml`
- `node_defs` (currently embedded JSON) → `nodes/<branch_slug>/<node_id>.yaml` — **per-file, not inlined**, because per-Goal node reuse (#62) is a Phase 5 design goal that requires node-level diffability.
- `state_schema` → inline in branch YAML (small, tightly coupled to branch identity).
- `universe_rules`, `universe_notes/`, `universe_work_targets`, `universe_hard_priorities` → `<universe>/rules.yaml`, `<universe>/notes/<ts>.md`, `targets.yaml`, `priorities.yaml`.
- `author_definitions` → `authors/<slug>.yaml` (definitions only; runtime instances stay local).
- Canon, prose, scene packets — already filesystem; just include them in the repo.

**Stays local SQLite/disk:**
- `runs`, `run_events`, `run_cancels`, `.langgraph_runs.db` (SqliteSaver), `run_lineage` parent pointers — runs are user-private.
- `run_judgments` — local by default; opt-in promote-to-public via new `publish_judgment` action (mirrors wiki draft → promoted).
- LanceDB vectors and `knowledge.db` (KG) — derived caches, rebuilt from canon/prose on clone via `make rebuild-index`.
- Auth / sessions / capabilities / runtime instances — per-machine state.

**Retired tables** (subsumed by git/GitHub):
- `action_records` ledger → git log + `git blame`. One commit per public mutation; commit author = actor; commit message = action summary. PLAN.md ledger semantics preserved exactly.
- `branches` / `branch_heads` / `universe_snapshots` (Phase 4 multiplayer tables) → git refs and tags do this natively.
- `node_edit_audit` → git history per node file.
- `vote_windows` / `vote_ballots` / `user_requests` → GitHub Issues + Discussions (Phase 7.5).

## Commit granularity: one MCP action = one git commit

**Load-bearing invariant.** Every MCP write action produces exactly one git commit, regardless of how many files it writes. A `build_branch` call that creates a branch plus N node files lands as one commit covering N+1 YAMLs. A `goals.bind` call that rewrites `branches/<slug>.yaml` lands as one commit. This preserves Phase 4 ledger-equivalence (one ledger row per public mutation maps cleanly onto one git log entry).

Implementation: backend composite helpers (`save_branch_and_commit(branch, extra_paths=[...])`) stage all target paths up-front, dirty-check across the full set, then call `git_bridge.commit(paths=[...])` once. Never `commit()` per `stage()`.

## Architecture: dual-write Storage protocol

Per dev-2 audit §3 (local-only): `_connect` chokepoints in `author_server.py` are the existing seam. Phase 7 makes them pluggable.

```
StorageBackend (protocol)
├── sqlite_only      — current default, used by tests + transitional builds
├── sqlite_cached    — writes go YAML→git first, SQLite mirrors as read cache
└── filesystem_only  — far-future; YAML is read AND written directly, SQLite gone
```

Phase 7 ships `sqlite_only` (unchanged) + `sqlite_cached` (new). Cutover is per-handler, not big-bang. Reads keep going through SQLite for query performance; writes go through both backends. On clone or pull, the cache rebuilds from YAML in seconds.

This preserves dev-2's "no test rewrites except the assertion shape" property — most tests load YAML and assert structurally similar shapes.

## Phased rollout

**7.1 Storage layout + serializer (no git yet).**
- YAML schema for branches, goals, nodes, state_schema. Round-trip property tests: SQLite → YAML → SQLite is identity.
- One-time export script `tinyassets/scripts/export_to_repo.py` writes existing universes' shared artifacts to `repo/<universe>/...`.
- `StorageBackend.sqlite_cached` lands but git seam stubs out as "write file to working dir, no commit."
- All existing tests pass against either backend (env switch).

**7.2 Git bridge (`tinyassets/git_bridge.py`).**
Thin wrapper around `subprocess.run(["git", ...])`. Surface:
- `stage(path)` — `git add <path>`.
- `commit(message, author)` — `git commit -m ... --author=...`. Author is the GitHub identity from §7.4 below; falls back to `anonymous <noreply@>` in dev mode.
- `pull()` — `git pull --ff-only`. Refuses on local divergence; returns conflict marker for the MCP layer to surface.
- `open_pr(title, body, branch)` — uses `gh` CLI if installed, else returns a "manual PR needed" payload with the push URL.

Build-time dependency: git binary present. `gh` is optional; absence degrades the `open_pr` flow but doesn't block writes.

**7.3 MCP write actions get a git seam.**
Two-phase commit semantics dev-2 recommended:
- Each public mutation (`build_branch`, `patch_branch`, `update_node`, `patch_nodes`, `rollback_node`, `goals propose/update/bind/delete`, `universe write_note/give_direction/submit_request`) writes its YAML, stages it, and **commits immediately** with a generated message attributed to the actor.
- Rationale: matches Phase 4 ledger semantics (one commit per public action) and avoids "I built a workflow but it's nowhere" UX confusion. It also means the git log IS the ledger from day one — no separate "publish" step needed for solo work.
- New action `sync_latest` — `git pull --ff-only`, returns `{pulled, conflicts: [...]}`.
- New action `publish_to_remote` — `git push` + optional `gh pr create` for users on a fork. Returns the PR URL.
- `judge_run` stays local-only; new `publish_judgment(judgment_id)` writes Markdown to `judgments/<slug>.md`, stages, commits.

**7.4 Identity wiring (the unblock).**
Per dev-2 audit §1: `_current_actor()` reads env var today; auth provider is implemented but unwired. Phase 7 wires it.
- New env var `TINYASSETS_GITHUB_USERNAME` for dev/local use — populates actor + git commit author.
- For hosted self-host installs that want OAuth, the existing `OAuthProvider` flow lights up (DCR + PKCE per MCP spec). Out of scope for Phase 7.1 ship; lands in 7.4 follow-up.
- Ledger queries (`/ledger` endpoint, `_action_get_ledger`) translate to `git log --pretty=...` calls. Tests assert commit shape, not row shape (per dev-2 test-suite impact note).

**7.5 GitHub Actions pipeline.**
- **Branch validation on PR**: parse YAML, run `BranchDefinition.validate()`. Fail PR check on errors. Highest-leverage hook — catches malformed contributions before merge.
- **Mermaid preview in PR comments**: render the branch's flowchart and post as a comment. Reviewers see the topology without checking out the PR.
- **Smoke-run** deferred — needs runner with provider key + budget guard. Track for 7.6.

**7.6 Outcome gates as committed YAML** (forward-compat seam for #56).
Phase 6 outcome gate claims land as `gates/<goal_slug>/<branch_slug>__<rung_key>.yaml` — filed by Goal first, then Branch, with the ladder rung as the leaf. Goal-first path because the ladder itself lives on the Goal (`goals/<slug>.yaml#/gate_ladder`), so all claims against one Goal stay colocated for diff-ability and per-Goal leaderboard rebuilds — one directory listing IS the leaderboard data. Diff-able, fork-friendly, queryable via the same YAML cache. GitHub Issues stay for human discussion of gate progress. See `docs/specs/outcome_gates_phase6.md` for the full Phase 6 spec.

> **Host decision 2026-04-14** — goal-first layout (this shape) over branch-first (`gates/<branch_slug>/<gate_id>.yaml`). Reasoning: leaderboard is the hot query path, and goal-first matches the user-browse mental model (browse a Goal → see all contending workflows).

**7.7 Privacy / draft workflow.**
- Convention: `draft/` directory at repo root + a corresponding `.gitignore` line. Drafts never auto-commit.
- MCP `commit_my_work` distinguishes `save_draft` (writes to `draft/...`, no git op) from `publish` (writes to canonical path, stages, commits).
- Open product question: how loudly does the bot warn before committing? My lean: silent for explicit `publish`; one-line confirmation for any auto-commit triggered by `build_branch` / `patch_branch`. Refine after Mission 7 user-sim feedback.

## Honest constraints (resolved)

- **Git tooling required**: power users fine; non-technical users go through MCP wrappers that hide git entirely. Phone-surface UX never sees `git pull`.
- **Pull conflicts** are the unavoidable rough edge. v1: surface the conflict with file paths in the `sync_latest` response and bail; user resolves manually or asks a human. v2 (7.x): MCP `resolve_conflict(file, choice)` action picks `ours`/`theirs`/`merge` per file. Not v1 scope.
- **PLAN.md hard rules preserved**: SqliteSaver-only stays (runs are local). LanceDB singleton stays (per-clone). No Postgres migration, no AsyncSqliteSaver. Hosted-runtime questions (LLM-key model, multi-tenant isolation) all collapse to "doesn't apply — each user runs their own clone."
- **Run executor stays process-local** per dev-2 §5. No queue work in Phase 7.

## Acceptance criteria

1. `StorageBackend` protocol exists with `sqlite_only` + `sqlite_cached` implementations; tests pass against either via env switch.
2. YAML schemas for branches/goals/nodes/state_schema + round-trip property tests (SQLite → YAML → SQLite identity).
3. Export script writes a current universe's shared state to a fresh repo dir without data loss.
4. `git_bridge.py` lands with stub-able surface; tests use stubs.
5. Each of the ~12 named write handlers commits a YAML file with attributed message; `_action_get_ledger` translates to `git log` and matches old shape closely enough to keep MCP clients happy.
6. `sync_latest` and `publish_to_remote` actions registered; conflict surfaces returned, not crashed on.
7. GitHub Action validates BranchDefinition on PR; mermaid preview comment posts.
8. Three retired tables removed (`action_records`, `branch_heads`, `node_edit_audit`); migration script handles existing data (export to git history before drop).
9. Mission 7 readiness: a non-technical user on a phone builds, runs, judges, and publishes a workflow. Their clone shows up as a PR on GitHub. Another user `sync_latest`s and sees the workflow.

## Escalations

- **Auto-commit-on-every-write vs stage-and-batch.** Spec assumes auto-commit per public mutation (matches Phase 4 ledger semantics, simplest UX). Alternative is "stage on write, commit on user's explicit `publish_session`" — fewer commits, neater history, but risks losing work and breaks the ledger-equivalence. Want host signal before locking. My lean: auto-commit, with a `commit_squash` follow-up action if history gets noisy.
- **Draft warning verbosity.** Same — the publish-vs-draft UX needs Mission 7 signal to tune.

## What this Phase 7 is NOT

- Not multi-tenant hosted runtime (discarded framing).
- Not federation across hosts (subsumed: GitHub IS the federation layer).
- Not a Postgres migration.
- Not a rewrite — additive seam at ~12 chokepoints + retire 3 tables.
