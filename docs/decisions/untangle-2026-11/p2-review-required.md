# p2-review-required as built (2026-11-16)

P2's second step (`p2-review-gate.md` item 4.2; `tasks.md` §10 "Process first"): `review`
becomes a required check of the merge queue's ruleset, posted by the review App.

1. **Research: step 1's `live:` holds, on the PR and on the merge group.** Read-only, from
   the check runs GitHub holds:
   - #5658's head `ce9882f3` has a PR review by Jonnyton (`COMMENTED`, first line `APPROVE`)
     and three `review` runs by `tinyassets-review` (App 5634948), each `success`, "APPROVE:
     reviewed at this head".
   - Its merge group commit `54668a171`, which is now on main, has a `review` run by the same
     App, `external_id` `queue-pr-5658`, `success`, posted at 22:40:40, 30 s after the group's
     other checks started and well inside the queue's 35 minutes.
   - #5659's group `bf241ddbf`, built on `54668a171` (`pr-5659-54668a17…`), is the same:
     `queue-pr-5659`, `success`. With `ALLGREEN` grouping each entry's commit is checked, and
     each group decides only its own PR, so a group of several PRs passes when each PR's head
     was approved.
   - The groups of #5654, #5655 and #5657, whose heads had no PR review, read `failure`
     ("has not passed review: waiting for review"). Once `review` is required, a PR landed
     without its PR review cannot merge, which is the point.
   - The live rules (`GET rules/branches/main`) equal the file's before this change.
2. **Who posts a check says which workflow reports it.** `#5655`'s guard held every required
   check to exactly one job. That is GitHub Actions' rule (`integration_id` 15368): a job's
   check run takes the job's name, on the commit its workflow ran for. An App's run takes the
   name its code picks, on the commit its code names. So `required-checks.test.ts` reads the
   `integration_id` and finds one reporter for each check:
   - Actions: exactly one job of that name, started by its workflow's own triggers. A
     workflow started by `workflow_run` runs on main, so its job never reports on the PR.
   - Any other App: exactly one workflow of that name (`review.yml`, `name: review`), started
     by its own triggers and those of the workflows it runs after (`review (merge queue)` on
     `merge_group`), and no job of that name anywhere. Actions would post such a job as a
     lookalike that `enqueue_merge.py`, reading checks by name, could take for the check.
   - The rest is the one predicate it was: the reporter is started by a pull request and the
     merge group with no path filter, and `enqueue-merge.yml` listens to exactly the
     reporters' workflows. So it now also runs after `review`.
   - The guard can't tie the App's id to `review.yml` (the id is the repository variable
     `REVIEW_APP_ID`); the workflow's name is the tie, and `tests/test_review_check.py` pins
     that `review.yml` mints the App's token in the `review` environment.
   - **Rejected:** a list of App-posted check names in the guard, a copy of the file's
     knowledge; and holding the App check to "the workflow that mints an App token", which
     another App's workflow would also match.
3. **The order: merge, then the founder's `PUT`, and the drift check unchanged.**
   - The drift check (`enqueue_merge.py` `gate_drift`) stops only `enqueue-merge`, not the
     queue. A PR that auto-enroll arms with the user's token enters the queue on its own:
     #5656 was added at 21:35:23 while `enqueue-merge`'s call a second later was refused with
     "already in the queue". So in the window between this merge and the `PUT`, merges go on;
     `enqueue-merge` refuses, loudly, which is what it is for (`required-checks.md` item 2).
   - The `PUT` applies main's file, as host-actions says for every ruleset change, so the file
     is reviewed and merged before GitHub enforces it.
   - **Rejected:** the `PUT` before the merge (live ahead of the file, the same window the
     other way round, and the lane waiting on the founder); and a drift check that lets the
     file run ahead of live, or tolerates one check, which is a special case and would hide a
     `PUT` that was never made.
4. **Found while checking `enqueue_merge.py`:** GitHub's refusal of an enqueue for a PR
   already queued reads "Pull request is already in the queue", which `_benign_enqueue_error`
   did not match, so #5656's run went red. It now matches.
5. **Consolidated:** `tests/test_review_check.py`'s asserts that no job in `review.yml` or
   `review-queue.yml` is named `review` (and `review-signal.yml`'s), now held by the guard
   for every workflow.
6. **Left:**
   - `live:` the founder's `PUT` (host-actions, "Pin the context").
   - P2 step 3 (`untangle/p2-receipt-delete`): without the receipt, no body edit follows the
     PR review, and `enqueue-merge` skips `review`'s `workflow_run` runs. That PR decides
     whether it follows them or leaves queueing to auto-merge (`tasks.md`).
   - `enqueue_merge.py` `latest_conclusions` orders a check's runs by completion, so if the
     rollup lists an older pass beside a newer `review` still in progress (a dismissal, say),
     it reads the pass; GitHub and `review_run_passed` take the newest run. That costs one
     enqueue GitHub refuses, since GitHub enforces the required check itself (P6's line).
