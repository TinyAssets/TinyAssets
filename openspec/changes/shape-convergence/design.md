## Context

The map is the Shape registry in `docs/architecture.md` (PR #4619): 27 rows,
each giving the one path, the second path still live, the status, the guard
and the item that removes the drift. This design covers four things the
registry cannot hold: how agents start today (§1), why shapes drift (§2), the
mechanism that stops it (§3) and the order of the work (§4). It replaces the
design note in #4616 and the inventory note in #4609. Their findings are
folded here, and neither note merges.

Measured on `origin/main` `358e8c7c5ec`.

## 1. How an agent or model turn starts today

There are 32 entry points. 24 are live in production. They collapse to four
distinct starters and three executors.

| Starter | Live entry points (aliases) | Executor | Admission / budget |
|---|---|---|---|
| Chat writer turn: `universe_intelligence._call_writer` (`universe_intelligence.py:1069`) | app and connector `converse` (`universe_server.py:2986`); owner answers (`request_answers.owner_turn`); due wakes (`agent_wakes._run`); continuations (`request_continuations._run`, 5 s thread); addressed custom agents (`addressed_agents.resolve`); voice relay (`realtime_voice.py:281`); durable ingress (dark) | (a) HTTP `AgentTurnCoordinator` via `make_interactive_agent_turn`; (b) otherwise `call_provider("converse")` → native CLI in a provider cell; (c) thin loop, dark behind `TINYASSETS_AGENT_LOOP` | `TurnRequestBudget` root (`:1522`), seat hold, `authorize_conversation_turn`, served authority |
| Foreground run session: `_ForegroundRunProviderSession` (`foreground_run_provider.py`) | `run_graph`, run a version, resume, waiting-run bind, direct input, source events and inbound webhooks, user automations (`automations._run_due_automation`), activities (`activity_runner.start`), cross-owner delivery (`delivery_runtime`), sub-branch nodes (`invoke_branch`, `invoke_branch_version`, `await_branch_run`), app-experience `turn_consumer` chat (`consumer_runtime.converse_turn`) | prompt node → `call_with_policy_sync` / `call_provider`; agent node (ADR-014) → `WorkflowAgentTurn` → `router.call` (never the thin loop) | `TurnRequestBudget` (`:288`) + `RunRequestAllocation` (`:295`); scopes `_admit` and `_admit_manifest`; `_authorize_attempt`; node seat |
| One-shot `call_provider` with no turn | `extract_learning` after each turn; `connection_inference` on a pasted credential; `memory/reflexion`, `knowledge/raptor`, `retrieval/agentic_search`; effect auto-review (`agent_review`) | router → whichever adapter is bound | router fallback root (`router.py:801`) or the caller's allocation |
| MCP sampling reviewer: `ta_capabilities._turn_reviewer` (`:410`) | review during an MCP client's `ta` calls | the client's model, outside `ProviderRouter` | none |

Present but not live, all to be deleted:

- `background_served_provider` (1790 lines): reachable only from tests.
- `cloud_automation_continuation` (3027 lines): retired, still shipped.
- The `fantasy_daemon` controller and phases, 15 `call_provider` sites:
  tray and local only.
- `control_plane/wake.py`: no handler is ever registered.
- The compose `slack-agent` service: it points at a module that does not
  exist.

There is no platform child-session primitive. Sub-agents exist only inside
the Claude CLI's own loop.

Counts: 22 `call_provider` sites, 5 `call_with_policy_sync` definitions, 5
fresh `TurnRequestBudget` roots, 9 admission gates on the model path.

**Target: one starter and one call.**

- Every agent starts as
  `session.start(agent_ref, input, parent)` in the control plane. That
  covers a chat message, a wake, an automation firing, an agent node, a
  child session (`ta agent run`) and an addressed agent.
- `agent_ref` is a file under `agents/<name>/` (C8).
- The session runs the thin loop (C9). Its tools are the four tools plus
  `ta` in the owner's box (C6).
- Every model call inside it is `model_call.call(model_ref, context,
  parent)` (C3). That call is metered at the broker on the parent
  allocation and returns a receipt.
- Admission happens once, at `session.start`: owner, seat, box, allocation.
- A workflow run is a durable job whose steps call the same two functions.
- Native CLIs become named external-agent packages started through the same
  job API (#4616 rule b).

## 2. Why shapes drift (evidence)

1. **Declared in prose, never made executable.** The ADR files were written
   on 2026-10-07 (#4547), backdated to the dates the decisions were made.
   Not one shape row had a guard that fails when a second path appears.
   AGENTS.md Fact 3 says "vendor-neutral compute only". Meanwhile
   `codex_app_server.py` landed in #4517 about an hour after ADR-007's file
   landed. 49 core files name a vendor.
2. **New paths land additively, behind a switch or beside the old one.**
   - The thin loop: `agent_loop/` and `TINYASSETS_AGENT_LOOP` arrived
     together on 10-02 (#4282). The flag is still unset in every deploy
     file nine days later.
   - The task queue: `branch_tasks_v2.py` was added on 07-24. v1 was last
     edited on 10-11.
   - The scheduler: retired in #4097, then re-added by #4276 and #4590.
   - The box: `BoxProvider` (#4274) was built, but all 34 `role_*` cells
     were added after ADR-012 (10-06 to 10-10). That is the path the
     isolation cutover actually took.
3. **Supersession is a banner, not a deletion.** `one-extension-unit` says
   it supersedes six changes, and all six are still open. 16 changes are
   fully checked but not archived. `target-architecture` is 0/12 while its
   slices ship as other changes. `rename-universe-to-command-center` and
   `command-center-cutover` both come from the same 10-01 decision.
4. **Deletion only happens in founder-triggered sweeps.** The deletions that
   did happen: the prune lane #4098/#4101 (ADR-014, 09-29), the stale-shapes
   sweep #4544 (−33.8k, 10-07), the switch removal in 9d583154bfa (10-08),
   and #4560 after the clean-cutover re-steer. No lane's last task was
   "delete the old path".
5. **Several fleets, no shared map.** Since 09-01, 798 PRs merged. By
   branch prefix: `codex/` 201, `claude/` 147, `fix/` 147, `feat/` 58,
   `tiny/` (the founder's app agent) 7, `cursor/` 1, with five more open.
   The coordination failures:
   - The REST API was built twice (#4589, #4602). Both add a public
     `/api` (founder-approved as a public door, 2026-10-11).
   - #4608 and #4609 overlap this registry.
   - The isolation stack was stranded by the cutover re-steer.
   - The Codex model pin changed four times.
   - Concerns and their fixes were filed in separate PRs, six times on
     daemon links.
   - 24 design notes were added since 09-01. Five audits cover the pi/Muse
     harness.

   Each fleet patches the path in front of it. The live old path collects
   the patches, and the dark new path collects none.

**Root cause:** a shape is declared in prose with no executable check, and
its cutover lands additively (a flag, a parallel module, a "superseded"
banner) with deletion left to a later lane. Every fleet then patches the
path that is live, so the old path keeps getting stronger and the new one
stays dark.

## 3. The mechanism (no host online, script-enforced)

Founder correction (2026-10-11): reconciliation must not depend on the lead
session being online. Three parts, and nothing else.

**(1) Guards on every PR (C1).** `scripts/check_shapes.py` is registered in
`scripts/ci_structural_guards.py` as `test_shape_registry`. Every fleet's PR
runs it, because CI is the only place they all meet. It holds one `SHAPES`
table, keyed by registry id. Each entry has `paths` (globs), `old` (patterns
or modules with a ceiling) and `forbid` (patterns that must not appear).

| Check | Fails when |
|---|---|
| `registry-parity` | an id in the architecture table has no `SHAPES` entry, or the reverse, or a row's status does not match its measured ceiling |
| `ceiling` | a drifted shape's old-path count rises. Counts may fall freely, the same ratchet as `check_channel_agnostic.py` |
| `converged` | a converged shape's count is above zero, so an old path survives its cutover |
| `second-path` | a new module defines a registered entry symbol (model call, `session.start`, budget root, admission, box access, extension loader, agent definition loader, MCP handle registration) outside the shape's `paths` |
| `opt-in-flags` | a new `TINYASSETS_*` flag selects between two paths of one registered shape |
| `doc-homes` | a new file is added under `docs/` outside the homes: `architecture.md`, `decisions/`, `concerns/`, `host-actions.md`, `reference/`, `getting-started/`, `ops/` runbooks. This covers "a new design note duplicating a registered shape" without judging content |
| `adr-has-spec` | an accepted ADR names no existing `openspec/specs/` or `openspec/changes/` path. This replaces "a decided note without a spec" |
| `superseded-open` | an `openspec/changes/*/proposal.md` says it supersedes a change that still exists outside `archive/` |
| `public-routes` | a new public route prefix appears beside `/mcp` and `/app` |

The guard is about 300 lines. Its fixtures are this table. It replaces prose
rules: AGENTS.md Facts 3 and 15 become `vendor-names` and the existing
platform-LLM tests, so both lines are deleted and their numbers left as gaps
(AGENTS rule 8).

**(2) Reconciler (C2).**

- `.github/workflows/shape-reconcile.yml` runs on `schedule` every 4 hours,
  on `workflow_dispatch`, and on PR open, sync or close.
- Token permissions: `issues: write`, `pull-requests: read`,
  `contents: read`. The default token is enough, because it only edits an
  issue and triggers nothing (ADR-004).
- It runs `scripts/shape_reconcile.py`. That script imports `SHAPES`, lists
  open PRs with their files and remote branches, and runs
  `openspec_flow.py audit --json`.

It then rewrites ONE issue, "Shape reconciliation" (label `shape-registry`),
with a table of:

- lanes touching the same shape (two open PRs or branches on one row);
- PRs that patch a drifted row's old path without lowering its ceiling;
- changes that are complete but unarchived, superseded but open, or at 0/N
  for more than 14 days;
- branches with no PR for more than 14 days;
- registry rows whose ceiling fell, so the row can be marked converged.

It never merges, closes or edits code. Acting on a finding is the owning
lane's job.

**(3) Orientation.** AGENTS.md "Orient" already names the Shape registry
(#4619). C2 replaces the `openspec_flow.py audit` pointer with the tracking
issue, which contains that audit. Cursor, Codex, Claude and the founder's app
agent all read the same two things before they start.

No new docs, reviews or rituals are added: one guard, one workflow, one
issue.

## 4. Order (foundation first)

| Item | What | Folds | Depends on |
|---|---|---|---|
| C1 | Guards + document homes + document prune | #4609 step 9 | — |
| C2 | Reconciler + queue fold | — | C1 |
| C3 | One model call | #4616 step 1 (`one-model-call-path`) | C1 |
| C4 | Plugin mirror built, not committed | #4609 step 2, note 2026-10-01 | — (parallel; halves every later diff) |
| C5 | Dead limbs and dead spawn paths | #4609 steps 5, 6, 8; ADR-008 | C1 (parallel with C3) |
| C6 | One box per owner, platform state outside boxes, zero-host recovery | #4616 step 2; `target-architecture` S1–S6, S11; `platform-state-outside-command-centers` | C3, C5 |
| C7 | Control plane is the only always-on layer; one wake path; two limits | `control-plane-scheduler`, wake and limit clusters | C3, C6 |
| C8 | Agents are files; one starter | #4616 step 3 | C3 |
| C9 | One loop; native CLIs as external-agent packages; vendor code out | #4616 step 4; `target-architecture` S7 | C6, C8, `broker-streaming-contract` |
| C10 | Workflows and extensions as packages; one handle server; narrow market | #4616 step 5; #4609 step 7 | C8 |
| C11 | UI as a package; one durable send; `app.html` modules | #4616 step 6; #4609 steps 1, 3 | C10 |
| C12 | Knowledge is files; export | ADR-017, ADR-018 | C6 |

C3 stays the first product item. Nothing found is more foundational:

- C1 and C2 add no product behaviour. They exist so C3 cannot be undone by
  another fleet.
- C4 and C5 are deletions that touch disjoint files.
- The box (C6) depends on knowing which paths survive C3 and C5. Boxing
  paths that are about to be deleted is the 17-cell-fix pattern again.

Open question carried from #4616: is the C6 box the shipped owner cell
behind the `BoxProvider` seam, or Firecracker/gVisor? An ADR superseding
ADR-012's wording must answer it before C6's spec.

## Decisions

- **The registry is a table in the map.** It is not a new file, because
  ADR-005 gives each fact one home.
- **Guards count, they do not judge.** Ceilings and forbidden patterns are
  mechanical, so every fleet gets the same verdict.
- **Net-negative is measured, not promised.** Each item's PR states the
  `git diff --shortstat` net for executable code, excluding the mirror once
  C4 lands. A positive net does not merge (#4616 rule c).
- **Renaming rides along.** The `universe-name` ratchet falls as slices
  touch files. Both duplicate rename changes fold into it and are deleted.

## Risks

- **A ceiling miscount blocks unrelated PRs.** Ceilings may fall freely, and
  a rise names the file and pattern. This is the `check_channel_agnostic.py`
  precedent, which works.
- **Document deletion breaks citations.** About 40 code comments cite
  deleted paths. C1 repoints or drops each one, and
  `test_live_docs_reference_real_scripts` covers commands.
- **A reconciler issue nobody reads.** Orientation names it, and its rows are
  facts for the owning lane, not tasks for the lead.
