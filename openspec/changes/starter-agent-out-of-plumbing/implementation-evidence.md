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
