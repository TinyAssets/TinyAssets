# network-answer as built (2026-11-22)

Owed by #5750 (`start-answer.md`): the owner's network re-apply (`launch.ts` `reapply`, an
egress policy write reaching a live lease's sandbox) read the sandbox Worker's answer
unchecked. An answer out of `NetworkApplied`'s shape fell to `networkAnswered`'s `failed`
branch, so it was tried again, but no cause was logged, and a refusal was logged as "did not
reach the sandbox".

## Research (before the build)

1. **What a re-apply does** (`sandbox/src/control.ts` `network`). On the Worker's serial chain
   it reads the lease's sandbox from E2B and replaces its network whole, the live egress token
   and the launched jobs' vendor hosts kept, never with an older network (`newer`). It answers
   `applied`, `at_resume` (paused: kept on the row, applied as it resumes) or `lost` (no sandbox
   of that lease). Its only faults are plain errors (its request check, an E2B error); the
   only refusal it can cross is the runtime's `object_overloaded`, thrown before the call runs.
   It launches nothing.
2. **What the egress design says a failure means** (E2, `untangle-2026-11-remaining-build.md`
   §M item 2; `egress.ts` header; `files.ts` `networkAnswered`). A failure, `lost` or no
   answer clears the send (`network_pending`); the lane sees the lease behind the current generation and sends it again past
   `NETWORK_RETRY_S`. Meanwhile egress is fail-closed: a call carrying the old generation is
   refused `egress_policy_stale`, one carrying the new one `egress_network_stale`, since only
   E2B's transform sets that header. No answer within `LANE_CALL_MS` records nothing, as the
   send may still land.
3. **Retried, or the sandbox stopped?** A start's lost answer stops its job because the job may
   be running unwatched (`startOutcome`). A re-apply has no job: whatever a lost one did, the
   sandbox holds either the old network or the new one, and sending the same plan again is
   safe (a whole replace, never a rollback). The gateway refuses the lease's calls until one
   lands. So a lost re-apply is retried, and nothing is stopped: the rule the code already had.
   What a failure changes is only the log.
4. **The classifier to share.** `startOutcome`'s reading of a call (a returned answer against
   the contract, a refusal the contract reads, one it does not, a plain throw) is not about
   starts; what each means is. A copy for the network would be two tests of one shape.

## As built

1. **One reading of a sandbox call** (`owner/src/sandbox.ts` `sandboxAnswer`): `answered`,
   `refused`, `malformed` with its cause (a refusal's code, or the contract's own fields as
   they came and only the names of the answer's keys) or `unanswered` (a plain throw).
   `startOutcome` is now its start reader (same outcomes, same log lines), and
   `networkOutcome` its re-apply reader.
2. **The re-apply reads it** (`launch.ts` `reapply`): `applied` and `at_resume` as answered;
   `lost`, a refusal, an answer out of shape and no answer all `failed`, each logged as itself;
   late still records nothing.
3. **The answer's states are the contract's** (`contract/src/sandbox.ts` `NETWORK_STATES`,
   which `NetworkApplied` is typed from), as `START_CODES` is for a start: the owner reads the
   list, never a copy of it.

## Found and left

- While re-applies keep failing, the sandbox keeps its old network's direct reach (a host the
  new policy dropped), though the gateway refuses its model calls. E2 leaves that window to
  the lane's retry, bounded by the retry clock and the lease's life; nothing here changes it,
  and a re-apply that cannot land (no sandbox of that lease) has no sandbox to reach out of.
