# sandbox-start-fault as built (2026-11-22)

Owed by #5750 (`start-answer.md`, "Found and left"): the sandbox Worker's own faults before a
job's launch crossed to the owner's object as plain errors, which it reads as `unknown`
`start_lost` (and stops the job in case it launched), though nothing launched.

## Research (before the build)

1. **What launches a job** (`sandbox/runner/runner.ts` `launchJob`). The runner's `launch` (a
   run's) or `login-launch` (a sign-in's) takes the document from the inbox and spawns the
   job's detached runner, then writes its state: the job may run from the moment that command
   is sent. Everything the Worker does before it (`sandbox/src/control.ts` `start`, `signin`,
   `#launch`) touches the sandbox but starts no job: the request check, the control record's
   read, `#ensure` (E2B `info`, a network update, `#adopt`, the line's `turn` and `wait`, E2B's
   `running` count, a resume with its `prepare` and `recover` or `renewed`, a create with its
   `info`, the runner's install and `prepare`), the lease file's upload, a joining job's
   network widened, `ready` (a run's `signin-check`) and the inbox upload.
2. **Every point an error comes back, and what the owner was told.**
   - The request check, `startDocument` on a malformed start, and any E2B or envd fault in
     the steps above (an E2B 5xx, a runner command answering a fault exit, envd's 502, a full
     disk's 507, a `liveNetwork` out of shape): a plain `Error`, read as `unanswered`, so the
     run settled `unknown` `start_lost` and was cancelled. **Before the launch: nothing
     started.** This is the bug.
   - The loader's mismatch (`sandbox_runner_mismatch`) and a runner of another protocol
     (`sandbox_runner_protocol`): refusals, read as `failed` by their code. Also before it.
   - The runtime's overload of a stub (`SLOTS`' `turn`, `wait`), thrown before that call ran:
     `refusalOf` reads it `object_overloaded`, transient, so the owner sends the start again
     within its bound (`start-transient.md`). Before the launch too.
   - A `sandbox_waiting`, `sandbox_lost`, `sandbox_unconfigured` or `signin_required` answer:
     the contract's start codes, all before the launch.
   - The launch command's own exec: envd losing the reply (the job may run), its process never
     started (envd's 502 says the same for both: the Worker cannot tell them apart), a fault
     exit after the spawn (the state write failing with the job running) or the command's
     deadline. **At or after the launch: whether it runs is not known.** The `launched` rows
     and `#arm` after it fail only with the job running.
   - Outside the object: the entrypoint (`index.ts`; `principal` on the owner the binding
     hands, always the object's own name), the service binding and the Durable Object stub
     (a reset, a lost connection, an exceeded timeout). None of them can say how far the
     object got, so they stay plain errors: `unknown`.
3. **How the protocol already tells the two apart** (`owner/src/sandbox.ts` `startOutcome`,
   `start-answer.md`). A refusal, by a code the contract reads, means nothing started: the run
   settles `failed` by its code, is not cancelled, and a sign-in ends without its stop. A plain
   throw is `unknown` and stopped. So the owner needs no change: the Worker has to refuse what
   it knows did not launch, and only the Worker knows how far it got.
4. **The shape.** One boundary in the one place every job's start passes (`#launch`): every
   fault from the request check to the launch command that is not already a refusal is logged
   there with its cause and refused by one terminal code, `sandbox_start_failed`; a refusal
   (the runner's two alerts, the runtime's overload) keeps its own code; a fault from the
   launch command on stays a plain error. Terminal, not transient: a fault says nothing of
   whether the same start succeeds later, and the owner may start the run again. Its message
   is fixed text: the cause (E2B's answer, a runner's stderr) is the Worker's log's, and never
   crosses. Checked against the intent (Fact 8): the fault is logged loudly where it happened,
   and the owner records what is known (`failed`, nothing started) instead of a doubt it
   resolves by a needless cancel.
5. **The wire.** No change to `Started` or to the runner protocol: the refusal crosses as every
   refusal does. A new code is an expand by itself. An owner of the version before reads a
   refusal of a code it does not know as `start_malformed`, `failed` and stopped, already
   better than `unknown`; an owner of this version never sees it from an older sandbox Worker.
   No protocol bump.

## As built

1. **The rule is the contract's** (`contract/src/sandbox.ts` `SandboxService.start`): a
   refusal means the job did not launch; a plain error is a loss or a fault at or after its
   launch. `sandbox_start_failed` is a terminal code (`refusal.ts`, 502 as the Worker's other
   two).
2. **One boundary** (`sandbox/src/control.ts` `#launch`, which `start` and `signin` now hand
   their check and their job): `launching` is set as the launch command is sent, and the one
   catch refuses every non-refusal fault before it.
3. **The owner's readers say it** (`owner/src/sandbox.ts`): `startOutcome` names the code
   among the refusals that come only before a launch, and `sandboxAnswer`'s `unanswered` is a
   start's fault at or after its launch, or a re-apply's.
4. **Review: the look armed on every path** (`#launch`). A start that made or resumed a
   sandbox and failed before its lease reached the row (a fault at the create's `prepare`, a
   wait or refusal after a resume) armed no look, and the owner's release finds a sandbox
   only by its lease, so the sandbox ran, billed, to E2B's own timeout at the runtime
   ceiling. It predates the PR (an `unknown` start's cancel stops only a launched job), but it
   is what a refused start leaves. The serial body now arms in a `finally`, so the look reaps
   it idle like any other (the faults-before-launch test reads the alarm).
