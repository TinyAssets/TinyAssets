# start-answer as built (2026-11-21)

Owed by the review of #5719 (`pass-owner.md` §6.7, which #5733 ticked half-done): a start's
answer out of the contract's shape was read as no answer, so a sandbox Worker bug read as a lost
start. #5733 (`owner-shape.md`) gave it its own code and log line, but the run still settled
`unknown`.

## Research (before the build)

1. **The protocol** (`contract/src/sandbox.ts` `Started`, `START_CODES`; `sandbox/src/control.ts`
   `start`, `signin`, `#launch`). A start answers `{ started: true }` only after the runner's
   `launch` command ran, its last outside step. Every `{ started: false, code }` returns before
   that command, so by the contract `started: false` means nothing launched. The Worker's two
   refusals (`sandbox_runner_mismatch`, `sandbox_runner_protocol`) also come before it.
2. **What can arrive out of shape.** Both Workers build from one contract but deploy apart, so the
   likeliest case is another version's answer: a start code or a refusal code this version does
   not know. Otherwise it is a bug. In neither case does an answer out of the contract say what
   the Worker launched.
3. **Unknown versus failed** (`runs.ts` header; ADR-034 4.4). `unknown` is a run whose runner was
   lost, or whose start never answered: whether it ran is not known. `settle` treats the two
   states the same way (grant revoked, lease given back, never replayed). Only the receipt's
   `state` differs. An answer that arrived ends the call, and its fault is definite and the
   sandbox Worker's. That is a failure, read by its code, never "no answer".
4. **The sibling classifiers.**
   - The sandbox Worker refuses a runner answering another protocol as `sandbox_runner_protocol`
     (`stateOf`), a terminal refusal the object settles `failed`: a protocol fault one hop down
     is already a failure.
   - #5743's transient refusals go through the same `startOutcome`: sent again within the bound,
     otherwise failed by their code. A sign-in's start (`signinStart`) reads the same outcome
     and ends the same way, its CLI stopped when `started` is null.
   - A refusal of a code this version does not know crossed as a refusal (`name` `Refusal`).
     `refusalOf` returns null for it, so `startOutcome` read it as a plain throw:
     `unknown` `start_lost`, logged "did not answer". It is the same fault as an answer of
     another version's start code, and was misread the same way.
5. **Whether to stop it.** An answer out of the contract says nothing of what the Worker did, so
   the job may be running. A cancel, or a sign-in's stop, answers `cancelled: false` when nothing
   runs, so stopping costs one call and is always safe.

## As built

1. **One classifier, one more state** (`owner/src/sandbox.ts` `StartOutcome`, `startOutcome`).
   `started: null` still means "whether it launched is not known; stop it". It now carries the
   state its start came to: `unknown` for `start_lost` (a plain throw), and `failed` for
   `start_malformed`. An answer, returned or thrown as a refusal, that the contract does not
   read is `start_malformed`. Each is logged with its cause: a returned answer's `started` and
   `code` as they came, plus only the names of its other keys (a bug's answer may hold anything),
   or a refusal's code.
2. **The run settles by that state** (`launch.ts` `launch`). The run settles `failed`
   `start_malformed` and is still cancelled. A sign-in is unchanged: it ends by the code and is
   stopped.
3. **One home for "crossed as a refusal"** (`contract/src/refusal.ts` `namedRefusal`), which
   `refusalOf` and `startOutcome` both read, so the name test is not copied.

## Found and left

- `launch.ts`'s network re-apply reads `got.state` unchecked. An answer out of `NetworkApplied`'s
  shape falls to `networkAnswered`'s `failed` branch, so it is tried again (the right outcome),
  but no cause is logged. It is a different answer, read through no classifier.
- The sandbox Worker's own faults before its `launch` command (a `prepare` or `recover` exit, an
  E2B or upload error, its bad-request check) cross as plain errors. The object reads them as
  `unknown` `start_lost`, though nothing launched. Only the Worker knows it had not reached the
  launch, so the fix is on its side: refuse those faults by a terminal code.

## Owed

- [ ] `owner/src/launch.ts` network re-apply: an answer out of `NetworkApplied`'s shape is
  logged with its cause and read as `failed` (`untangle/network-answer`).
- [ ] `sandbox/src/control.ts` `#launch`: research first whether every fault before the runner's
  `launch` command is refused by a terminal code, so the object settles it `failed` instead of
  `unknown` (`untangle/sandbox-start-fault`).
