Reviewing the diff and grepping every executor caller now; looking at universe_intelligence's internal entry points next.

Checking how graph prompt nodes and queued runs execute (whether any bypass session.start).

Entry points check out so far; checking inbound hooks, the domain runner, and the guard's strength.

Found a suspicious tools-enabled model call in ta_capabilities; inspecting it.

Now examining the "two loops" claim against S3's converged status.

Confirmed C9 = "one loop" in shape-convergence; checking its status, then running the guards.

A spawn-entry test fails on the branch; checking why and whether main fails too.

check_shapes.py exits 1 on the branch. Checking whether CI runs it and the PR's check state.

Confirmed in CI. Last check: how many matches the removed probe still has on HEAD.

I don't agree with the PR as written. All the live start paths do go through `session.start`, but the "converged, ceiling = 0" claim was reached by swapping out the guard that measured the duplication, and CI fails because of it. I didn't edit anything. The one Linux-only test was not run here: `test_every_parent_kind_resolves_the_agent_from_its_files` fails on Windows because the owner-box double needs Linux, so neither a pass nor a fail counts for it.

## Findings

**1. HIGH: the zero ceiling comes from replacing the probe, and `structural-guards` fails in CI** (`scripts/check_shapes.py:70-76`)
- On main, S2's probe was `\b(?:_call_writer|make_interactive_agent_turn)\(` with a ceiling of 5. On HEAD that same regex still matches **5** lines, so the executor duplication it measured hasn't shrunk at all.
- The new probe only counts `call_background_work_agent` / `_call_work_agent` (plus `session_start` / `start_agent`, which never existed). This PR deletes those, so the count drops to 0 automatically. It's a new pin, not a reduction.
- The registry is shrink-only, and it rejects this. `python scripts/check_shapes.py` exits 1 with `S2 ceiling: old-path probe removed`. CI's structural-guards job fails the same way: `tests/test_shape_registry.py::test_shape_registry`, "1 failed, 769 passed".
- The PR body says "Targeted Linux oracle: 28 passed (… shape registry …)". That contradicts CI.
- The `Diff scope declared` check also fails because the body has no `Lane:` declaration.

**2. MEDIUM: the registry now records the executor drift nowhere** (`docs/architecture.md:155-156`)
- The old S2 row listed the two executors with their own admission gates as drift. The new row moves that to "S3/C9", but S3 still says drift "none", status converged.
- C9 is "One loop. The thin loop becomes the only loop." (`openspec/changes/shape-convergence/tasks.md:145`). The `session.py:25-26` docstring still says the executors are "C9's one loop in waiting".
- What actually exists:
  - Chat: `_call_writer` → `InteractiveHttpAgentTurn` (`ThinLoopChatAdapter`), or the non-HTTP `text_sync` path (`universe_intelligence.py:1005-1031`).
  - Agent nodes and activities: `call_foreground_work_agent` → `WorkflowAgentTurn` (`WorkAgentAdapter`), whose `run()` override only remaps errors (`workflow_agent.py:233-286`).
  - Both subclass `AgentTurnCoordinator`, so the loop itself is mostly shared. What's duplicated is the two executor and admission wrappers plus chat's `text_sync` path.
- So S3's own provider-file probes being at 0 is defensible. But "S2 converged / S3 none" leaves the real duplication on no row. The S2 row should stay drifted with the old probe kept and its ceiling at 5, or a probe should be added to S3 that's actually tracked.
- "C9" is also ambiguous: in `owner-daemon-in-box/design.md:539`, C9 is the wake index (T7).

**3. MEDIUM: the guard catches reuse of known executor names, not a genuinely new second path** (`tests/test_spawn_entry.py:20-40`)
`STARTER_CALLS` lists specific symbols. None of these would be caught:
- A new module that builds `InteractiveHttpAgentTurn(...)` directly. It isn't listed; the guard only covers its factory, `make_interactive_agent_turn`.
- A new `AgentTurnCoordinator` subclass.
- A new caller of `text_sync` with `engine_mcp_enabled=True`.
- `from tinyassets.universe_intelligence import converse; converse(...)`. The regex only matches `intelligence.converse(` / `universe_intelligence.converse(`, yet that bare-import form is exactly how `session.py:132` calls it.

Also, putting `session.start(` itself in the allowlist means a legitimate new caller of the one starter fails the test, while a new starter under a fresh name passes.

**4. LOW: tools-enabled model calls from `ta` sit outside `session.start`** (`tinyassets/ta_capabilities.py:497-508`)
`ta call model` with tools sets `engine_mcp_enabled=bool(tools)` and goes through `invoke`. It uses caller-defined tools (`agent_request` set), so it reads as a model call (S1), not an agent start. Worth saying explicitly in S2/S1 so it doesn't grow into one.

**5. LOW: "the domain runner is not a shipped entry point" is true but loose** (`pyproject.toml:123`, `tinyassets/checkpointing/sqlite_saver.py:153-184`)
`domains` still ships in the wheel. `compile_all_graphs` (exported from `tinyassets/checkpointing/__init__.py`) compiles the fantasy graphs, whose phases call `text_sync`. No live caller or console script exists, so this isn't a live path, but it is shipped code that can run a model loop.

## The other questions

- **Is there only one way to start an agent?** Yes, for every live path I traced:
  - Chat: `universe_server.py:3422`.
  - Owner answers: `request_answers.owner_turn` → `converse`.
  - Wakes: `agent_wakes.py:231`.
  - Continuations: `continuations.py:172`.
  - REST: `rest_api.py:254` → `converse`.
  - Inbound events and automations: the C7 owner tick creates activities, which run via `activity_session.py:110`.
  - Agent nodes and queued runs (including conversation designs through `consumer_runtime` → `dispatch_initial_run`): `foreground_run_provider.py:787`.
  - `ta agent run`: `ta_capabilities.py:293`.
- **Did deleting the background executor drop live behaviour?** No. Its last caller was removed in C7 (#4845, `6b0856a5f5a`), and `git grep` on origin/main shows only the definitions. Scheduled runs and automations now go through the owner tick → activity → `session.start`, which matches S9.

VERDICT: DISAGREE_EVIDENCE
