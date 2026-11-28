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
     decides: success when it carries to the head, else a fresh review of the head.
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
   named ("BLOCK at X, which is not in H's history; each head needs a review of it after
   that"), until a counted verdict on the head itself is submitted after it. It answers
   and voids nothing. An `APPROVE` there is ignored, as the design says.
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
   - The cost lasts the PR's life: the answer is the head's alone, so every later head,
     even the same change or a clean merge of main, needs its own review posted after the
     BLOCK, and no verdict carries past it (#5862's second review, N-2). Force-pushes are
     refused here by rule, so this is rare, and it never jams: a review of each head
     answers it there.
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
6. **An edit of a writer's review voids every verdict submitted at or before it**, as a
   dismissal does, so the check waits for a verdict after the edit. Before this, a writer
   could edit a `BLOCK`'s first line to `APPROVE` (or delete it), and the next decision
   passed with no review after it (`review-signal.yml` fires on `edited`; #5862's review,
   P-2).
   - Research, the fields. REST's review object (GitHub's docs, and the listing read
     2026-11-28: `_links author_association body commit_id html_url id node_id
     pull_request_url state submitted_at user` and two Copilot fields) has no edit time;
     its only time is `submitted_at`. The issue timeline's `reviewed` event, which
     `review.yml` already reads, adds `updated_at`, which the docs list ("string or null")
     but do not define. GraphQL's `PullRequestReview` has `lastEditedAt`,
     `includesCreatedEdit` and `updatedAt`, but these sessions are refused GraphQL ("GitHub
     GraphQL is not available from Claude Code sessions"), so it cannot be measured here.
     A second shape of the review list was also rejected before (`p2-review-writers.md`
     item 3). So the candidate is the timeline's `updated_at`, measured live below.
   - Measured on #5862 itself, 2026-11-28: one `COMMENT` review whose first line is not a
     verdict (6096259332), with one inline comment (4725827968, thread
     `PRRT_kwDOSAQuCc69A83P`). The review's timeline `updated_at` after each step:

     | Step | When | `submitted_at` | `updated_at` |
     |---|---|---|---|
     | submitted | 06:24:05 | 06:24:05 | 06:24:05 |
     | body edited (`PUT pulls/5862/reviews/6096259332`) | 06:24:36 | 06:24:05 | **06:24:37** |
     | thread resolved | about 06:25:05 | 06:24:05 | 06:24:37 |
     | reply in the thread (`POST .../comments/4725827968/replies`) | 06:25:15 | 06:24:05 | 06:24:37 |
     | inline comment edited (`PATCH pulls/comments/4725827968`) | 06:25:21 | 06:24:05 | 06:24:37 |

     So an edit of the body moves it to the edit's second, and resolving, replying and
     editing an inline comment do not; `submitted_at` never moves. The reply became a
     review of its own (6096262997, empty body, not in the timeline as `reviewed`), which
     is an ordinary comment here. The reviews listing never changed but for the body.
   - With no edit it still moves a second: of the 249 reviews on #5662 to #5862, 231 had
     a 0 s gap and 18 had 1 s. An edit is a gap over 10 s (`review_edits`). An edit
     within 10 s of submitting goes unseen, and the decision reads the edited body as
     the verdict.
   - The timeline names the review's author, not its editor. Only an edit of a writer's
     review voids: an outsider's review is no verdict either way, so an outsider
     editing their own cannot hold a PR. A dismissed review's `updated_at` is its
     dismissal, which voids from the dismissed review's own time, as before, so it is
     skipped. An edited review whose author GitHub does not find (404: a bot, a deleted
     account) is no writer's, so its edit voids nothing; any other failed read is no
     verdict, as before (#5862's second review, I-1).
   - Counting a review "as of its submission" would need its first body, which no read
     here returns. Voiding is the read we have, and it fails safe.
7. **An intended narrowing:** a rebased or force-pushed head no longer carries a verdict
   (`carries`' docstring loses "a rebase … carries"); it waits for a review. `carries` itself
   is unchanged here; #5861 (`process/ship-rules`), merged in while this was built,
   dropped its prose carry, so it carries the same change only.
8. **Who counts stays `push_reader`.** The design's "who counts" adds this repo's own bots
   for what the reviewer and steward read; `review.yml` keeps `push_reader` (its step 3
   counts the verdicts `latest_verdict` counted). A bot's review counting as a verdict would
   let any workflow holding `pull-requests: write`, a PR's own among them, approve it.
9. **Reads stay lazy.** `can_push` is asked newest first, once a login, only for a BLOCK
   or a not-late APPROVE that no newer counted verdict answers, and then for the author
   of each review edited at or after it: an answered verdict cannot decide, so neither
   its author's read nor an older edit's ever holds a PR. The first form read every
   edited review's author up front, so one 404 (a bot's, a deleted account's) held the
   PR for good, even after a writer approved the head (#5862's second review, I-1).
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
    - an edit by an author whose read 404s: never read once a newer verdict on the head
      answers it (E1), and voiding nothing when read;
    - two reviewed commits in no line.
    Also tested: `review_edits`, on the values item 6 measured too; the outside BLOCK's
    title; an edited review's author read only when its edit could matter, a 404 as no
    writer and a timeout as no verdict (`test_review_writers.py`); `in_line`'s ancestry
    and refusals; and case 3 through `review.yml`'s own script, as a PR and as a merge
    group.
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
      as an edit;
    - every edited review's author read up front (the first form), and a 404 for an
      edited review's author read as no verdict.

- [ ] live: the first PR reviewed after this merges reads its `review` run's title as before (`APPROVE: reviewed at this head (...)`), and the first one with two reviewed heads names the standing verdict
