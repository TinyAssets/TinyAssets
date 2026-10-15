## 1. The loop (PR 1)

- [x] 1.1 Thin turns run where turns run today; the shared loop is 2.1
      (`design.md` decision 1).
- [x] 1.2 Box tools by `op_id` over a handle bound at turn start; lost replies
      asked with the same `op_id`; unknown holds; ordered writes; bounded,
      owned cancel/timeout kill in the box.
- [x] 1.3 Owner reads `history`/`activity` in the loop, read-only, never
      forwarded to the box.
- [x] 1.4 Tool router + coordinator hook (`open_tools`, `op_id`); journal
      unchanged; the temporary `TINYASSETS_AGENT_LOOP=thin` switch was replaced
      by admitted box binding selection.
- [x] 1.5 Memory per waiting turn: 500 concurrent against a mock SSE server
      (`design.md` § Measurement).

## 2. Production path (PR 2, after S4's driver, S6's streaming contract, S8a)

Checkpoint 2026-10-14: `codex/c7-c9-session-convergence` composes C7 #4845
at `c1eb3d44` and C6 #4920 at `bc418c32`. Session admission now binds before
agent-file loading, pins a coherent file generation, explicitly carries the
handle into child/executor parents, and preserves the durable send/run identity.
Thin tools use that handle and the BoxProvider exception contract. The native
caller selects BoxLauncher using its existing usage/attempt ID; the package
runner's host-path assumptions and metadata discovery still need C6 composition.
None of these notes claims the owner-box layout is active or live acceptance.
Jonathan reprioritized Roundtable; finish the active tested checkpoint, then
resume the remaining cutover only under parent coordination.

- [ ] 2.1 One shared loop: task-aware, non-blocking provider-assignment
      admission; journal writes off the loop; then turns as tasks on it.
- [ ] 2.2 Configure the `BoxProvider` (S4 local driver) in the daemon; adopt
      its module types in place of `BoxExec`; its `stream` honours an I/O
      deadline and its image ships `flock` (design Risks).
- [ ] 2.3 Model stream to the app: shared broker streaming (C9) -> loop -> the
      frontend's SSE response, cancellation propagated both ways.
- [ ] 2.4 Owner generation on journal writes through S8a's lease interface.
- [ ] 2.5 Retire the per-turn provider jail and the engine route's four tools
      for HTTP turns on the thin loop; keep both for CLI paths.
- [ ] 2.6 CLI-in-box only for command adapters and file-OAuth CLIs; Claude
      subscription serving stays owner-only (D6).
- [ ] 2.7 Live proof: a rendered conversation through the live app with an
      admitted box binding, plus `mcp_public_canary.py`.
- [ ] 2.8 Spec sync to `openspec/specs/control-plane-agent-loop/` and archive.
