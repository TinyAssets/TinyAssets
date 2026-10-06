# L14 preparation evidence

## K2 round 4: answers to review round 2 of 007e13e99b (ADAPT, 2026-10-06)

**(a) The guard sees what each adapter requests.** `open_engine_tools` is
replaced by one recording stand-in whose session lists exactly the tools asked
for, drawn from all 14 backend capabilities the route registers. HTTP is
rendered by the real `AgentTurnCoordinator` up to its executor: `_open_tools`
makes the request, and the codec encodes that round's own `agent_request`.
Codex is rendered by its real launch. Both renderers also require one request
for the owner's route with exactly `model_tools(config)`. The new test
`test_the_guard_fails_an_adapter_that_asks_for_its_grant` points
`model_tools` at `granted_tools` in the coordinator and in `codex_provider`.
Against the previous guard it fails for `http:openai_chat` and `cli:codex`;
it passes now.

**(b) Activities are checked per provider.** The native probe now runs that
provider's real `complete` on a launch that never finishes: Claude on a
stalled stream-json process, Codex on an app-server whose turn hangs. It then
yields the activity. The probe passes only if the call ends with
`ActivityYielded` and the provider killed its own process.
`test_the_guard_fails_a_provider_that_shields_itself_from_cancellation` wraps
each provider's `complete` in `asyncio.shield`. Against the previous guard it
fails for both providers; it passes now.

**N1: Codex AGENTS.md injection is closed and the real CLI shows it.**
- `SERVED_LAUNCH_ARGS` adds `-c project_doc_max_bytes=0`.
- `_codex_home_file_mounts` binds only the files named in
  `codex_launch_contract.SERVED_HOME_FILES`, which is `auth.json`. No
  0.160.0 setting suppresses a CODEX_HOME `AGENTS.md`. Tried and failed:
  `instructions=""`, `user_instructions=""`, `include_user_instructions=false`
  and `features.agents_md=false`; `--disable agents_md` is rejected outright.
  So the home is built by name.
- `scripts/codex_cli_smoke.py` builds CODEX_HOME the same way, from a snapshot
  that also holds an `AGENTS.md` and an `AGENTS.override.md`. It plants an
  `AGENTS.md` in the working directory too. It requires the request's
  instruction text (the `instructions` field plus any developer or system
  messages) to equal `baseInstructions` exactly, and the user messages to be
  exactly the prompt.

Real codex-cli 0.160.0, credential-free loopback capture: PASS. Two negative
controls fail with "a planted AGENTS.md": dropping `project_doc_max_bytes=0`,
and adding `AGENTS.md` to `SERVED_HOME_FILES`.

**Lower concern: confirmed and closed.** Claude CLI 2.1.291 ran as
`claude_provider` launches it, with cwd at the universe root and
`--setting-sources project`, in a credential-free capture. It sent the
universe's `CLAUDE.md`, `.claude/CLAUDE.md` and `.claude/rules/*.md` to the
model, and it ran a `.claude/settings.json` `SessionStart` hook.
`CLAUDE.local.md` was not loaded. With `--setting-sources ""`, none of the
docs is sent and the hook does not run.

Production now passes `--setting-sources ""`, which is strictly narrower:
user-tier settings stay excluded as before. The tests:
- `test_the_universe_the_agent_writes_is_no_setting_source`;
- the guard's Claude renderer, which counts the project docs as added
  instructions.

Both fail with the old flag. `scripts/native_cli_payload.py` mirrors the new
flag.

## K2 round 3: answers to the cross-family review of 1834f866b8 (2026-10-06)

Codex reviewed 1834f866b8 and returned BLOCK. It agreed the Codex tool
reduction works: exactly `bash, edit, read, write` on 0.160.0. Each finding is
answered below. Commits c80ceb85dd, then the merge of origin/main 341115ee5a,
then the round-3 commit.

**(1) The guard does not fully guarantee the founder rule. AGREE, fixed where
code can prove it; the rest is narrowed with evidence.**

- HTTP-only budget line: fixed. `AgentTurnCoordinator._run` builds one
  `instructions` string, the budget line included, and passes it to both
  execution kinds. The guard
  `test_the_turn_gives_every_executor_the_same_instructions` drives the real
  coordinator for both kinds, with and without a budget. With the fix
  reverted it fails on the budgeted case.
- Claude tools are now observed, not substituted. The guard asks the engine
  route, through its middleware, for the tools it lists to Claude's own MCP
  URL (signed grant, `model_inventory=four`). It compares those schemas with
  the definition.
- Capabilities are now behavioural, not inferred from source text. The HTTP
  tool session is the one `AgentTurnCoordinator._open_tools` dials.
  `activities` now means two things for each kind: a launch happens, and a
  yield stops the agent. A native call is cancelled mid-flight; an HTTP turn
  starts no further round.
- Unsupported HTTP protocols are now checked against the real refusal:
  `model_policy._ineligibility` returns `executor_unsupported` for a
  connection with no agent codec. Ollama is checked against the router's
  refusal (no agent execution kind).
- DISAGREE_EVIDENCE on "every provider invocation": the non-served
  `codex exec` path (`CodexProvider.complete` without `sandbox_workspace`) is
  a workflow prompt node using Codex as a text model, not an agent turn. The
  founder rule covers agents. `test_only_the_json_path_streams_and_the_legacy_path_is_verbatim`
  pins that split.
- Decided (lead, 2026-10-06; accepted as vendor framing, no capability difference): claude-code 2.1.290 prepends its identity line and
  environment block under subscription OAuth, and has no switch for it. See
  the K2 concern.

**(2) App-server lifecycle. AGREE, all fixed.**

- Credential-bearing RPC errors: `AppServerTurn.run` scrubs the server's error
  text with `_redacted_stderr_excerpt` before clipping. The router's cooldown
  log line now logs `redacted_failure_detail(str(exc))` for every provider.
  Tests: `test_a_refused_request_is_reported_without_its_secrets` (initialize,
  thread/start, turn/start) and `test_a_provider_error_is_logged_without_its_secrets`.
- Handshake cancellation leak: every `call()` cancels and awaits its request
  task on any exit, and `request()` drops its pending future. Test:
  `test_a_callers_cancellation_propagates_through_the_reap`, which cancels
  before `initialize` is answered. Removing the cleanup makes it fail.
- Stale resumed instructions: `thread/resume` carries
  `baseInstructions` = the current definition's instructions
  (`codex_app_server.thread_resume_params`). Request capture on real
  codex-cli 0.160.0 (loopback endpoint, temporary home, no credential):
  a resume with `baseInstructions: B` sent B and not the start's A. A resume
  without the field sent A again, so the override is not persisted and must
  be sent on every resume. Tools persisted across the resume (`read`). The
  user input no longer carries an instructions-changed preamble, and
  `agent_sessions.resume_input` is deleted. Tests:
  `test_next_turn_resumes_and_sends_only_what_is_new` and
  `test_changed_instructions_replace_the_resumed_threads_own`.
- Swallowed tool-task exceptions: helper tasks report failures through
  `_settle`. A tool failure outside `EngineToolError` answers Codex with a
  failed call, then fails the turn as
  `ProviderError("codex tool call failed unexpectedly: <type>")`, with the
  exception as its cause. Test: `test_an_unexpected_tool_failure_fails_the_turn_with_its_cause`.

**(3) Stop/yield for every capability. AGREE, fixed.** Six boundaries, each
tested:

1. The engine route serializes an activity's top-level calls
   (`ActivityFence`). A call queued behind the one that yields is admitted
   only after the yield, and is refused. A nested `ta` platform call
   re-enters the route without queueing behind its own parent, and still
   meets the fence. Tests in `tests/test_activity_fence.py`.
2. `Capabilities.dispatch` checks the activity on every `ta` request,
   connection calls and the catalog included
   (`test_every_ta_request_is_refused_once_the_activity_stops`).
3. When a `ta` request stops the activity (the agent's own ask), the bridge
   withholds the answer until the jail is dead (`universe_tools._halting`).
   The command is blocked in that call, so no later line of it runs
   (`test_the_command_that_yields_never_gets_past_its_ta_call`).
4. The jail supervisor polls the activity and kills a running command once
   it stops (`killed == "activity_stopped"`). This also covers background
   jobs. Tests: `test_a_running_command_is_killed_once_its_activity_stops`, and
   the engine wiring in `test_an_activity_bash_is_handed_the_stop_its_activity_polls`.
5. Codex dynamic tool calls run one at a time
   (`test_tool_calls_run_one_at_a_time_in_the_order_asked`).
6. A native call made for an activity is cancelled once the activity yields,
   pauses or stops (`WorkAgentAdapter._until_activity_stops`). The provider
   ends its process family and the router settles the seat, at the point
   where an HTTP turn would start no further round. Tests:
   `test_a_native_activity_call_ends_when_its_activity_stops` (yield, pause,
   stop) and `test_an_activity_yield_kills_a_native_cli_turn_and_releases_it`.
   The latter runs the real Claude adapter against a real process tree: the
   tree is gone, the turn is `held_native_unknown`, and the claim is
   released. It is Linux-only and runs in the oracle.

With these in place, the native-only admission refusal #4524 added
(`activity_runner.require_supported_executor`) is lifted, as its concern
note anticipated. `test_native_only_start_refuses_before_creating_activity`
is retired, and `test_native_only_start_queues_the_activity` replaces it.
#4524's incident record is folded into the K2 concern. The
`agent-turn-runner-liveness` requirement ("refuse unsupported activity
starts" when the executor "cannot safely yield") still holds. No registered
agent executor fails it any more, and a kind with no agent execution
(Ollama) is refused by the router before any agent turn.

**(4) Lost watchdog guarantees. AGREE, all restored.**
`tests/test_codex_stream_watchdog.py` is back, with all 29 test names ported
to the app-server path. Each has at least its old assertion count. The
ported tests cover:

- startup stall (`launch` phase);
- progressing-but-capped;
- long tool and wedged tool (tool wait, then cap);
- malformed output that does not reset the clock;
- a 70 KB event, from a real child process;
- EOF from a live child: it is ended and no task leaks;
- completion closing an open tool;
- a failed terminal closing an open tool;
- a recoverable `error` notification;
- structural-only completion;
- the config knobs (unbounded served cap, overrides, nonsense fallback,
  library cap).

The phase telemetry (`phase`/`tool_phase`) is restored on every timeout. The
real-vocabulary test now replays a real codex-cli 0.160.0 app-server turn
(`tests/fixtures/codex_app_server_0160_turn.jsonl`, 15 messages including a
numeric-id `item/tool/call`). It was recorded against a loopback Responses
endpoint that asked for one `read` and then replied, with no credential.
Other removed codex tests are back under their names on the new path:
compat terminal-reason precedence, stderr fallback, last `error` kept,
scrub-before-clip, nonzero-exit confinement, recorded-stream attribution,
rollout-claim attribution, the engine route bearer/owner checks through the
real `_served_engine_tools` + `open_engine_tools`, and the four engine-MCP-args
guarantees. A failed turn with no reason now keeps the last `error`
notification.

**(5) Credential-error handling. AGREE**, fixed as in (2). The ownership
checks the review confirmed are unchanged.

**Oracle failures from the lead's run (411 passed / 5 failed).**

- `test_http_activity_yield_*` (4): regressed by 6a0f104097, not by the
  app-server change. Bisected: 274adfc1ba passes, 6a0f104097 fails. Only
  the four tools are model-visible since then, and the fixture rewrote the
  model's call to the hidden `write_graph` handle. It now asks through
  `bash` → `ta call write_graph`, the path the model uses.
- `test_served_turn_spawns_fake_codex_through_full_os_sandbox_command`: the
  sandbox does wrap the new launch. The test now asserts the whole chain:
  bubblewrap with the full-deny seccomp profile (`nested_sandbox=False`,
  recorded at `jail_seccomp.program_fd`), then `prlimit` with every
  `PROVIDER_LIMITS` flag (`--nproc` ≤ 512), then the `-I -S` egress
  forwarder, then the codex command running `app-server` with
  `SERVED_LAUNCH_ARGS`.

**Round-3 continuation: merge of origin/main with the remote-box ta bridge
(#4525), 2026-10-06.** Every disposition above was rechecked against the
merged code: each cited fix and test is present. Merge interactions:

- `engine_mcp_server.py` kept both sides: the bridge's private
  `ta-bridge://request/{payload}` resource and the four-tool `bash`
  declaration (`output_schema=None`). A remote box's `ta` request goes through
  `ta_capabilities.engine_dispatch`, which carries the activity stop check, so
  finding (3)'s boundary 2 covers it too.
- The thin loop (`ThinLoopChatAdapter`) handed `open_loop_tools` the backend
  grant as if it were the model's tool list. On this branch that is the 14
  backend capabilities, so the engine session asked for `read_graph` & co. as
  listed tools and failed `engine_tools_missing`. It now passes the four
  model tools for display and the backend grant as `capability_grant`, the
  split `AgentTurnCoordinator._open_tools` already uses.
  `test_engine_tools_keep_their_engine_route` keeps its name and claim on
  the K2 path: the model is offered only the four box tools and the loop's
  owner reads, and the engine session shows `bash` alone but carries the
  turn's backend grant. A `ta call read_graph` in the box reaches the
  engine route verbatim over that session, and its answer is delivered back
  to the box. Dropping `capability_grant` makes it fail.
- `test_stop_during_a_tool_call_lets_it_finish_and_starts_nothing_after`:
  the completed tool is now `bash` running `ta call read_graph`, the model's
  only path to it since 6a0f104097. Both are asserted.
- Two clock-driven waits this branch added are classified `CALL_SCOPED` in
  `tests/control_plane_timer_inventory.py`: `AppServerTurn.read`, which reads
  one app-server turn, and `WorkAgentAdapter._until_activity_stops`, which
  polls one native activity call.
- `scripts/ci_structural_guards.py` found two more classification gaps in
  this PR. `tinyassets/starter_seeds.py` (6a0f104097) commits its seed store
  without an owner fence, so it is listed in `FENCE_BEFORE_C2`. The
  channel-agnostic baseline follows the Codex adapter split: before the split,
  `codex_provider.py` had 72 `codex` mentions. After it, `codex_provider.py`
  has 55, `codex_app_server.py` 15 and `codex_launch_contract.py` 1, so 71 in
  total, one fewer than before.

**Merge interactions with origin/main (4 commits).**

- #4520 added seven starter skills whose index lines are resident. The stock
  payload rose to 4,546 / 4,482 / 4,414 chars (http/claude/codex). The
  ratchet moved 4,000 → 4,600 with the cost stated in the test.
- `test_a_request_larger_in_bytes_than_the_window_fits_by_its_tokens`
  regressed by this branch's four-tool payload: the request is now 1,186
  bytes, so the answer reserve dominated the window. The answer reserve is
  now 64 tokens in both runs; the byte-vs-token claim is unchanged.
- `test_exactly_four_tools_are_served_to_the_universe_agent_on_every_adapter`
  imported the removed `_ENGINE_MCP_ENABLED_TOOLS`. It now checks codex's
  `model_tools`.
- `test_turn_interrupt`'s native coordinator now grants the owner binding
  that `prepare_starter` (6a0f104097) requires.

## K2 round 2: one definition across providers (2026-10-06)

Lead decision applied: provider-specific translation is allowed, capability
differences are not. `tinyassets/agent_definition.py` is the one definition
(the engine route's granted tools with their schemas, the turn's instructions,
`AGENT_CAPABILITIES`). `tests/test_one_agent_definition.py` renders it through
every registered kind's real launch code: `cli:codex`, `cli:claude-code`,
`http:chat_messages`, `http:openai_chat`, `http:content_blocks`,
`http:anthropic_messages`, `local:ollama`. Kinds with no agent executor must be
refused for agent turns. On this branch's round-1 head (79fd2246f3) it fails
for `cli:codex` (no app-server launch exists) and `cli:claude-code`
(capabilities lack `activities`); on `origin/main` it cannot collect
(`FOUR_MODEL_TOOLS` is absent). It passes after: 9 passed.

### Codex: option B plus the catalog route

Captured with loopback sinks, codex-cli 0.160.0 (`$APPDATA/npm/codex.cmd`):

| Launch | Model-visible tools | Resident chars |
|---|---|---|
| app-server, `dynamicTools` = four, no MCP, no reduction | `exec`, `wait`, `request_user_input`(+`_async`), `clock.sleep`, 6 `collaboration.*` | 25,547 |
| + every documented switch | `exec`, `wait`, `request_user_input_async`, 6 `collaboration.*` | 16,563 |
| + `environments: []` | unchanged | 16,321 |
| + reduced `model_catalog_json` (the production launch) | `read`, `write`, `edit`, `bash` | 1,571 |

No MCP server means no resource tools, so option A (adding resource list/read to
every provider) was not needed. Codex still pins code mode per model, so the
catalog route is required alongside option B. `thread/resume` restores the same
four tools (captured in two processes sharing one home).

Signed-in persistence check: temporary `CODEX_HOME` holding the user config
with its `mcp_servers` tables stripped, plus the reduced catalog. Auth was
hard-linked from the existing login (no token bytes copied or printed; hashes
compared only). The live ChatGPT turn listed exactly
`functions.read, functions.write, functions.edit, functions.bash`. `model/list`
returned the catalog (sentinel description) before and after the turn. The
catalog file, real `auth.json` and real `models_cache.json` were unchanged, and
the temporary home was deleted. A first run that kept the user's own
`mcp_servers` showed `node_repl`, `openaiDeveloperDocs` and the three resource
tools: a served launch must never load a user config, so the jail binds no
`config.toml` into its `CODEX_HOME`.

Production: `codex_provider._complete_served` launches `codex app-server` with
`codex_launch_contract.SERVED_LAUNCH_ARGS` and the reduced catalog bound
read-only at `/codex-home/model-catalog.json`. It declares the definition's
tools and forwards each `item/tool/call` through `open_engine_tools`, the same
owner-pinned route, session, live turn and signed grant the HTTP loop uses.
Other server requests (approvals, user input) are declined. The image pin
moves 0.153.4 -> 0.160.0 (daemon and oracle). The build gate
`scripts/codex_cli_smoke.py` now runs this exact launch against the pinned CLI
and fails unless the model sees exactly the declared tools. It passed locally
on 0.160.0 (CLI default `gpt-6.1-sol`).

### Provider branches removed

1. Codex served `codex exec` with native tools (`exec`/`apply_patch` code mode,
   collaboration, `request_user_input`, clock) and `web_search="cached"`.
2. Codex's MCP wiring (`_codex_engine_mcp_args`, the engine bearer in the jail
   env, `enabled_tools`); the route is dialled from the platform process.
3. The exec JSONL reader and its helpers (`_stream_codex_exec`,
   `_codex_turn_completed`, `_configured_rollout_model`, the machine branch of
   `_structured_failure_excerpt`). The configured model now comes from the
   thread's own `model`.
4. Codex served nested sandbox (`--sandbox workspace-write`,
   `nested_sandbox=True` permissive seccomp). Codex runs nothing, so it gets the
   jail's full deny profile.
5. Codex concatenating instructions into the user input; they are the thread's
   `baseInstructions`, in the instruction slot every other provider uses.
6. `WorkAgentAdapter.infer`'s `native_agent` activity refusal. The fence is the
   engine route's `ActivityFence` for every provider.

Not removed (see `docs/concerns/2026-10-06-k2-native-and-box-inventories.md`):
Claude CLI's identity line and environment block under OAuth (no switch on
2.1.290: `--bare` and `CLAUDE_CODE_SIMPLE=1` refuse OAuth). The coordinator's
HTTP-only budget line (`agent_turn_coordinator.py`, held by #4524, open).
`render_native_input` stays: it renders history into one native input, which
is translation.

## Revised K2 continuation: shared-contract handoff

Founder direction 2026-10-06: provider adapters translate ONE agent definition;
separate HTTP/Claude/Codex inventories are not the product contract. The audit
found the following divergence, left explicit instead of declaring parity:

- `agent_turn_coordinator.py` sends engine inference a tool schema per round but
  hands native executors an entire turn through `render_native_input`.
- Claude disables native tools but adds native instructions. Codex still adds
  native tools and instructions. Its production launch enables cached web search.
- `definition.PROTOCOLS` includes four HTTP names: `chat_messages` and its alias
  `openai_chat` have an agent codec; `content_blocks` and `anthropic_messages`
  return no agent codec. `_CLI_PROVIDER_CLASSES` registers two native classes.
- `OllamaProvider.complete` uses `/api/generate` with no tools and its base
  `agent_execution_kind` is unset. It is a host-process executor, not currently
  an owner-authorized agent adapter. No distinct command adapter class is
  registered by `call._build_fallback_router` or `provider_for_definition`.
- `agent_loop.tool_session` independently defines box tools plus history/activity
  reads and backend handles. It has no authenticated remote ta transport.

Removed production branches: **none**. No claim of requirement (1) or (2)
completion. A central tuple alone would hide these execution differences.
Background-job parity remains required; wf-orphan owns execution fixes.

Retained and corrected the uncommitted `scripts/native_cli_payload.py` probe.
One metric function counts compact Unicode JSON chars, UTF-8 wire bytes, all
declared tools (including additional native namespaces), and resident chars/4.
The latter is an estimate, never actual model tokens. All rows below use the
same synthetic supplied instructions (`Synthetic stock prompt.`) and user input
(`Reply OK.`), not the production stock persona. Static production prompt budgets
in `test_converse_turn_cost.py` are unchanged.

| Executor / protocol | Resident chars | Chars/4 estimate | Evidence |
|---|---:|---:|---|
| chat_messages | 1,501 | 375.25 | Real installed encoder, synthetic fixture |
| openai_chat | 1,501 | 375.25 | Same encoder alias |
| content_blocks | unavailable | unavailable | No installed agent codec |
| anthropic_messages | unavailable | unavailable | No installed agent codec |
| claude-code | 2,773 | 693.25 | Local CLI capture; four MCP names; native instruction additions |
| codex | 49,713 | 12,428.25 | Local CLI capture; 11 native handles; missing MCP definitions, incomplete |
| ollama-local | unmeasured | unmeasured | Text-only adapter; not represented as a working agent |
| command adapters | not registered | not registered | No separate executable adapter found |

Different HTTP/Claude JSON envelopes contribute syntax bytes; Claude and Codex
also inject instructions and Codex injects tools. The Codex probe's stdio route
is not the production HTTP route, so missing MCP tools are a probe limitation,
not evidence that production lacks those tools. It exits nonzero. Both native
captures explicitly mark production parity unproved. No live model ran.

Reproduce on the development host (use an empty external output directory):

```powershell
python scripts/native_cli_payload.py registered-http --out "$env:TEMP/k2-http.json"
python scripts/native_cli_payload.py claude-code --out "$env:TEMP/k2-claude.json"
python scripts/native_cli_payload.py codex --executable "$env:APPDATA/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe" --out "$env:TEMP/k2-codex.json"
```

The first command exits nonzero for unsupported registered dialects; the final
command exits nonzero for missing/extra tools or overflow. These are diagnostic
commands, NOT the requested paired live task-success/round/token harness.
Live paired trials and that harness remain undone.

Required Claude review via `peer-agents`: **ADAPT**, 103 seconds, read-only,
4 reviewer unit tests passed. Earlier fixes remain; F4/F5 remain blockers.
AGREE probe finding 1: exact tool-set equality plus budget is now required for
the capture check, and production parity is always explicitly unproved.
AGREE probe finding 2: added required MCP startup, enabled-tool selection and
approval-mode settings; prominently retained the stdio-vs-production caveat.
AGREE ancillary findings: exclude token-count endpoints, report every inference
request's metrics, and regenerated the Claude capture. No second review round.

The reviewer identified shared-executor overlap with wf-orphan and stopped as
required by the brief. AGENTS loop rule 7 requires handoff for repeated findings;
the production blockers were handed off rather than vendor-patched. The lead
must reconcile the shared contract with wf-orphan before further runtime edits.

Verification for this diagnostic slice: Linux oracle **19 passed, zero skips**
(6 probe tests plus 13 unchanged turn-cost tests), Ruff passed. The normal mirror
build hit a Windows rename lock without changing tracked files. Supported
`--runtime-root` staging outside the repository passed its import probe (630
files), and all 628 non-cache files compared byte-identical to the tracked mirror.
An oracle snapshot attempt collided with the mirror staging; its tar failure
was not a test pass. The sequential rerun produced the 19-pass result above.
`origin/main` a97c17c26e is merged; PR #4517 remains draft. No deployed-SHA,
real-user acceptance, spec-sync or shipped claim.

Final hygiene against merged `origin/main`: **46 added / 0 removed / 0 tampering**.

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
recorded in docs/concerns/2026-10-06-k2-native-and-box-inventories.md (F4
resolved in K2 round 2 above).

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
