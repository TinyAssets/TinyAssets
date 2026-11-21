# dry-run-required as built (2026-11-21)

`process/dry-run-required`: two process fixes. The cutover's dry run now gates merges. Auto-enroll now honours a person's pause. No ruleset changes: `required-tests` was already the required context. Research came first (Fact 14): the job timings, the queue's runner waits, the scope a rule could skip, and the auto-merge events of the last 80 PRs, all from the API on 2026-11-21.

1. **The dry run is part of `required-tests`.**
   - It was its own workflow (`cutover-dry-run.yml`) and was never required. #5722 broke the post-cutover tree and nearly merged; someone caught it by hand.
   - Its three jobs moved unchanged into `tests.yml` as `cutover-dry-run-legs`, `cutover-dry-run` (the matrix of `platform-tests`' legs on the applied tree) and `cutover-dry-run-checks` (the typecheck and the process leg there).
   - `required-tests` needs the matrix job and the checks job. One step fails unless both succeeded. A failed `cutover-dry-run-legs` skips the matrix, and a skip fails that step.
   - `ci/guards/cutover.test.ts` holds this wiring, with six mutations. It is the manifest's guard and goes with the dry run.
   - The cutover deletes `ci/dry-run.ts`, so `tests.yml`'s cutover edit now names the three jobs, their `needs` and the gate step.
   - The workflow's delete entry left the manifest along with the file. The manifest's `about`, `kit/cutover.ts`, `dry-run.ts` and `tasks.md` §10 step 5 now point at `tests.yml`. `b24-dry-run.md` item 2's "not required" is corrected in place.
2. **It runs on every event but the merge group.**
   - In the queue, runners are the bottleneck. Six merge-group runs of 42 jobs each (44901260453 to 44902114587) had jobs waiting up to 410 s for a runner. Adding 15 more jobs to every queue entry would slow every merge.
   - A PR can break the tree only through its own diff, and its PR run checks that diff. The queue already gets its whole-surface Python run (`required-tests-shard`); `affected-tests` is the same shape the other way round, on the PR only.
   - Two PRs that are each green alone could still break the tree together. That is rare, and the hourly run on `main` catches it, raising `main-red`. That run replaces the old nightly one.
   - The residual risk, plainly: a PR's run reads its diff merged onto `main` as of its last push, and the queue reruns none of the dry run, so a PR whose run passed before `main` moved merges unchecked against what moved. The hourly run is the only net, and its slots are best-effort (`tests.yml`'s `schedule` comment), so such a break is seen after the merge, not before (review of #5731).
3. **No diff scope.**
   - The legs it reruns (`platform-tests`, the typecheck, `process`) run on every event, and the dry run follows the same rule.
   - A path rule would skip very little. Of the last 199 merges on `main`, 9 touched only `.md` files and 3 only paths the manifest deletes (13 counting both).
   - A `.md` change can still break the tree: the process leg reads docs that stay.
4. **Cost, measured.**
   - **The old workflow, ten PR runs (44888783406 to 44904009707):**
     - the legs job took 14 to 23 s;
     - the slowest leg took 199 to 344 s (one outlier took 1069 s, in run 44894003926);
     - the checks job took 85 to 107 s;
     - the next job picked up the matrix 2 to 122 s after the legs job finished;
     - the whole run took 231 to 590 s of wall time (the outlier run, 1095 s).
   - **Tests on six of those heads:** `required-tests` started 380 to 1200 s in. The dry run had finished before that in all six: 231 s against 380, 252 against 782, 386 against 910, 422 against 487, 462 against 702 and 523 against 1200.
   - **What this adds to a PR's critical path:** nothing on those runs. The legs run in parallel with everything else. The PR already ran these jobs as a separate workflow, so it asks for no more runners than before.
   - **Where it could become the last job:** its path is the legs hop plus its slowest leg. That is about `platform-tests`' slowest leg plus 15 s and one runner pickup. It falls within the recorders' 6 minutes.
   - **Watchdogs:** each `timeout-minutes` (10, 45, 20) stays, all more than 6 times the measured time.
   - **Left:** a static matrix would save the hop, but it would need a copy of `platform-tests`' legs, or YAML anchors and a skipped leg for the import workspace. Neither is worth 15 s off a path that is not critical.
5. **Auto-enroll honours a pause.**
   - **The bug, on #5709:** the coordinator disabled auto-merge at 08:07:50 and again at 08:16:39. Each push re-armed it within seconds (08:16:18, 08:34:07). In both cases, auto-enroll's `synchronize` run found the PR unarmed and armed it again.
   - **Why the actor alone cannot tell the two apart:** enrollment arms with `MERGE_ATTRIBUTION_TOKEN`, which is a user's PAT, so its enables are a `User`'s, just like the coordinator's. Its two disarms (the queue cap, and replacing an auto-merge a bot armed) were a `User`'s too.
   - **What the last 80 PRs show:**
     - a removal from the queue records no `auto_merge_disabled` event (none follows any of the 86 removals);
     - `queue-handoff.yml`'s disarm (`queue_attempts.py`) already runs as `github-actions[bot]`.
   - **The one rule:** auto-merge stays off while the PR's newest `auto_merge_enabled` or `auto_merge_disabled` event is a disable by a `User`.
     - Every disarm the workflows make is now the bot's: auto-enroll's two disarms run with `github.token`. So a `User`'s disable is always a person's.
     - A person re-arms by enabling auto-merge, which makes the newest event an enable.
     - A fix pushed after a queue failure is armed as before.
   - A label was weighed. It would need the coordinator to do a second thing on top of the disable it already does, and a disable with no label would still be re-armed.
   - **The rule fails closed:** an unknown `User` disable keeps a PR off until a person acts, and a failed timeline read fails the step.
   - **Shown before pushing:** the step's own script ran against a fake `gh` for five timelines (paused, removed from the queue, never armed, disarmed by the bot, re-armed by a person). Only the paused one stays unarmed. With the condition mutated, it arms. `main`'s copy arms it too, which reproduces #5709.
6. **Owed:**
   - [ ] live: the next PR a person pauses stays off across its next push (auto-enroll's summary, "Not enrolled: a person disabled auto-merge"), on `process/dry-run-required` once merged.
