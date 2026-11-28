# verdict-order as built (2026-11-28)

A live bug in the required `review` check (Fact 17), found by #5856's reviews (I-4 of the
review of 46bebfb2c; I-1 and Nit 1 of the review of f83cc0f15), fixed in its own PR before
`process/ship-rules`. The rule is #5856's § Target pipeline step 3 (at cb858fde6), with
one departure (item 4). #5862's review of b58494bfd found a hole in that departure's first
form, and two pre-existing holes of the same kind (items 5 and 6).

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
   - A review is itself a record of push order: a review of commit D submitted at t says D
     was pushed by t, whoever wrote it and whatever it says. Item 5 uses it.
3. **The rule as built**, one function every path uses (`review_check`, through
   `standing_verdicts` and `in_line` in `scripts/review_checks.py`):
   - A verdict counts when it is a writer's (`push_reader`), not voided (a dismissal, or a
     writer's edit: item 6), after the last base change, on a commit in the head's history
     (`in_line(commit, head)`), and, for an `APPROVE`, not late (item 5).
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
4. **Departure: a writer's `BLOCK` on a commit outside the head's history stands**, red and
   named ("BLOCK at X, which is not in H's history; a review of H after it answers it"),
   until a counted verdict on the head itself is submitted after it. It answers and voids
   nothing. An `APPROVE` there is ignored, as the design says.
   - Ignored, as the design said, the BLOCK would reopen case 3 across a force-push:
     `APPROVE@H1'` t1, then a late `BLOCK@H1` t2, H1' the same change pushed again, would
     pass with the finding unfixed.
   - The first form built (at b58494bfd) voided every verdict before it, as a dismissal
     does. That dropped a standing BLOCK too: `BLOCK@H2` t1, `BLOCK@H1'` t2, a late
     `APPROVE@H1` t3, H2 the same change, passed (#5862's review, I-1). Voiding only the
     APPROVEs before it would also close that, but a void is a silent wait, and the
     reviewer's other finding (N-3) was that this one read "waiting for review". Standing
     is the same rule every BLOCK follows, with one difference: nothing orders X against
     the head's commits, so only the head is known to be later.
   - The cost: after a force-push, a review of the head is needed even if the head's next
     commit is the same change. Force-pushes are refused here by rule, so this is rare,
     and it never jams: a review of the head always answers it.
5. **A late `APPROVE` is ignored.** An `APPROVE` of commit C is late when any review of a
   strict descendant of C, in the head's history, was submitted before it: that head
   existed, and the APPROVE read an older one. It neither stands nor answers anything.
   - Why: every void (a dismissal, a base change, an edit) removes a newer head's BLOCK, and
     after it a late `APPROVE` of an earlier head counted and carried. `BLOCK@H2` t1, a
     dismissal or a base change at t2, `APPROVE@H1` t3, H2 the same change, passed
     (#5862's review, P-1). Now it waits for a review of H2.
   - The evidence is every review except a pending one: an outsider's, a comment, a
     dismissed or pre-base-change one. A review proves its commit existed then, whoever
     wrote it, and ignoring an APPROVE only ever makes the check wait.
   - An APPROVE of H1 posted while H2 exists, with nobody having reviewed H2 yet, is not
     late and still carries.
6. **An edit by a writer voids every verdict submitted at or before it**, as a dismissal
   does, so the check waits for a verdict after the edit. Before this, a writer could edit
   a `BLOCK`'s first line to `APPROVE` (or delete it), and the next decision passed with
   no review after it (`review-signal.yml` fires on `edited`; #5862's review, P-2).
   - Research: the reviews listing has no edit time (its fields, read 2026-11-28:
     `_links author_association body commit_id html_url id node_id pull_request_url state
     submitted_at user` and two Copilot fields). The issue timeline, which `review.yml`
     already reads, has `updated_at` on each `reviewed` event. GraphQL's `lastEditedAt`
     is refused from these sessions, and a second shape of the review list was rejected
     before (`p2-review-writers.md` item 3).
   - `updated_at` moves without an edit: of the 249 reviews on #5662 to #5862, 231 had a
     0 s gap after `submitted_at` and 18 had 1 s. An edit is a gap over 10 s
     (`review_edits`). An edit within 10 s of submitting goes unseen; the decision then
     reads the edited body as the verdict.
   - Only a writer's edit voids, so an outsider editing their own review cannot hold a PR.
     A dismissed review's `updated_at` is its dismissal, which voids from the dismissed
     review's own time, as before, so it is skipped.
   - Counting a review "as of its submission" would need its first body, which no read
     here returns. Voiding is the read we have, and it fails safe.
7. **An intended narrowing:** a rebased or force-pushed head no longer carries a verdict
   (`carries`' docstring loses "a rebase … carries"); it waits for a review. `carries` itself
   is unchanged and still carries a prose delta until `process/ship-rules` drops it.
8. **Who counts stays `push_reader`.** The design's "who counts" adds this repo's own bots
   for what the reviewer and steward read; `review.yml` keeps `push_reader` (its step 3
   counts the verdicts `latest_verdict` counted). A bot's review counting as a verdict would
   let any workflow holding `pull-requests: write`, a PR's own among them, approve it.
9. **Reads stay lazy.** `can_push` is asked newest first, once a login, only for a BLOCK
   or a not-late APPROVE that no newer counted verdict answers, and for each edit's author:
   an answered verdict cannot decide, so its author's read never holds a PR.
   `p2-review-writers.md` item 5's "only authors newer than the deciding writer are read"
   is now this: a writer's late APPROVE of an earlier head no longer stops the reads, since
   an older BLOCK on the newer head may stand.
10. **Tests** (`scripts/tests/test_review_check.py`, real commits through the CLI, each as
    the PR's run and its merge group's, in `test_the_decision_across_heads`):
    - the nine cases of the review of f83cc0f15;
    - the most descendant commit deciding over the latest time;
    - a reader's late BLOCK;
    - a force-pushed head's APPROVE ignored, its BLOCK answered by a later review of the
      head, standing over the head's earlier APPROVE, and not answered by a review of the
      head's parent;
    - S1 and its variants: a BLOCK outside the history, a writer's, a reader's or one on a
      commit never fetched, never answering H2's BLOCK, while a review of H2 after both
      answers both;
    - a late APPROVE after a dismissal or a base change, and an APPROVE of H2 after one;
    - a dismissal voiding from the dismissed review's time, not its own;
    - an edit: to APPROVE, out of its verdict, followed by a verdict, a 1 s update, and a
      reader's edit;
    - two reviewed commits in no line.
    Also tested: `review_edits`, the outside BLOCK's title, `in_line`'s ancestry and
    refusals, and case 3 through `review.yml`'s own script, as a PR and as a merge group.
11. **Mutations**, each killed by the tests named in the PR body:
    - the old latest-by-time rule;
    - the naive most-descendant rule with a standing BLOCK ignored;
    - no history filter;
    - an older verdict answered by any newer one, or by one on an ancestor;
    - dismissals ignored;
    - a reader counted;
    - a missing commit read as in the history, and a missing head as no history;
    - the group passing whatever its PR decided;
    - an outside BLOCK voiding everything before it (the first form), or answered by any
      newer verdict;
    - late APPROVEs counted;
    - edits ignored, a reader's edit voiding, a 0 s edit threshold, and a dismissal read
      as an edit.

- [ ] live: the first PR reviewed after this merges reads its `review` run's title as before (`APPROVE: reviewed at this head (...)`), and the first one with two reviewed heads names the standing verdict
