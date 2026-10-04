## 0. Status tracker (the one place; owner: dots-research, updated 2026-10-02)

The goal is one composed experience in the founder's own account: an always-on
agent (ChatGPT dots) with pi's four tools and customization, Claude Code's
act → verify → iterate feel, and OpenClaw-style configurability. It is judged
by the founder's real conversation, not by tests.

Status words: **live** (merged and deployed), **queued** (stamped, in the merge
queue), **review** (PR open, not stamped), **building** (draft or in progress),
**not started**.

| Slice | What it gives the founder | State | PRs |
|---|---|---|---|
| S1 sessions + tone | One continuing session per thread; result-first voice | live | #4173, #3972 (tool jail) |
| C0/C1 naming | "Command center" everywhere, public MCP names | live | #4189, #4261, #4264 |
| W own workspace | The agent owns `/u` (its workspace), wiki and brain; git + rg | W1, W3 live; **W2 queued** | #4185, #4198, #4194 |
| S3a egress | bash reaches the internet through a checking proxy | live | #4174 |
| S2 steering | A message sent mid-turn reaches the running turn | live; **never-lost fix queued** | #4188, #4290 |
| S4 journal + live line | The owner sees each tool step live; custom UIs read `read_live` | **queued** | #4190 |
| D1 rules, auto-review, hand-backs | Custom Rules (do / if pre-approved / ask first / hand off), seeded hand-backs, auto-review on the agent's own model | live; per-agent keys **queued** | #4193, #4199, #4200, #4228 |
| D2 activities | Several things at once with no chat open; survives deploys; waiting on you | design live; code **building** (store, Activities branch, dispatcher, `/app/live`) | #4211, #4220, #4221 |
| D3 proactive research + proposals | Idle research on a cadence; output is proposals the owner approves | **not started** (trigger owned by S8b) | — |
| D4 onboarding, profile, push | Name + one responsibility; profile page; push on done/waiting | push live; onboarding/profile **not started** | #4138 |
| D5 computer | Browser broker, live view, take over; the agent sees what it built | **building** (ui-capabilities) | #4306 image read, #4316 UI preview, #4314 headless shell |
| D6 four tools + `ta` | Exactly read/write/edit/bash, breadth behind `ta search` | tools live (S1); **building**: D6a additive jail CLI, platform/connection discovery and calls, local extensions implemented on `feat/d6-ta-capabilities`; verification below. Attached MCP interface deferred in design §6 D6a; resident cutover remains | #3972; no D6a PR requested |
| D7 memory | Memory items, Harness tab, history, Undo | **not started** | — |
| D8 roster | Talk to any agent; per-agent rules/visibility | converse-by-agent **building** (agent-chat); design live | #4287, #4227 |
| D9 sharing | Publish / install a whole command center | **building** (cc-package) | #4315 |
| D10 everywhere | Starter template for every new account; old surface deleted | **not started** | — |
| D11 export | A runnable, publish-ready local folder | design live; build **not started** | #4218 |
| S7 thin loop | The Claude-Code-style agent loop the founder's agent should run on | **review** / building | #4282, #4292 |
| S6 broker | The sealed box's request broker | **review** | #4299 |
| S4-box sealed boxes | Each command center in its own box | PR1 auto-merging; PR2 **building** | #4274, #4319 |
| S8a owner lease | Per-command-center owner, fenced turn journal | **building** / review | #4308, #4313 |
| S8b scheduler | One owner tick; triggers; decayed proactive cadence | **building** | #4276 |
| Friction fixes | One-call patch request | **building** | (harness-patch-request, PR to open) |

**D6a verification (2026-10-04, `feat/d6-ta-capabilities`).** Implemented and
locally verified; no PR, deploy or real-user acceptance claimed. Linux oracle:
`python scripts/linux_oracle.py -- tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py tests/test_universe_tools.py tests/test_universe_tools_jail.py tests/test_universe_egress.py tests/test_authenticated_external_call_effector.py tests/test_agent_rules.py -q`
passed **206 tests, zero skips**. Final CLI refusal-exit and credential-reflection
coverage plus route regressions:
`python scripts/linux_oracle.py -- tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py tests/test_engine_mcp_routes.py -q`
passed **69 tests, zero skips**. Oracle temp root is `/tmp/b`, outside the repo.
Windows focused ta/engine tests: **124 passed, 3 skipped**, with basetemp under
the host's external Temp directory. An initial Windows run also hit the existing
drive-letter mount failure in `test_a_provider_launch_view_masks_every_hidden_root_file`;
that test passed on Linux. Changed-source/test Ruff and mirror regeneration
(including the import probe) pass. Repository-wide Ruff has **59 existing
findings in untouched files**; no unrelated lint edits. Attached MCP, resident
cutover and D1 durable workflow provenance remain explicitly deferred in
design §6 D6a. Existing tests were neither removed nor renamed.

**D6a grant binding (2026-10-04, PR #4439 review finding 2).** `ta` is bounded by
the launch's signed tool grant (design §6 D6a). Windows:
`tests/test_ta_capabilities.py` covers the default, narrowed nodes, connections,
forged, absent, cross-launch and cross-server grants. **Pending the hosted Linux
run**: `tests/test_ta_capabilities_jail.py`, whose engine helper now launches on
a signed route and which adds `test_node_grant_bounds_ta_inside_the_jail`; it
was not run for this change. Independent cross-family review is still owed.

**Next integration step.** Once S7 (#4282/#4292), S6 (#4299) and the box tools
land, the founder's account is switched (per account) onto the thin loop, the
four tools and the sealed box. It is judged in his live conversation: does it
act, verify visually, iterate, stay always on, and run long?

**Per-agent controls follow-up (2026-10-03):** #4287/#4228 are reviewed
foundations with accepted D8 control-wiring residuals. The design-only
[addressed-agent-control-provenance](../addressed-agent-control-provenance/proposal.md)
owns that integration and its proof matrix. No complete per-agent controls,
runtime mitigation or live acceptance is claimed by that design. Its first
Claude ADAPT fold retains native D2 and unproved launch-isolation holds, and adds
explicit legacy recurring-work reconfirmation. [Claude design acceptance](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542)
is recorded at 6bf7923597983ec9af61968b99001745a981f2a7; implementation proofs remain pending.

## 1. Design

- [x] 1.1 Research the four reference harnesses and ChatGPT dots from current sources, and audit tiny's code paths, production turns and files (design.md §1-3). The first version was approved 2026-10-01, with the gpt-6-astra ADAPT folded in.
- [x] 1.2 Founder approval of the dots revision (2026-10-01: "modeled after chatgpt's dot agent with always on behaviour, but with the customization of pi.dev and openclaw"), with the command-center naming delta (design §4.11a).
- [ ] 1.3 Archive `universe-harness-four-tools` after syncing its built S1 delta. This change carries its plan forward.

## 2. Slices (each its own PR of at most 12 tasks, live-proven in the founder's app; task lists are in design.md §6; a slice adding a storage shape opens its own storage proposal first)

- [x] 2.1 S1 Sessions, tone, seeded `AGENTS.md` (#4173, merged).
- [ ] 2.2 C0 command-center naming (copy only); substrate: W, the agent's own workspace including wiki and brain (mechanism costed in place of the paused #4175); S3a egress (#4174); S2 owner steering; S4 journal and spill.
- [ ] 2.3 D1 Execution context, Custom Rules, auto-review and hand-back defaults (authority; specified in this change).
- [ ] 2.4 D2 Activities: parallel child sessions, seat release while waiting, deploy recovery, the automation activity target, and a complete Scheduled view (storage proposal first).
- [ ] 2.5 D3 Read-only proactive research by capability, proposals and grantable blocks; D4 onboarding, the profile shell and push.
- [ ] 2.6 D5 The computer: restricted browser broker, a context per activity, live view, Take over / Return control.
- [ ] 2.7 D6 Exactly four tools behind `ta search`/`describe` with versioned read-only handbook references; D7 memory items, Harness tab, history and Undo, then sole-owned learning retirement/backlog migration and N>=10 paired write/recall proof per model-family x stock/customized-AGENTS fixture cell (design D7). Starter policy/content is delegated to `starter-agent-out-of-plumbing`.
- [ ] 2.8 D8 Roster and command center; D9 shareable bundles with owner-activated quarantine; D11 export to a runnable, publish-ready folder (design §4.17); D10 reuses `starter-seed-lifecycle`'s scoped sidecar receipts/upgrades, plus chat-app channel extensions. Depend exclusively on starter-agent-out-of-plumbing task 2.3 for consumer/renderer cutover wiring, old-guidance deletion and all-center/dormant-center proof without owner-review holds.

## 3. Close

- [ ] 3.1 Sync the deltas into `openspec/specs/` and archive this change once D10 is live.
