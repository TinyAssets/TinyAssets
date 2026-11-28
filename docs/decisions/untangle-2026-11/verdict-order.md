# verdict-order as built (2026-11-28)

A live bug in the required `review` check (Fact 17), found by #5856's reviews (I-4 of the
review of 46bebfb2c; I-1 and Nit 1 of the review of f83cc0f15), fixed in its own PR before
`process/ship-rules`. The rule is #5856's § Target pipeline step 3 (at cb858fde6), with one
departure (item 4).

1. **The bug.** `latest_verdict` took the writer verdict with the newest `submitted_at`,
   whatever commit it read, and `carries` rule 1 lets a verdict on one commit stand on
   another with the same change. Reviews of two heads overlap: reviewer A reads H1, the
   steward pushes H2, reviewer B reads H2, and whoever posts last wins.
   - `BLOCK@H2` t1, then a late `APPROVE@H1` t2, H2 a clean merge of main: the APPROVE was
     the latest and carried, so `review` passed with H2's finding unfixed.
   - The naive fix, the latest verdict on the most recently pushed commit, opens the mirror:
     `APPROVE@H2` t1, then a late `BLOCK@H1` t2 whose finding is still in H2, passes. Today's
     code fails that one; the naive fix would not.
2. **Research: what orders two heads of a PR.** The rule needs "the same commit or a later
   one", so a commit order.
   - Commit dates: set by the committer's clock, and a rebase keeps the author date. Not an
     order of pushes. Rejected.
   - `GET pulls/{n}/commits`: the commits now in the PR, oldest first, capped at 250, with
     force-pushed-away commits gone. Not push order, and a read `review.yml` does not make.
     Rejected.
   - The issue timeline `review.yml` already reads: a `committed` event carries the commit's
     dates, not a push time, and a plain push has no event of its own; only a force-push is
     marked (`head_ref_force_pushed`). Rejected.
   - The review App's own `review` runs, one posted per head on `synchronize`: the first
     run's time on a head is about when it was pushed. But the merge-group path was built
     never to read the PR's own runs (`p2-review-gate.md`), and a head whose decision never
     posted would have no time. Rejected.
   - Git ancestry, `git merge-base --is-ancestor older newer`, on the objects `review.yml`
     already fetches (`fetch-depth: 0`, the head, and every review's `commit_id`). Measured
     on git 2.43: 0 when `older` is `newer` or its ancestor, 1 when not, 128 when either is
     not a commit here. Every head pushed without a force-push descends from the one before,
     and this repo never rebases, amends or force-pushes (the untangle skill: "Merge
     `origin/main` in; never rebase"). **Chosen**, as the design pins.
3. **The rule as built**, one function every path uses (`review_check`, through
   `standing_verdicts` and `in_line` in `scripts/review_checks.py`):
   - A verdict counts when it is a writer's (`push_reader`), not voided by a dismissal,
     after the last base change, and on a commit in the head's history (`in_line(commit,
     head)`).
   - A counted verdict stands unless a counted verdict submitted after it is on the same
     commit or a descendant. So a `BLOCK` stands until one is, and the latest verdict on the
     most descendant reviewed commit always stands.
   - `review` fails on the newest standing `BLOCK`, naming whether it carries. With none,
     every standing verdict is an `APPROVE`, and the one on the most descendant commit
     decides: success when it carries to the head, else the delta review it needs.
   - Two standing commits neither in the other's history (a force-pushed head merged back
     into the line) have no most descendant one: the check fails, asking a review of the
     head, which descends from both and so answers both.
   - `in_line` raises on a head that is not a commit here, or an ancestry git cannot read:
     `_review_check` exits 2, and `review.yml`'s trap posts "could not decide". A reviewed
     commit that is not here, with the head here, is not in its history (force-pushed away
     and not fetched).
   - The merge group follows: `--group-sha` makes the same decision and `group_check` passes
     only its success. Unchanged.
4. **Departure: a writer's `BLOCK` on a commit force-pushed away voids the verdicts before
   it**, as a dismissal does; an `APPROVE` there is ignored, as the design says. Ignored, the
   BLOCK would reopen case 3 across a force-push: `APPROVE@H1'` t1, then a late `BLOCK@H1`
   t2, H1' the same change pushed again, would pass with the finding unfixed. Voiding never
   jams a PR: the check waits, and any verdict posted after the BLOCK counts. The design's
   step 3 and its lane-0 test (8) in `claude-ship.md`, merged with #5856 while this was
   built, are corrected here to say so, and its owed line for this lane is ticked.
5. **An intended narrowing:** a rebased or force-pushed head no longer carries a verdict
   (`carries`' docstring loses "a rebase … carries"); it waits for a review. `carries` itself
   is unchanged and still carries a prose delta until `process/ship-rules` drops it.
6. **Who counts stays `push_reader`.** The design's "who counts" adds this repo's own bots
   for what the reviewer and steward read; `review.yml` keeps `push_reader` (its step 3
   counts the verdicts `latest_verdict` counted). A bot's review counting as a verdict would
   let any workflow holding `pull-requests: write`, a PR's own among them, approve it.
7. **Reads stay lazy.** `can_push` is asked newest first, once a login, and only for a
   verdict that no newer counted verdict answers (on its commit or a descendant), or a
   force-pushed-away BLOCK: an answered verdict cannot decide, so its author's read never
   holds a PR. `p2-review-writers.md` item 5's "only authors newer than the deciding writer
   are read" is now this: a writer's late APPROVE of an earlier head no longer stops the
   reads, since an older BLOCK on the newer head may stand.
8. **Tests** (`scripts/tests/test_review_check.py`, real commits through the CLI, each as
   the PR's run and its merge group's): the nine cases of the review of f83cc0f15; the most
   descendant commit deciding over the latest time; a reader's late BLOCK; a force-pushed
   head's APPROVE ignored, its BLOCK answered by a later review, and its late BLOCK voiding;
   two reviewed commits in no line; `in_line`'s ancestry and refusals; and case 3 through
   `review.yml`'s own script, as a PR and as a merge group. The unit tests of the verdict
   shape now read `standing_verdicts`.
9. **Mutations**, each killed by the tests named in the PR body: the old latest-by-time
   rule; the naive most-descendant rule with a standing BLOCK ignored; no history filter;
   a force-pushed-away BLOCK ignored; an older verdict answered by any newer one, or by one
   on an ancestor; dismissals ignored; a reader counted; a missing commit read as in the
   history; a missing head read as no history; the group passing whatever its PR's decision.

- [ ] live: the first PR reviewed after this merges reads its `review` run's title as before (`APPROVE: reviewed at this head (...)`), and the first one with two reviewed heads names the standing verdict
