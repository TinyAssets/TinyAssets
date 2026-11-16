# p2-review-gate as built (2026-11-16)

P2, "one review gate" (d3 §B6, §F4; `tasks.md` §10 "Process first"), in its order: reviews
posted the way the App's `review` check reads them and seen green on a real PR; then
`review` required; then the receipt half deleted, with the ADR that every PR is reviewed by
one check. This PR builds the first step, the ADR's draft and a fix to the check's writes.

1. **Research: how the check decides, and why it never has.**
   - `review.yml` runs on `pull_request_target` (open, reopen, a new head, an edit) and on
     `workflow_run` of `review-signal.yml` (a PR review) and of `review (merge queue)`. It
     reads the PR's reviews and timeline as the Actions token and posts a check run named
     `review` as the review App (id in `vars.REVIEW_APP_ID`, key in the main-only `review`
     environment). `drain_review_gate.py` `latest_verdict` takes the latest trusted review
     (OWNER, MEMBER, COLLABORATOR) whose first non-blank line is exactly `APPROVE` or
     `BLOCK`, after the last base change and dismissal; `carries` takes it to the head
     across the same change or a Markdown prose delta.
   - **No PR review has ever been posted.** On the 37 PR heads `review.yml` decided between
     2026-11-15 and today, every App run reads "waiting for review" (one "deciding", one
     red, item 3). Each PR's verdict is the receipt alone: an issue comment opening
     `Drain-Review-Verdict: APPROVE`, cited from the body, which the scope guard reads.
     The host's `land_pr.sh` posts exactly that and nothing else. The untangle skill
     already says a reviewer posts its verdict as a PR review on the commit it read.
   - `Drain-Review-Verdict: APPROVE` is not a verdict line for the check
     (`test_what_is_not_a_verdict`), and the receipt comment must open with it
     (`_attests_approval`). So one post cannot serve both while both gates stand.
2. **How a verdict reaches the check.**
   - The reviewer writes one verdict file, as today. At landing, before the body is
     edited, it is posted twice:
     - as a PR review: `POST /repos/{r}/pulls/{n}/reviews` with `commit_id` the commit the
       reviewer read (the receipt's `Drain-Review-Head`), `event` `COMMENT`, and a body of
       the verdict word (`APPROVE` or `BLOCK`) on its own first line, then the receipt;
     - as the receipt comment, as today.
   - `COMMENT`, because the one writer account cannot approve its own PR, and the check
     reads the first line, not the review's state.
   - The review fires `review-signal.yml`, then `review.yml` decides. The body's edit fires
     it again, and the approval carries.
   - A `BLOCK` is posted as a PR review too, which turns the check red. It carries no
     receipt.
   - `land_pr.sh` is host tooling, outside the repo. Its change is two lines after the
     receipt comment: build the review body from the receipt's verdict line, and `POST` it
     with `commit_id` the receipt's head, which the script has already checked is `$HEAD`:

     ```bash
     printf '%s\n\n' "${VERDICT#Drain-Review-Verdict: }" | cat - "$RECEIPT" > "$TMP/review.md"
     gh api $R/pulls/$N/reviews -X POST -f commit_id="$HEAD" -f event=COMMENT -F body=@"$TMP/review.md" >/dev/null
     ```

     The coordinator applies it. In the repo, the rule
     is `docs/reference/executable-gates.md`'s "To land", and the untangle skill already
     says it.
   - **Rejected:** accepting `Drain-Review-Verdict: APPROVE` as a verdict line, or letting
     the receipt cite the PR review. Either is an alias of one verdict for a gate that step
     3 deletes. Posting twice from one file lasts only until then.
3. **The bug: a decision's second write to its own run.**
   - **What was reported:** two `review.yml` runs for one head when a PR opens, the second
     making a new run, so the first's verdict fails with `gh: Not Found (HTTP 404)` and
     "check run … did not take the verdict" (#5656: run 43958158933 failed, 43958163569
     passed).
   - **What the runs show instead.** The two decisions did not overlap. The job's
     concurrency group already runs one decision per PR and kind at a time: 43958158933
     decided from 21:15:28 to 21:15:41, and 43958163569 from 21:15:46. The first run POSTed
     check run 133618390056 ("deciding") at 21:15:36. Its verdict PATCH answered 404 at
     21:15:38.4. A moment later its trap's PATCH of the same run, with the same token,
     succeeded and left it red. The second run then found that run completed and POSTed
     133618555981.
   - The same 404 hit a lone run: 43757055770, #5638's merge group, check run
     132943429788, POSTed at 20:16:02 and refused at 20:16:03, with no other decision on
     that commit.
   - A third case shows a write GitHub accepted and then lost. On #5654's head `50473067`,
     both decisions logged "waiting for review". Only one App run exists, 133562303593, and
     it reads "deciding". The second decision's reopen and verdict PATCHes were both
     answered `in_progress`, and the verdict did not stick. The script checked only status
     and conclusion, which "deciding" and "waiting" share.
   - So GitHub does not reliably apply writes to one check run made seconds apart. It
     refuses some and loses others. Of the 173 `review.yml` runs since 2026-11-15, these two
     are the only failures, and one more passed with its write lost. GitHub's REST docs say
     nothing about it either way.
   - **The fix: each decision writes once, and never to a run that exists.** It decides
     first, then POSTs one new run carrying the verdict. On any exit after the commit is
     known and before that POST, the trap POSTs a failed run, so no earlier pass stands.
     Readers already take the App's highest id for the PR (`review_run_passed`), and the
     code already POSTed a new run over a completed one. Gone: the listing of the App's
     runs, the reopen, the PATCH and its answer check, and the POST's retry with its
     sleeps.
   - **Not cancel-in-progress.** The runs were already serialized, and the failure came
     from a lone run. Cancelling a running decision would leave whatever it had written
     half done, and the newest event already replaces a pending one in the group.
   - **Not "update by name".** The API updates a check run only by id. A new run under the
     same name is the by-name update, and it is this fix.
   - **Cost:**
     - Every decision adds a run on the head. An older in-progress "waiting" run stays
       under the newer ones, and GitHub and every reader take the newest.
     - An earlier pass now stands while a decision runs, about 10 s, where before it
       stood until the "deciding" POST, about 5 s in. Both are inside the signal's own
       latency, which is about a minute. A merge group decides its PR afresh, so a BLOCK
       still stops the queue.
     - A failed verdict POST now fails the decision, through the trap's failed run, where
       it was retried. That is loud and needs a rerun.
4. **The order, each step with its branch or wait.**
   1. Built here. The second half is `live:` the check seen green on a real PR, once a
      coordinator posts a PR review on a PR landed this way. This PR is the first
      candidate.
   2. `untangle/p2-review-required`, after step 1's `live:`. It adds
      `{"context": "review", "integration_id": <the review App's id>}` to
      `docs/ops/merge-queue-ruleset.json`. It changes `required-checks.test.ts`, which
      holds every required check to exactly one job: a check whose `integration_id` is not
      Actions' is posted by an App, so the guard holds it to the one workflow that mints
      that App's token (`review.yml`), on the PR and on the merge group's signal, and
      `enqueue-merge.yml` listens to it.
      - **Founder step:** the ruleset `PUT`, as `docs/host-actions.md`'s ruleset section
        says, right after that PR merges. Until the `PUT`, `enqueue-merge` refuses on the
        drift.
   3. `untangle/p2-receipt-delete`, after the `PUT`. It reuses `land/7`'s `66d29f032`
      where it still applies: the scope guard's receipt block; auto-enroll, enqueue, queue
      freshness and the floor hotfix reading `review_passed_at`; the receipt's wording.
      In `development-coordination-runtime/spec.md` the receipt's requirement goes, and
      the hotfix requirement's floor fixes stop citing a receipt. That requirement sat
      after "Retired capabilities", so `openspec validate` refused the spec on main; the
      review of this PR moved "Retired capabilities" to the file's end, and it validates.
      Its `Lane:` parts do not apply, since P3 removed `Lane:` (d3 §B2), and neither does
      `a6ad432c8`.
   4. With step 3: ADR-038 accepted, and ADR-015's Status line names the supersession of
      its review line. `executable-gates.md`'s "To land" loses its receipt half, and its
      table row becomes "Review: `review.yml`".
5. **`land/7` otherwise:** `b3c0aa6e4` and `d1cfa2f27` (the check, the signals, the App
   binding) are on main. `b968ea14f`'s gates line is replaced by this PR's.
