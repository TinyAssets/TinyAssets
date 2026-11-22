# runner-protocol-contract as built (2026-11-22)

`untangle/runner-protocol-contract`: the contract step of runner protocols 3 and 4's expand and
contract (`runner-protocol-3.md` #5757, `agent-mcp.md` #5754, `runner-protocol-4.md` #5764,
`agent-hooks.md` #5765). Main's Worker spoke 3 and 4 beside its protocol 4 runner.

## Research first (Fact 14), read 2026-11-22 on main 48eaf29be

What a Worker of this tree could still meet that speaks or holds an older protocol:

- **What only 3 needed is already gone.** Protocol 3 is 4 less the start document's `hooks`
  and the internal `hooks` command (`protocol.json`). A protocol 3 runner ignores `hooks`, so
  the Worker writes nothing only 3 reads. `region`, the field kept for runners speaking 2,
  went in `agent-hooks.md` item 9. In `src/` and `runner/`, what is left of 3 is `SPEAKS`
  and two comments. Of protocol 1, the guard's `RETIRED_IN_1` is left. It can no longer fail:
  the next line holds the runner's commands to protocol 4's frozen record, which names none
  of them (`untangle-2026-11-wave-2.md`: "drop `RETIRED_IN_1` at the next bump", three bumps
  ago).
- **Persisted shapes.** The control record's rows keep a runner's digest, never its protocol
  or a document. The owner's rows keep the owner contract's `SandboxStart`, which
  `startDocument` maps at each launch. An inbox document is written just before its `launch`,
  at a path the next launch of that id overwrites. A job's `start.json` is read later only by
  the runner that launched it (`recover`, `cancel`), and by a newer runner only for a live
  run's `agent` and `engine`, which every protocol has. A newer runner is adopted only when
  nothing lives. So the runner has no dual-read to drop. `start.hooks[event] ?? []` is
  protocol 4's "an event absent holds none", not a read of 3.
- **Where an older runner is met after a deploy.** The runner ships in the Worker by digest
  (#5428), so a deploy replaces the runner of every new sandbox and of every sandbox adopted
  later. An older one stays only in a sandbox that outlived the deploy, paused or running, on
  `/opt/ta/runner/<digest>/`, under its row's digest. The loader runs any digest still
  installed. The Worker meets that runner on these paths:
  1. A start finding it running, or resumed with its memory (`#adopt`). The Worker asked
     `state` and refused a runner it does not speak **before** reading `live`. So a sandbox
     nothing lived in, holding a runner two deploys old (paused across two deploys), was
     refused `sandbox_runner_protocol` at every start, forever, though adoption needs only its
     `state`. The design says "adopts the Worker's at a start that finds nothing living", and
     the rolled-back test assumed the same.
  2. A start resuming a **rebooted** sandbox (`pauseMode: "filesystem"`), or one paused
     **mid-renewal**: no adoption, so no `state`. `prepare`, `recover` or `renewed`, then
     `signin-check`, the document and `launch` went to the older runner, its protocol never
     asked. After this contract, a protocol 3 runner would take a protocol 4 start and drop
     its `hooks` without a word: the misread Fact 8 forbids.
  3. The look (`#runnerState`) and the release's reap refuse an unspoken runner already.
  4. `cancel`, `login-input`, `login-drop` and `login-cancel` stop or feed a job that a
     Worker speaking its runner's protocol launched. They carry no document.
- **Live, today.** Nothing holds a protocol 3 runner with work on it. Routes are not switched,
  no owner has run anything, and the doors do not serve `/mcp`, so no owner sandbox exists.
  The deploy's pair check runs throwaway sandboxes that no control row names. Three dark
  redeploys since #5765 (the latest run 44998458165, on 48eaf29be) shipped the protocol 4
  runner. So dropping 3 now refuses no one.

## What landed

1. **The Worker speaks 4 alone** (`SPEAKS`). Protocol 3's record and its pin stay, frozen.
   Every record, 1 to 4, now has a use: it shows what an older runner answers to `state`
   (item 3).
2. **A start makes its sandbox's runner one the Worker speaks to before sending it anything
   else** (`control.ts` `#speaking`, which replaces `#adopt`'s check). A runner not the
   Worker's own is asked `state`. Where nothing lives in it and it can be adopted (running,
   or resumed with its memory and no renewal pending), the Worker's own is adopted, whatever
   the old runner speaks. Any runner it keeps (one a run lives on, a rebooted sandbox's, a
   renewal's, or one whose adoption failed) must speak a protocol in `SPEAKS`. If it does
   not, the start is refused `sandbox_runner_protocol`, by name, and nothing more is sent to
   it. A start's resume runs this check as well (`#resume`'s `start`, formerly `adopt`, now
   passed on the renewal branch too). A `state` that fails is a fault, thrown: the Worker can
   no longer go on, unchecked, with a runner that could not say its protocol. An adoption's
   own failure still keeps the runner (`sandbox_runner_adopt_failed`), which is then held
   to `SPEAKS`.
3. **`state` is the one answer that never moves** (`protocol.ts`, `state-format.ts`): the
   guard (`state-format.test.ts`) holds that every recorded protocol has the `state` command,
   the same answer, and the same loader path and the exit codes the Worker reads around it.
   That is what lets item 2 read `live` from a runner it does not speak.
4. **`RETIRED_IN_1` is gone**, with its guard line.
5. **The tests** (`sandbox.test.ts`): it adopts over a runner nothing lives on that speaks
   protocol 3 or 9. A rebooted sandbox's runner, and a renewal's, carry the start where the
   Worker speaks their protocol (state, then prepare, recover or renewed, then the launch, all
   on the old digest). Where it does not, the start is refused by name after `state` alone.

## Left, with why

- **`cancel` and the sign-in's `login-*` commands are not checked.** Each stops or feeds a job
  that a Worker speaking its runner's protocol launched. Each command is in every record, 1
  to 4, with no change. None carries a document. Refusing one would only keep a CLI
  running to the ceiling. Such a job can exist only where a run lives across two deploys, and
  the look runs that sandbox to the end of its window and kills it there
  (`foreign-runner-reap.md`, which corrects item 3 of the research: the look's refusal stopped
  the look for the owner's other sandboxes too).
- **`SPEAKS = [4]` is a deploy-order decision, not a behaviour a test pins.** The Worker's
  tests are parametric on `SPEAKS`, and the guard bounds it to protocols one either side
  of `PROTOCOL`. Putting `[3, 4]` back passes, as the rule allows. A test pinning `[4]` would
  repeat the constant.

## Review

- **A sandbox a start's resume refused was left running, unseen.** `#resume` marked its row
  running (`paused = 0`, the meter's `resume`) only at its end, so a refusal from `#speaking`
  (or any fault after E2B's resume) left a running sandbox whose row said paused: `#arm`
  skips a paused row with no launched job, so no look came, and it ran billed to E2B's own
  timeout at the runtime ceiling, the gap `sandbox-start-fault.md` item 4 closed for the
  other paths. The row now says it runs, and the meter logs the resume, as soon as E2B has
  resumed it. The look then takes it: a speaking runner's idle sandbox is reaped, and one
  that speaks another protocol is refused by name at each look, as on the running path
  (the rebooted refusal test reads the row and the alarm).
