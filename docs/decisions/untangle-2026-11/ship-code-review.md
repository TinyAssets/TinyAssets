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
  (`pulls/{n}/files`), which is the diff at the PR's head; a pending review
  has no `submitted_at`.
- `review.yml` reads the first non-blank line of a review by an account that
  can push; `latest_verdict`, which `fix/verdict-order` (#5862) rewrites, is
  not touched here: the poster writes the verdict and reads only
  `last_base_change`, the vocabulary and the shapes.
- The one review on #5861 (6096097641) was written by hand under headings
  ("## Important"), with no line opening with a marker.

## As built

1. `scripts/review_post.py read` lists what a review must answer: each earlier
   `Important:` finding, by id (`comment:<id>` inline, `review:<id>#<k>` in a
   body), outdated ones included, and the PR's comments, the writers' apart
   from the bots'; only from an account that counts, after the last base
   change.
2. `review_post.py post` builds one `COMMENT` review from the reviewer's JSON
   and posts it on `--commit`: the verdict first, a founder question on its
   own line, each finding with its one marker (inline where its line is in
   the PR's diff, else in the body with `path:line`), and every earlier
   Important as fixed, withdrawn with its reason, or standing. It refuses a
   verdict its findings contradict either way, a founder question under
   `APPROVE`, an earlier Important left out or listed twice, a withdrawal with
   no reason, past five Nits, a summary line opening with a marker, and a
   missing, partial or writer-less writers file. It then resolves the threads
   of the inline findings it lists as fixed, by the session's route.
3. The peer-agents skill: the dispatch section holds the denied-permission
   rule, the reviewer's dispatch (a writers file in the brief, `--write` in its
   own worktree, its result the posted review) and the founder step; the
   reviewer's procedure is find in parallel, verify once, rank, post; the
   `VERDICT: APPROVE|ADAPT|REJECT` contract is gone. The constraints block lets
   a reviewer's own subagents find and verify (the untangle skill's step 5
   already says subagents search); only peers are refused. Its `--write`
   line for codex said `--full-auto`; `peer_agent.py` passes `-s
   danger-full-access`, and the line now says so.
4. The steward skill opens the PR as a draft and readies it, the acts
   `land_pr2.sh` did, and drops "until it exists, watch without it".
5. `REVIEW.md`'s review section names the poster instead of restating the
   format it writes.
6. #5861's Nits: `ship-rules.md`'s "fails no run" corrected, its item 6 and
   label line; `pr-scope-guard.yml` cites `REVIEW.md` for the lineage rule;
   `review-queue.yml` drops the removed carry's reason; the gates page's pin
   at its file's size.

## Departures

- **A head past the commit read is refused**, nothing posted, where
  `land_pr2.sh` only noted it. The poster places inline comments from the
  head's diff, and a review covers the head it read: the reviewer reviews the
  new head. A push between the read and the `POST` can still leave a review
  on an earlier head, which is why `REVIEW.md`'s "one more after a `BLOCK`
  posted late on an earlier head" stays.
- **The review App's bot does not count** as a commenter, where the design
  named it: it posts only check runs (`review.yml`), so it writes nothing the
  reviewer reads, and naming its login in the script would be a live name in
  code. `github-actions[bot]` counts (the queue-handoff ledger).
  `REVIEW.md`'s "Who counts" says the same.
- **Reviews written before the poster** (headings, no line opening with a
  marker) list no earlier finding. A PR reviewed that way, #5861 and #5862
  today, has its next review list those findings by hand in its summary.

## Pins

The skills aggregate moves from 37,400 to 37,478 B (peer-agents 5,175 to
5,049 B, steward 4,147 to 4,351 B), paid by `REVIEW.md`'s 210 B (4,550 to
4,340). The gates page's pin drops from 1,387 to 1,231, its file's size. Both
ratchet totals drop with them: the per-file total from 4,403 to 4,246, the
aggregate total from 58,471 to 58,339.

## Owed

- [ ] live: the coordinator deletes the host's `land_pr2.sh`, `land_pr.sh` and `untangle/receipt_*.md`, and the brief's receipt line, once one PR's review is posted with `review_post.py` (after `process/ship-code-review` merges)
