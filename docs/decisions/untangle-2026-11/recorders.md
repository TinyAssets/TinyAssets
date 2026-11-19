# recorders as built (2026-11-15)

`untangle/recorders` moves the four recorders a refactor is checked with into the repo. Before, they lived in one session's scratch area, and each run copied them into a tree and patched their imports with `sed`, so no other engine or session could run them.

1. **They are `platform/{doors,owner}/test/record/*.record.ts`, run by `npm run record -- <dir>` and compared by `npm run record:diff -- <base> <head>` (`ci/record.ts`).**
   - They are not `*.test.ts`, so no project's `include` names them, and no normal leg, sizes leg or `npm test` runs them. They are heavy, and CI is the one full run.
   - They sit in `test/`, so the layer table holds them as tests (`ci/guards/isolation.test.ts`), and the waits guard reads them.
   - Each workspace's `test/record/record.config.ts` is its own config's workerd project (`ci/project.ts` `recording`), with its pool, size class and setup files unchanged. Only the files it runs differ.
   - The runner runs each recorder in its own workspace, in turn, keeping its prefix's lines (`REC`, `TOOLS`, `RPC`, `RV`). It writes them sorted, with every `"next"` cursor one placeholder, to `<dir>/<name>.txt`. Anything else the run printed goes to `<name>.log`. A failed run fails the command after its lines are written (Fact 8).
   - The rules are the same for all four. Before, only two of them normalised cursors, and the order came from the host's `sort` locale. No wire or `tools/list` line holds a cursor, so the normalisation leaves those lines as they were.
2. **Their imports are the layer table's.**
   - The doors' recorders define the owner's agent through the owner's RPC file doors (`doors/test/record/support.ts` `owned`). They no longer import `owner/test/runs-support.ts`, which the table refuses and which B23 owes only for older tests.
   - Types and values come from the contract: `Binding`, `PLATFORM_CALLER`, `apiKeyOrigin`, `SandboxStart`, `toolTable`, `slotBusy`, `egressTokenOf`, `generationOf`.
   - The reviewer's recorder reads `contract/doors.json` itself, not a copy dropped beside it.
   - The probes for code merged long ago are gone. Those were the wire recorder's `BUSY_ONCE`/`REFUSE_ONCE`/`SlotBusy` checks and the service-binding test's `typeof refusalOf` and `[branch]`/`[main]` label. Its four `svc` lines lose the ` [branch]` suffix, the one wording change.
3. **Found while moving them: the wire recorder had not seen the sandbox's doors since #5533.**
   - #5533 (2026-11-12) took `generation` off `NetworkPlan`. The scratch copy still sent `String(start.network.generation)`, which is `"undefined"`, and the `sed` patches never touched it.
   - So every sync, run-events and model-door line (34 of 804) recorded `egress_policy_stale`, not the door's answer.
   - The generation now comes from the contract's `generationOf`, the same reader the doors' own tests use. `tsc` checks the recorders now, and it refused the old field.
4. **Determinism, fixed in the recorders, not the product.**
   - **`ownerExport`'s `pending`.** The door's contract says a page whose object is not yet written answers `pending` with its cursor unchanged, to be asked again (`owner-do.ts`, `ownerExport`). Whether the generation's pass ran before the call is timing, not behaviour. Both owner recorders now ask through `asked` (`owner/test/record/support.ts`). It asks the page again by its cursor until the object is written (a liveness bound only), and prints that answer. This is a caller's own documented loop, not a hidden product race.
   - **The wire recorder's `inbound replay`**, a third case, found by this PR's third run of main. That line answered `202 deduped` in two runs and `409 event_conflict` in one.
     - An inbound event's identity is its `Idempotency-Key` and its `X-Event-Timestamp` (`doors/src/inbound.ts`).
     - The recorder stamped each request with `now()`, so whenever a second turned over between the event and its replay, the "replay" was a different event under the same key. The 409 was then the correct answer.
     - The test now takes one stamp and sends it on every event. A real replay carries the same stamp, so the product is right.
   - **The order of `outsideAgents` rows.** `agentsPage` (`owner/src/pages.ts`) answers each list in its stable key's order: `agent_id` for agents, a unique key, so the order is total and a caller always sees the same one. But an outside agent's id is minted at random, and the recorder's `clean` erases it, so the printed order flipped between runs. The reviewer's recorder now prints an answer's `agents` in the order of its cleaned rows (`shown`), which is a total order of what a line shows. An answer out of `agent_id` order fails its line first, so the sort hides no breach of the order the door promises. Nothing in the product is unordered. Every other list prints as answered.
5. **Left as it is.**
   - Each recorder keeps its own `clean`. The doors' two files share one, but the builder's and the reviewer's differ in two patterns. No line of one recorder is ever compared with another's, and making them one would rewrite every line for nothing.
   - The reviewer's admission state `pending` is still set by SQL. No door makes an admitted owner, with its agents and runs, pending again.

## November 19 verification

The combined rebuild snapshot `ce85dfe5e` was compared with `f934871ad`. All 804 wire records, 13,622 Owner RPC records and 73,945 reviewer records matched byte for byte. The RPC comparison covered 278 Worker cases and the reviewer comparison covered 279. The seven tool-discovery profiles contained only the expected description edits. The app profile matched exactly.

A separate regression in the recorder CLI could truncate a large comparison when its output went through a pipe. On this branch the CLI wrote all 315,783 expected bytes through a pipe and through npm on both Node 22 and 24. The regression test lives in `platform/ci/guards/record-output.test.ts`. Re-run the behavior comparison when the combined runtime changes.
