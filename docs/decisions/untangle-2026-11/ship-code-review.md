# ship-code-review as built (2026-11-28)

The second build lane of `claude-ship.md`: the reviewer posts its own review
with a repo script, by a find, verify, rank and post procedure, and the host's
landing step goes.

## Research

- The collaborator read, from this session with the GitHub MCP's
  `list_repository_collaborators` (read only, 2026-11-28):
  `{"firstPage":0,"items":[{"login":"Jonnyton","id":23060883,"role_name":"admin"}],"lastPage":0,"nextPage":0,"prevPage":0}`.
  That is the writers file's shape: `items[].role_name`, with `nextPage` 0 on
  the last page.
- This session's GitHub proxy refuses GraphQL (`gh pr view`, HTTP 403) and
  names REST and its own routes in its place:
  `POST repos/{o}/{r}/pulls/{n}/ccr/comments/{id}/resolve` resolves a thread,
  and `POST .../pulls/{n}/ccr/ready_for_review` readies a PR. REST `GET
  pulls/{n}/reviews`, `pulls/{n}/comments`, `pulls/{n}/files`,
  `issues/{n}/comments` and `issues/{n}/timeline` work (`--paginate
  --slurp`), as does `GET labels/steward-watching` (200).
- A review comment lands only on a line its file's patch shows
  (`pulls/{n}/files`), which is the diff at the PR's head. A pending review
  has no `submitted_at`. The reviews listing has no edit time; the timeline's
  `reviewed` events do, and GitHub stamps `updated_at` up to a second after a
  record is made with no edit (#5862's read of 249 reviews). A comment has
  `updated_at` itself.
- `review.yml` reads the first non-blank line of a review by an account that
  can push; `fix/verdict-order` (#5862) rewrites which verdict governs, and
  an edit voids the verdicts before it there.
- The one review on #5861 (6096097641) was written by hand under headings
  ("## Important"), with no line opening with a marker.

## As built

1. `scripts/review_post.py read` lists what a review must answer: every
   earlier `Important:` finding (`review:<id>#<k>` from a review's body,
   `comment:<id>` for an inline one its body lacks, outdated ones included),
   every edit to a review or to a thread's first comment (`...:edited`), every
   `Founder question:` (`question:<id>`), and the PR's comments, the writers'
   apart from the bots'. Entries come only from writers, whatever the base
   did.
2. `review_post.py post` builds one `COMMENT` review from the reviewer's JSON
   and posts it on `--commit`. The body is the record: the verdict first, a
   founder question on its own line, every finding with its one marker and
   `path:line`, each also inline where its line is in the PR's diff, and every
   earlier entry: an Important or an edit as fixed, withdrawn with its reason,
   or standing; a question as answered, by a writer's comment posted after it,
   or standing. It refuses a verdict its findings contradict either way, a
   founder question under `APPROVE`, an earlier entry left out, unknown or
   listed twice, a withdrawal with no reason, an answer that is not a writer's
   comment after the question, past five Nits, a finding text with a blank
   line, a summary, text or note line opening with a marker or a founder
   question (a later read would take it for an entry of its own), and a
   missing, partial or writer-less writers file. It then resolves the threads
   of the inline findings it lists as fixed, by the session's route.
3. The reviewer runs `origin/main`'s poster, copied out with `git show` to a
   directory outside the head's worktree and run with `python3 -I`, as
   `land_pr2.sh` ran main's gates: a PR cannot loosen its own review. So the
   poster imports nothing of the repository: its verdicts are its own two,
   held equal to `review_checks`' by a test, and a failed read fails the run
   for the reviewer to repeat.
4. The peer-agents skill: the dispatch section holds the denied-permission
   rule, the reviewer's dispatch (a writers file in the brief, `--write` in its
   own worktree, its result the posted review) and the founder step; the
   reviewer's procedure is find in parallel, verify once, rank, post, with
   main's poster; the `VERDICT: APPROVE|ADAPT|REJECT` contract is gone. The
   constraints block lets a reviewer's own subagents find and verify (the
   untangle skill's step 5 already says subagents search); only peers are
   refused. Its `--write` line for codex said `--full-auto`; `peer_agent.py`
   passes `-s danger-full-access`, and the line now says so.
5. The steward skill opens the PR as a draft and readies it, the acts
   `land_pr2.sh` did, says the reviewer posts with main's poster, and drops
   "until it exists, watch without it".
6. `REVIEW.md`'s review section names the poster instead of restating the
   format it writes, and says the poster is main's, that earlier edits and
   founder questions are accounted for across base changes, and that the
   bot's comments raise and answer nothing.
7. #5861's Nits: `ship-rules.md`'s "fails no run" corrected, its item 6 and
   label line; `pr-scope-guard.yml` cites `REVIEW.md` for the lineage rule;
   `review-queue.yml` drops the removed carry's reason; the gates page's pin
   at its file's size.

## From #5864's first review (25154449e, BLOCK)

- Running the PR's own poster let a PR loosen its review: item 3.
- An earlier founder question was never accounted for: item 2's `question:`.
- An edited or deleted Important left the accounting: a review's body holds
  every finding, so deleting an inline comment changes nothing, and an edit
  stands until a review marks it, since what it said before is gone.
- A base change dropped every earlier finding, and a PR's own workflow can
  retarget the PR twice: findings now carry across base changes; only
  verdicts reset there (`review.yml`).
- `github-actions[bot]` was a source of findings, and a PR's workflow can post
  as it: entries come from writers only.

## Departures

- **A head past the commit read is refused**, nothing posted, where
  `land_pr2.sh` only noted it. The poster places inline comments from the
  head's diff, and a review covers the head it read: the reviewer reviews the
  new head. A push between the read and the `POST` can still leave a review
  on an earlier head, which is why `REVIEW.md`'s "one more after a `BLOCK`
  posted late on an earlier head" stays.
- **The review App's bot does not count**, where the design named it: it posts
  only check runs (`review.yml`), and naming its login in the script would be
  a live name in code. `github-actions[bot]`'s comments are listed, apart, and
  raise and answer nothing.
- **Reviews written before the poster** (headings, no line opening with a
  marker) list no earlier finding. A PR reviewed that way, #5861 and #5862
  today, has its next review list those findings by hand in its summary.

## Pins

The skills aggregate moves from 37,400 to 37,667 B (peer-agents 5,175 to
5,184 B, steward 4,147 to 4,405 B) and `REVIEW.md`'s from 4,550 to 4,486 B:
together +203 B, for the rules the review added, inside the aggregate total.
The gates page's pin drops from 1,387 to 1,231, its file's size. The
per-file total drops from 4,403 to 4,246, and the aggregate total from 58,471
to 58,339.

## Known

- The live PR body's footer is the session proxy's, which rewrites it on every
  `PATCH` (`claude-ship.md`'s Known); `land_pr2.sh` re-appended the session
  footer by hand. CI's Untangle check reads above it.

## Owed

- [ ] live: the coordinator deletes the host's `land_pr2.sh`, `land_pr.sh` and `untangle/receipt_*.md` and the brief's receipt line, then ticks `claude-ship.md`'s `process/ship-code-review` lane line, once one PR's review is posted with main's `review_post.py` (after `process/ship-code-review` merges)
