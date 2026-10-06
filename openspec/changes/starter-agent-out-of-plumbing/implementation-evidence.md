# L14 preparation evidence

## Scope decision

Prepare the editable bundle and generic ordered reader without activating the
renderer. The spec explicitly permits content preparation while D6 is unproved.
D10's transaction API is absent and its tasks are all unchecked in this checkout.
Do not substitute a second installer or remove existing guidance before the
single coordinated release. No handles, runtime prompt assembly, extraction or
static prompt budgets change in this slice. No deployment claim is made.

## 1.1 Baseline investigation (partial)

Pinned supplied baseline: b2bcfca0ef36d97070ab371a37ae170cedcfee71.
Pinned implementation starting point: 70c30da9f94e7a3e09d0e18dd18bbb6d747ed7ea.
Python AST literal lengths from those revisions:

| Fragment | Supplied baseline characters | Starting-point characters |
| --- | ---: | ---: |
| DEFAULT_OPERATING_INSTRUCTIONS | 1337 | 1649 |
| _GROUNDING_IS_CURRENT | 465 | 465 |
| _CROSS_SURFACE_CONTINUITY | 686 | 686 |
| _UNRECORDED_LESSON (retained, D7) | 678 | 678 |
| _HARNESS_HEAD | 1186 | 1263 |

Reproduce by parsing `git show <sha>:tinyassets/universe_intelligence.py` and
`git show <sha>:tinyassets/universe_tools.py` with ast.parse, then applying
len(ast.literal_eval(assignment.value)) to the named assignments.

Existing HTTP synthetic-wire baseline: Linux oracle Python 3.11.17,
bubblewrap 0.12.0, tests/test_converse_turn_cost.py: **10 passed**. This preserves
the extraction-on accounting tests (three calls after a non-writing tool step,
two after a successful governed write). The synthetic free-model fixture is not
a pinned tokenizer or natural-task token measurement. HTTP/Codex/Claude full-task
tokens, outputs, skill loads and paired model trials remain unmeasured. No
D6-only SHA or adapter-specific savings are asserted; task 1.1 stays unchecked.

## 1.2 Prerequisite investigation (partial)

The checkout's ta CLI supports search, describe and invocation. write_graph's
description links on-demand read_graph handbook chapters. Prepared skills use
that discovery path and link to current references rather than embedding API
payload recipes. No old handle is removed. Deployed permissions/recipes and
the <1000-token D6 core-plus-schemas precondition remain unverified; task 1.2
stays unchecked. D6 must establish them before activation.

## 1.3 Prepared consumer surface (partial)

`starter_agent_files()` publishes seven path/content pairs: editable AGENTS.md,
starter/hooks.md, and five starter skills. The hook is 867 characters; AGENTS.md
is 569. These are character counts, not tokenizer evidence. Skill bodies are
on demand and parse through the existing skill index. Hooks cover memory,
continuity, input method, incomplete onboarding, workspace/skills, current-file
grounding and response priority. The starter AGENTS content does not duplicate
the hooks. No new text enters the always-sent head.

`read_instruction_files(selected_agent_root)` reads actual hooks before AGENTS,
labels sources and returns explicit read-failure notices. It does not install,
substitute defaults, write receipts or select an agent. The caller must authorize
the owner and supply the selected agent root. It is deliberately not wired into
the live renderer until D10 installation and D6 acceptance are ready.

Tests cover custom/empty/missing AGENTS, hook edits/emptying/deletion, a separate
selected root, linked files/parent directories, and unreadable/invalid text.
D10 installation, collision/former-default notices, Undo, owner precedence in
the complete renderer and replacement-main integration remain pending. Task 1.3
stays unchecked because publishing the source and reader does not complete them.

## Remaining work

2.1-2.3 await verified D6 and D10 prerequisites and the coordinated consumer
cutover. 3.1 and 3.2 await real per-adapter costs and the paired model matrix.
3.3 and 3.4 remain unchecked as requested; no spec sync, archive or live
acceptance was performed. D7 learning paths remain unchanged.

## Verification of the prepared slice

Linux oracle (MSYS_NO_PATHCONV=1):
`python scripts/linux_oracle.py -- -q tests/test_starter_instructions.py tests/test_converse_turn_cost.py tests/test_universe_file_reads_are_bounded.py --basetemp /tmp/b`
passed **36 tests** on Python 3.11.17 / bubblewrap 0.12.0, uid 1001.
Ruff passed on the three changed Python files. The plugin mirror build staged
621 files and its import probe passed. Test hygiene against origin/main reports
**5 added test functions, 0 removed, 0 tampering**. No affected heavy file is
listed for this unactivated reader/content surface. Existing prompt ratchets
are unchanged; no resident savings claimed.

Merged origin/main b61e68934c (#4508) before the final push. Its harness dependency
handoff agrees that the selected agent's installed roster directory must be
authenticated; do not derive a root from an untrusted agent/binding name.

Cross-family review: Claude via peer-agents, 2026-10-05, completed successfully
in 98 seconds with **VERDICT: APPROVE** and no floor/correctness blockers.
AGREE: safe reads, owner preservation, no accidental installation, explicit
dependency boundaries and mirrored/package content. N1: Claude's Windows test
attempt had three symlink setup failures (WinError 1314); DISAGREE_EVIDENCE as a
blocker because all cases passed on the required Linux oracle. No skip or xfail
was added. N2: AGREE that branch/worktree wording can become stale; retained as
an implementation-in-progress handoff, not permanent architecture documentation.
# Lane K2 — four tools plus starter cutover (2026-10-06)

Owner: Codex. Branch: `feat/four-tool-starter-cutover`, worktree `wf-K2`.
This lane absorbs the content/reader commits from #4514. Do not merge as a
completed cutover: tasks 2.x remain incomplete.

## Implemented prerequisite

Backend grants now use `BACKEND_ENGINE_CAPABILITIES`, independently of
`SERVED_ENGINE_MCP_TOOLS`. HTTP transport accepts a separately validated backend
grant, signs that grant on the private route, and exposes/calls only the selected
model handles. Its coordinator supplies both values. Codex likewise selects its
displayed handles through `model_tools` while signing the complete backend grant.
Existing narrowed node grants and connection reach remain unchanged.

Regression tests reduce model visibility to four handles and verify that signing,
verification, node grant validation and ta catalog reach retain the backend
capabilities. They also reject invalid grants and attempts to call an undisplayed
handle directly through the HTTP model session. The ta test proves routing and
catalog preservation, not a model's end-to-end common-task competence.

## Activation blocker and decision

`tinyassets/starter_skills.py` and the absorbed source bundle exist, but the D10
transaction API does not. `openspec/changes/starter-seed-lifecycle/tasks.md`
still has all nine implementation tasks open. The starter design's Migration
Plan step 3 and task 2.3 explicitly require this API for stock upgrades,
custom/deleted preservation, visible notices, Undo and dormant-center recovery.
Removing resident advice now would strand existing centers that have never
received the hooks/skills. Recreating those files during prompt assembly would
violate the requested removal of per-turn seeding and the single-installer design.

Decision: implement the independent grant prerequisite and retain today's
14-handle renderer until the coordinated cutover can use D10. Do not introduce
a second installer or silently claim the four-tool cutover is active. No public
MCP connector implementation or handle was changed. D7 extraction is unchanged.
Claude discovery/built-in filtering and the optional thin box loop still require
cutover work; this prerequisite does not claim they are four-tool-only.

## Reproduced measurements

At main `adf29db4e7` plus the absorbed content and grant refactor, the actual
engine schemas serialized with `agent_chat_codec.tool_definitions` and default
`json.dumps` measure:

| Selection | Description characters | HTTP serialized schema characters | Estimated schema tokens |
| --- | ---: | ---: | ---: |
| Current 14 handles | 32,353 | 38,748 | 9,687 |
| Four-handle projection (not activated) | 490 | 1,574 | 393.5 |

Estimate is characters / 4, not a model tokenizer or measured billing. This is
schema-only, not whole resident payload. Owner context and native CLI envelope
costs remain unmeasured; the audit's 47,203-character system-plus-schema total is
an external baseline, not a new measurement. No whole-payload 1,000-token pass or
runtime savings is claimed. Existing prompt ratchets were not changed.

An isolated local run reproduces the existing description-budget failure:
32,353 > 30,100. A combined Linux run with ta tests first reported 50 passed;
that does not supersede the isolated failure. The broader oracle run puts the
cost file first to expose it. Final verification and review results follow below.
