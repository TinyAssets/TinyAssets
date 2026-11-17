# p2-receipt-delete as built (2026-11-17)

P2's third step (`p2-review-gate.md` item 4.3 and 4.4; `tasks.md` §10 "Process first"): the
receipt half of the review gate goes, and the required `review` check is the one review gate.
ADR-038 is accepted, superseding ADR-015's review line.

1. **Order: this merges only after the founder's `PUT` reads back.** Until
   `gh api repos/TinyAssets/TinyAssets/rules/branches/main` shows
   `{"context":"review","integration_id":5634948}`, the receipt in `Diff scope declared` is
   the only review gate GitHub enforces, and this PR deletes it. Read back at 2026-11-17
   00:33 UTC, as this was built: three checks, no `review` yet.
2. **Research: who queues a PR once nothing edits the body.**
   - GitHub's docs (github/docs, `merging-a-pull-request-with-a-merge-queue.md`): "If the
     pull request can't be added to the queue immediately, GitHub enables auto-merge and adds
     the pull request to the queue when requirements are met", and `gh pr merge` "enables
     auto-merge" when the required checks have not passed. So auto-merge armed by a person
     queues the PR by itself when `review`, a required check, turns green.
   - Measured: on the 100 most recently updated merged PRs, every `auto_merge_enabled` and
     `added_to_merge_queue` event is Jonnyton's (a `User`). Auto-merge queued #5656, #5660 and
     #5661 before `enqueue-merge`'s call, which read "already queued" (#5656's went red on it,
     run 43961566455). `enqueue-merge` queued #5649 and #5658 first, by seconds, after their
     last required check passed. Nothing it queued would not have been queued.
   - `enqueue-merge`'s reason was an App re-arming auto-merge as a GraphQL `User`
     (`cursor[bot]` on #4649, 2026-10-12), whose arm the queue never admits. It did not recur
     in those 100 PRs, and such a PR would sit armed and unqueued: a stall, never a bad merge.
   - **Rejected: `enqueue-merge` following `review`'s run.** The `review` run that follows a
     PR review is started by `workflow_run`, which runs on "the last commit on default
     branch" (the events docs). Its `workflow_run` payload names main's head and no PR, so
     `enqueue-merge`'s fallback (`commits/{head}/pulls`) would name the PR that merged main's
     head. Following it needs a channel naming the PR (an artifact from `review.yml`), a third
     link in the chain, for a job auto-merge already does.
   - **Decided: auto-merge alone queues the PR.** `enqueue-merge.yml` and
     `scripts/enqueue_merge.py` go, with its receipt copy and `latest_conclusions`'s ordering
     by completion (`p2-review-required.md` item 6, owed to P6), which nothing reads now.
3. **The drift check moves to auto-enroll, and now stops something.**
   - `enqueue_merge.py` `gate_drift` refused to enqueue while the live rules differed from
     `docs/ops/merge-queue-ruleset.json`. It never stopped the queue (`p2-review-required.md`
     item 3), and its red run sat on main, under `workflow_run`, where no PR shows it.
   - With the receipt gone, auto-enroll arms every ready PR at open, and only the ruleset
     keeps an unreviewed PR from merging. Arming relies on the rules, so the check belongs
     with it: `scripts/ruleset_drift.py`, run before auto-enroll arms. While the rules differ,
     the `Enroll for auto-merge` job fails on the PR, and nothing is armed.
   - Cost: between a ruleset PR's merge and its `PUT`, a PR opened or pushed is not armed, and
     is armed again by its next push or by hand (host-actions' ruleset row says so).
   - **Rejected:** disarming armed PRs on drift (a stall that no event undoes), and a drift
     check in a required check (it would fail every PR in the same window, the ruleset PR's
     own merge group included).
4. **The receipt half, from `land/7`'s `66d29f032` where it applies:**
   - `drain_review_gate.py`: the receipt's parser, its comment inventory, the diff key, the
     carry-head listing and their commands go. It keeps the review check and the Untangle
     check, which `--untangle-check` runs (was `--blocking-review`'s first half).
   - `pr-scope-guard.yml`: the diff-key step, the receipt step and its honesty note go. The
     Untangle check is its own step, which fetches the head itself rather than relying on the
     test-hygiene step's fetch. The release-critical count stays, for the reviewer. The
     "base predates `test_hygiene_gate.py`" skip and a one-caller grep wrapper go.
   - `auto-enroll-merge.yml` arms without a receipt (above). Its full-history checkout goes.
   - Queue freshness and the floor hotfix read `review_passed_at`, the one reader of the
     App's check. The hotfix's operator shell needs `REVIEW_APP_ID`, as every reader does;
     without it the hotfix refuses, naming the variable.
   - The Bash guard's stale-receipt refusal goes with the receipt; the spec's Bash guard
     requirement loses it. The spec's receipt requirement goes, and the hotfix requirement
     reads the `review` check.
   - `required-checks.test.ts` loses `enqueue-merge.yml`'s listener check. Its rule that no
     job is named `review` stays, for `gh pr checks` and people, which read checks by name.
   - Merged with #5666 (`p2-review-writers.md` item 7): its `latest_verdict`, which counts a
     verdict only from an account that can push and fails closed on a permission it cannot
     read, is kept. `_TRUSTED_ASSOCIATIONS` goes with the receipt's comment inventory, its
     last reader, so no merge decision reads `author_association`. #5666's `live:` proof
     passed first: `review` run 44020160475 decided #5667's merge group `dcfd9df870ae` as
     `APPROVE`, with the permission read working.
   - Not applicable: `66d29f032`'s `Lane:` parts (P3 removed `Lane:`), its release-critical
     cap (already gone), and `a6ad432c8`.
5. **Kept:** `tests/test_review_check.py`'s `Drain-Review-Verdict: APPROVE` case, which is not
   a verdict: a landing script still posting the old line must not pass. The decision logs,
   ADR-003's body (ADR-005) and the old OpenSpec changes, which describe the receipt as it
   was; P9 (`untangle/p9-openspec`) deletes the old changes.
6. **Owed:** `drain_review_gate.py` holds the review check and the Untangle check, no drain
   and no receipt, so P6 names it for them (`untangle/p6-process-code`). The host's landing
   script reads that path from main, so it changes with the rename.
