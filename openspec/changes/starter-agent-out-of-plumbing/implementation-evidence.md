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

## K2 verification and cross-family disposition

The broader Linux oracle passed **163 tests**, including the cost file first,
and the engine/node follow-up passed **107 tests**, also including the budget
test. The local Python 3.14 budget failure is therefore recorded separately
from Linux Python 3.11 results; the environment discrepancy is unresolved and
no threshold or test was weakened to reconcile it. The plugin mirror builds
627 files and its import probe passes. Ruff and diff whitespace checks pass.

Claude review via `peer-agents` completed successfully in 81 seconds against
831ee194cc: **VERDICT: ADAPT**, by code inspection (no reviewer tests).

- **AGREE F1:** code-node dispatch and grant intersection still depended on
  the visible registry. They now use the backend registry. A real engine/store
  regression projects four visible tools, permits the granted brain read and
  refuses an ungranted brain write before any mutation. Internal transport
  inventory validation also uses the backend registry; model sessions continue
  to expose and admit only the caller-selected handles.
- **AGREE F2:** native node deny lists must retain withheld backend handles;
  `_granted_config` now uses the backend registry. Claude four-only discovery
  remains explicitly pending activation, as already recorded above.
- **AGREE F3:** a module-local projection alone did not test the launchers.
  Added coordinator and Codex launch tests: four actual displayed handles,
  complete signed backend grant. The real code-node refusal regression covers
  the grant-intersection failure from F1.
- **AGREE F4, resolved:** merged origin/main 22bc0f728e after #4514 landed;
  kept its evidence plus K2's section through the add/add conflict. Product
  changes were identical across that merge.
- **DISAGREE_EVIDENCE** with the review's final assertion that read/write/edit
  are absent from backend capabilities: all three are literal members of
  `BACKEND_ENGINE_CAPABILITIES`, alongside bash. The coordinator/Codex and
  transport tests verify they remain grantable engine handles.

No second review round was requested. Follow-up Linux verification passed
**244 tests** (grants, transport, real code nodes, engine, coordinator, HTTP
loop, ta and ta jail); local focused verification passed 87 tests. Remaining
tasks 2.x, full resident budgets, natural
common-task trials, deployment, real-user acceptance and spec sync are undone.

## K2 continuation: D10 API slice

The founder authorized implementing D10 inside K2; the separate-worktree planning
note is superseded by that instruction. The existing nine-task lifecycle spec is
the contract. Added immutable manifests and a scoped SQLite journal/receipt API
in the daemon-owned sidecar, including candidates, notices, conditional file Undo,
hash-bound adoption, deletion tombstones, and crash recovery. No runtime consumer
is wired yet. API callers must supply the authenticated canonical center binding
and hold the turn boundary; this is not a model-callable authority surface.

Initial Linux oracle: 31 passed across manifest and lifecycle tests. Tests cover
stock/custom/empty/deleted paths, independent hooks, retries, pre/post-write crash
recovery with concurrent edits, Undo choices across versions, adoption, links,
schema mismatch, and forged transaction/blob/notice IDs across owners and centers.
No public MCP handle changed. Remaining D10 integration, whole-payload budgets,
four-tool activation, Muse capability proofs and final Claude review remain open.

## K2 continuation: lifecycle review corrections

Claude peer review (read-only, one round, 258 seconds) returned **ADAPT**.
AGREE: preserve the original install transaction/candidate set across Undo;
reject adoption from Undo/adopt transactions; retain per-version notice identity;
bound seed lock waits and make owner GET a read-only committed snapshot. These
API corrections are covered by regressions. The owner snapshot does not create
sidecars and does not take the tool boundary lock. The working-tree lifecycle
and release-consumer oracle passed **39 tests** on Linux Python 3.11.

Consumer integration and other review findings are still being verified. Native
Codex built-in tools and the opt-in remote thin-loop capability bridge remain
release blockers; a four-MCP-schema projection is not proof of those actual
model inventories. Do not merge or deploy this draft as a completed cutover.


## K2 continuation: consumer cutover and capability proofs

This section supersedes the earlier prerequisite-only and API-only status.
The D10 API is implemented and wired to new center provisioning, shared turn
admission, and persona assembly using the authenticated canonical center owner.
The renderer reads editable instructions without reseeding or default substitution.
Automatic migration preserves custom, empty, deleted and linked files, installs
independent hooks/skills, delivers a durable version notice, and exposes owner-only
hash-bound adoption and conditional Undo. Failed new provisioning archives its new
sidecar before rollback so a later center cannot inherit a false install receipt.
D7 extraction is retained; successful ta memory writes carry daemon-produced
structured receipts so they do not trigger duplicate extraction.

The ordinary HTTP adapter and Claude engine inventory expose read/write/edit/bash;
backend grants remain independent. Backend-only grants get a restricted ta command
transport without arbitrary shell or extension execution. Real Linux ta-jail tests
exercise the signed broker, including forged stdout and denied-write controls.
The public MCP connector surface is unchanged.

The 2026-10-06 founder Muse-fit matrix (PR #4518, head
4a2d09680a650d838ef54bdb42306d025f8899d5) is covered by real store/engine task
proofs: multi-step chat and skill discovery; sensitive connection/publication
approval requests; HTTP/MCP connections and Google Calendar OAuth requests;
automation/workflow creation, reads and pause; notifications; memory read/write/
forget; editable name/preferences and onboarding; document file creation;
app_ui add/activate; and owner-confirmed publishing with private-file exclusion.
These are deterministic adapter/ta task proofs, not live model-family trials or
proof of a completed Google OAuth consent flow.

### Payload measurement

Budget uses Unicode characters / 4, an estimate rather than a vendor tokenizer.
Real input schemas are included; dynamic owner content is measured separately and
is not truncated to make the stock budget pass. Stock system text is 2,350 chars.

| Adapter-supplied payload | Schema chars | Total chars | Estimated tokens |
| --- | ---: | ---: | ---: |
| HTTP | 1,574 | 3,924 | 981 |
| Claude MCP projection | 1,510 | 3,860 | 965 |
| Codex MCP projection | 1,442 | 3,792 | 948 |

The pinned owner-context fixture adds 1,214 chars in every row. The external audit
baseline is 47,203 total chars; the earlier reproduced 14-tool schemas alone were
38,748 chars. Descriptions fall from 32,353 to 490 chars. Static ratchets remain
in tests/test_converse_turn_cost.py and tighten to 500 description chars and 220
harness-head chars (actual 215), plus the new 4,000-char stock envelope budget.
**Native projections exclude opaque CLI-added instructions/tools.** They do not
prove the whole native envelope fits 1,000 tokens. The opt-in remote thin-loop
adapter also remains outside the claimed four-tool cutover.

### Final review disposition

Required cross-family review via peer-agents/Claude: **ADAPT**, one read-only
round, 258 seconds, no reviewer tests. AGREE F1: moved preparation to shared
admission/persona entrypoints and provisioning. AGREE F2/F3: preserve original
install offers after Undo, reject adoption from owner-choice transactions, use
read-only owner snapshots without seed locks, bound mutation waits to five seconds.
DISAGREE_EVIDENCE only with F3's uncaught-PermissionError subclaim: PermissionError
is an OSError subclass already handled by the existing handler. AGREE F4/F5 remain
open: native Codex inventory and the remote box ta bridge block release. These are
recorded in docs/concerns/2026-10-06-k2-native-and-box-inventories.md.

The repeated node fixture failure was handed to Claude for bounded diagnosis and
fixture repair under AGENTS rule 7, not another review round. The fixture now signs
a real route; the subsequent wire regression fixes FastMCP ToolResult metadata
rather than serializing an MCP result as a string. All 27 agent-node tests pass.

Linux verification batches: 238 passed (UI, memory, approvals, engine security,
provider sandbox and file-read guards); 282 passed (workflow, common-task ta,
provisioning, first contact, visibility/privacy, learning and payload); 162 passed
in the earlier mixed batch with six workflow-fixture failures, all six fixed and
covered in the 282-pass run. Earlier 173-pass and 132-pass batches cover the other
changed guidance/grant suites; the latter's sole old resident-guidance expectation
was moved to the on-demand skill and passed in the 238-test batch. No skipped,
xfail or removed assertions were introduced to handle failures. Final checks follow.

Do not merge/deploy this draft as a complete cutover. Native inventory/envelope,
remote authenticated ta transport, atomic exclusion of native apply_patch,
N>=10 paired model-family trials, deployed-SHA assertion, real owner app acceptance,
and spec sync/archive remain undone. No provider substitution or native inference
shutdown was used to claim compliance.

Final additional Linux batches: **215 passed** (lifecycle, renderer, sessions,
harness, raw-I/O ratchet and guidance) and **187 passed** (tightened payload
ratchets plus affected heavy provider authority/retry, server isolation and
cycle suites). Ruff passes all changed canonical Python files; plugin rebuild
copies 630 files and its import probe passes; whitespace check passes. Staged
hygiene against origin/main reports **39 added / 0 removed / 0 tampering**.

Merged origin/main a97c17c26e (including #4518 and #4515) before final push.
Post-merge Linux oracle: **96 passed**, covering copied-agent templates/system
browser, delivery/account deletion, common ta tasks and stock payload budgets.
The rebuilt mirror is unchanged and passes its import probe; final hygiene still
reports 39 added / 0 removed / 0 tampering. Both starter OpenSpec change gates
return ALLOWED. No deployed/live-acceptance claim or spec archive was made.
