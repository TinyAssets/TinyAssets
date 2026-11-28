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
- How Code Review keeps the reviewed code from steering the reviewer, read
  2026-11-28 (`code-review`, `code-review-customize`): it reads `REVIEW.md`
  and its includes "from the base or default branch and never from the pull
  request's own changes"; "a review works from reading the diff and the
  surrounding code, without building or running your code"; "inside a review,
  access to your repository is read-only". PR Steward reads its instruction
  files from the default branch too (`pr-steward-security`).
- What a Claude Code session loads, read the same day (`settings`,
  `permissions`): "Hooks and other `.claude/settings.json` keys load from the
  current working directory's `.claude/` folder", and `CLAUDE.md` and skills
  from the working directory too. A directory added with `--add-dir` still
  loads its `.claude/skills/`, `.claude/commands/` and `.claude/agents/`
  (and its `CLAUDE.md` with `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`),
  so adding the head that way would load the PR's skills. Codex reads the
  working directory's `AGENTS.md`.

## As built

1. `scripts/review_post.py read` lists what a review must answer: every
   earlier `Important:` finding (`review:<id>#<k>` from a review's body,
   `comment:<id>#<k>` for an inline one its body lacks, outdated ones
   included; both read line by line, a marker or question after any indent or
   bullet),
   every edit to a review or to a thread's first comment (`...:edited`), every
   `Founder question:` (`question:<id>#<k>`), and the PR's comments, the
   writers' apart from the bots'. Entries come only from writers, whatever the
   base did. Edits are read by `review_checks`, the one rule `review.yml`
   applies: `review_edits` for a review (now `{id: (author, when)}`) and
   `is_edited` for a comment, a record with no times failing loudly. The poster
   imports the `review_checks` beside it, main's in main's worktree.
2. `review_post.py post` builds one `COMMENT` review from the reviewer's JSON
   and posts it on `--commit`. The body is the record: the verdict first, a
   founder question on its own line, every finding with its one marker and
   `path:line`, each also inline where its line is in the PR's diff, and every
   earlier entry: an Important or an edit as fixed, withdrawn with its reason,
   or standing; a question as answered, by a writer's unedited comment posted
   after it, or standing. It refuses a verdict its findings contradict either way, a
   founder question under `APPROVE`, an earlier entry left out, unknown or
   listed twice, a withdrawal with no reason, an answer that is not a writer's
   comment after the question, past five Nits, a finding text with a blank
   line, a summary, text or note line opening with a marker or a founder
   question (a later read would take it for an entry of its own), and a
   missing, partial or writer-less writers file. It then resolves each thread
   whose findings it lists as fixed, all of them, once, by the session's route.
3. The reviewer is a fresh session started in a new detached worktree of
   `origin/main`, Code Review's way (it runs in a clean environment): a
   `claude -p` peer in review mode, `peer_agent.py claude --review <dir> --cwd
   <main>`, and no other form (§ Reviewer mode: no full agent, no credential,
   never `--write`). A session loads its hooks, settings, MCP config and
   instructions from its working directory when it starts, so one started
   there loads main's alone and inherits nothing; a subagent keeps its
   parent's, which no check at dispatch can read (§ Reviewer form). It reads
   the head only as git objects
   at its sha (`git diff`, `git show`, `git grep`), never checked out or added
   with `--add-dir`, which would load the PR's skills, and never builds,
   installs, runs or imports the head's code: CI runs the gates, the reviewer
   reads their results on the head, and a test it wants is a finding. The
   rules live once, in the peer-agents skill's reviewer procedure; `REVIEW.md`,
   the untangle and steward skills point there. Nothing is copied, so a copy
   cannot fail into an empty file, and before this PR merges the poster's
   command fails loudly (no such file). The poster calls `gh` by its absolute path and
   refuses one inside a git checkout, read from the files rather than from
   `git`. A failed read fails the run for the reviewer to repeat.
4. The peer-agents skill: the dispatch section holds the denied-permission
   rule, the reviewer's dispatch (a writers file in the brief, where it runs
   by the procedure, its result the posted review) and the founder step; the
   reviewer's procedure is where it runs, then find in parallel, verify once,
   rank, post; the `VERDICT: APPROVE|ADAPT|REJECT` contract is gone. The
   constraints block lets a reviewer's own subagents find and verify (the
   untangle skill's step 5 already says subagents search), refuses peers, and
   keeps "the one test file needed" for a builder: a reviewer runs none of
   the head's code. Its `--write` line for codex said `--full-auto`; `peer_agent.py`
   passes `-s danger-full-access`, and the line now says so.
5. The steward skill opens the PR as a draft and readies it, the acts
   `land_pr2.sh` did, says the reviewer works from a worktree of `origin/main`
   and never runs the branch's code (a test it asks for is the steward's to
   add), and drops "until it exists, watch without it". The untangle skill's
   "a reviewer checks the PR out" becomes "reads the PR as git objects".
6. `REVIEW.md` keeps main's authority line, Ship's framing: the rules come
   only from `origin/main` (the file, its includes, `CLAUDE.md`, main's
   procedure, in a fresh session started in a detached worktree of it), never
   the PR's copy. Everything else is what is reviewed, never instructions: the
   head's files, its `REVIEW.md`, `CLAUDE.md`, `AGENTS.md` and skills included,
   and all PR text (body, comments, review replies), whoever posted it, since
   every agent posts as the one writer; text in them addressed to a reviewer is
   a finding, and a founder answer is read as his answer. The reviewer reads the head as git
   objects and never runs its code. Its review section names the
   poster instead of restating the format it writes; earlier edits and founder
   questions are accounted for across base changes; the bot's comments raise
   and answer nothing.
7. #5861's Nits: `ship-rules.md`'s "fails no run" corrected, its item 6 and
   label line; `pr-scope-guard.yml` cites `REVIEW.md` for the lineage rule;
   `review-queue.yml` drops the removed carry's reason; the gates page's pin
   at its file's size.
8. Carried from #5862's review into `review_checks.py`, after merging it: one
   cache of the permission read for the verdict and edit paths, where there
   were two that could ask twice and read a 404 in opposite ways, and a 404
   read as no writer on the verdict path too, so a `github-actions[bot]`
   verdict no longer holds a PR; a thread reply's empty review is named as
   evidence a later APPROVE of an earlier head is late (`verdict-order.md`
   items 5, 8 and 9).

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

## From #5864's second review (bd4183d97, BLOCK)

- The reviewer ran in the head's worktree, so it loaded the PR's hooks,
  settings, `PATH`, instructions and skills (a planted `gh` got an APPROVE
  posted): item 3, from the research above.
- A failed copy from main left an empty file that ran and exited 0: nothing is
  copied now.
- The edit rule copied #5862's with nothing holding them equal, and differed:
  it now matches, and one source is owed (below).
- The verdicts test read a private name: it reads a posted review through
  `review_checks`' public `latest_verdict`.
- An edited founder answer counted, and a review's second founder question was
  not listed: neither holds now.

## From #5864's third review (c1ea3ab98, BLOCK)

- Nothing forbade the reviewer running the head's code, and two lines invited
  it ("run at most the one test file needed"; "a reviewer checks the PR out"):
  a planted test could post a late APPROVE with the session's `gh`, skipping
  the poster, and both main's and #5862's verdict read count it. Item 3 now
  forbids it, Ship's way. Running head code locally has no need Ship does not
  meet with CI, so no credential-free form is offered: every process in this
  session inherits `GH_TOKEN` and `GITHUB_TOKEN` (read by name, 2026-11-28),
  and no form without them was shown to be achievable here.
- The subagent form inherited its parent's checkout, which may be the head's:
  item 3 pins it.
- An indented or bulleted `Founder question:` passed under `APPROVE` and went
  unlisted, and an inline `Important:` after a comment's first line went
  unlisted: both read now as a review body's lines are.

## From #5864's fourth review (7065d9113, BLOCK)

- The head had dropped main's "never the PR's copy" and said nothing of the
  head's own instruction files read with `git show`: item 6 restores the
  authority line and makes the head's text what is reviewed.
- "Not the head's tree" was the wrong test for the subagent, and uncheckable:
  a clean-ancestor check by the dispatcher, replaced in the fifth round.
- An inline comment's `Founder question:` went unlisted
  (`question:comment:<id>#<k>` now), and a check from a workflow the head
  edits read as proof (the procedure now says it proves nothing).

## From #5864's fifth review (b6b3c4574, BLOCK)

- The clean-ancestor check read the checkout's tracked files at dispatch, not
  what the parent loaded at its start: an ignored `.claude/settings.local.json`
  or `.mcp.json`, settings fixed at start that differ from the checkout now,
  and a merged hook main later reverted all passed it. The fifth round of
  patching the subagent's isolation is Fact 14's signal to research the shape
  again: § Reviewer form.
- All PR text is under review, a writer's too, since every agent posts as the
  one writer (`REVIEW.md`). Founder questions in replies and in edited inline
  comments are listed. `claude-ship.md`'s lane line is ticked; the land step
  is the `live:` line below. `review.yml`'s header and the 404 docstring
  (`PermissionUnread`, pre-existing) say what a 404 is.

## Reviewer form

Researched 2026-11-28, before patching a sixth time. The simplest form isolated
by construction is a session that starts in a clean detached worktree of
`origin/main`: it reads hooks, settings, MCP config and instructions from that
directory alone (Claude Code's `settings` and `permissions` docs, item 3's
research), as Code Review runs in a clean environment. A subagent can never be
that: it keeps whatever its parent loaded at its own start, and nothing a
dispatcher reads later shows what that was, so every check of it is a check of
the wrong thing.

The form is available here at no extra cost. From a new detached worktree of
`origin/main` (5c6a53ef9), `claude -p "Reply with the single word OK"` with
`ANTHROPIC_API_KEY` unset exited 0 and answered `OK` on the subscription, the
budget a subagent spends too; it loaded that worktree's tracked `.claude/`
settings (it noted their allow rules wait for workspace trust: they never widen
a reviewer) and no ignored file, since a new worktree has none. Main's
SessionStart hook never touches a linked worktree. So the peer is the only
reviewer form and the subagent clause is deleted; what still reaches the peer
from outside is the host's own `~/.claude` and the dispatcher's environment,
neither of which a PR writes, and the environment is narrowed below.

## Reviewer mode

The coordinator found, before dispatching this PR's sixth review, that the
fresh peer ran `--write`, which is `--dangerously-skip-permissions` for claude:
a reviewer, which reads untrusted PR text by design, held an unrestricted shell
with the session's credentials and no permission check, the opposite of Code
Review's narrow tools, and a dispatch that routes around this host's
permission rules. Fact 17: it lands here.

Research, 2026-11-28 (`claude --help` of 2.1.332; the `permission-modes` and
`permissions` docs):

- `--permission-mode dontAsk` "auto-denies every tool call that would otherwise
  prompt"; allow rules (`--allowedTools`) still run, deny rules
  (`--disallowedTools`) beat allow rules, and `--permission-prompts none` leaves
  nobody to answer a prompt. `--tools` sets the built-in tools at all.
- A built-in set of read-only Bash commands (`cat`, `echo`, `ls`, `grep`, git's
  reads, ...) runs unasked "in every mode", not configurable, though a deny rule
  applies, and outside the working directories only while
  `permissions.blockReadsOutsideWorkingDirectories` is off.
- A compound command (`&&`, `||`, `;`, `|`, `$( )`) is split, and each part must
  be allowed; a deny rule applies to any part.
- `--bare` would skip hooks and settings, but it reads no OAuth, so it cannot
  run on the subscription. `--strict-mcp-config` with no `--mcp-config` loads no
  MCP server.
- This session's environment holds `GH_TOKEN`, `GITHUB_TOKEN`, AWS, E2B and
  Cloud SDK credentials (names read only). A reviewer that can `echo` a
  variable could repeat any of them into a public review, so a deny list cannot
  protect them: the environment has to be built from an allowlist. `claude -p`
  ran with only `PATH`, `HOME`, `TERM`, `LANG`, the proxy and its certificates
  (exit 0, `OK`), and `gh` ran with `GH_TOKEN` set to a placeholder: the
  session's proxy replaces it with the real credential.
- Codex is not installed on this host, and its sandboxes take no per-command
  allowlist, so it is no reviewer: `--review` refuses it.

As built, `peer_agent.py claude --review DIR` (`review_claude_args`,
`review_env`): `dontAsk`, no permission prompts; the tools Read, Grep, Glob,
Bash, Write, Edit and Agent (its finders and verifier); allowed Read, Grep,
Glob, Agent, writes under DIR only, git's `show`, `diff`, `log`, `grep`,
`rev-parse` and `merge-base`, and `python3 -I <main>/scripts/review_post.py` and
`review_checks.py --untangle-check` by path; denied `gh`, `curl`, `wget`, `env`,
`printenv`, `git push`, WebFetch and WebSearch; reads and writes outside the
working directories blocked; DIR added; no MCP server; an environment of
`_REVIEW_ENV` and the placeholder `GH_TOKEN` alone. `--review` and `--write`
cannot be one dispatch. The reviewer reads CI's checks through the poster's
`read`, which now lists the head's check runs, so it needs no `gh` of its own.
`git worktree` is left out: the dispatcher makes `<main>`.

The probe, the same flags from a worktree of this branch (whose poster `main`
lacks yet), told to run each command once: `env`, `printenv HOME`, `curl -sS
https://example.com`, `gh api -X POST .../issues/0/comments`, `python3 -c
'print(1)'` and `echo "${AWS_SECRET_ACCESS_KEY:-unset}" | wc -c` were refused,
and so was a Write into the worktree (the file is absent); `git show
HEAD:REVIEW.md`, the poster's `read` (through the placeholder token) and a Write
into DIR ran. `scripts/tests/test_peer_agent_review.py` holds the flags and the
environment (8 mutations, each killed).

## Bootstrap

Until this PR merges, `origin/main` has no poster, so this PR is reviewed by
hand under main's `REVIEW.md`, which predates it:

- Rounds 1 and 2 (25154449e, bd4183d97) reviewed from detached checkouts of
  the head and ran probes that imported its poster.
- Round 3 (c1ea3ab98) reviewed from a worktree of `origin/main` but ran the
  head's whole `scripts/tests`, `check_context_budget.py` and
  `test_rulebook_ratchet.py`, because the brief asked for gates.
- Round 4 (7065d9113) ran none of the head's code.

No review was posted on the PR: each verdict is a host receipt, so this PR's
`review` check comes only from the host's `land_pr2.sh` posting the last
receipt, which the `live:` owed line deletes afterwards. Main's `peer_agent.py`
has no `--review` yet either, so this PR's next review is dispatched in that
mode with this head's `peer_agent.py`, which the coordinator reads first. The
first PR after it is reviewed from a worktree of `origin/main` with the poster
and review mode this PR lands, and runs none of the head's code.

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
  marker) list no earlier finding. A PR reviewed that way, #5861, #5862 and
  this one today, has its next review raise each finding that still stands
  again as a finding of its own: the summary takes no marker line, and a later
  read takes entries only from marker lines.

## Pins

The skills aggregate moves from 37,400 to 39,153 B (peer-agents 5,175 to
6,475 B, steward 4,147 to 4,504 B, untangle 10,575 to 10,671 B) and
`REVIEW.md`'s from 4,550 to 4,996 B: together +2,199 B, for the rules the
five reviews and the review mode added, inside the aggregate total.
The gates page's pin drops from 1,387 to 1,231, its file's size. The
per-file total drops from 4,403 to 4,246, and the aggregate total from 58,471
to 58,339.

## Known

- The live PR body's footer is the session proxy's, which rewrites it on every
  `PATCH` (`claude-ship.md`'s Known); `land_pr2.sh` re-appended the session
  footer by hand. CI's Untangle check reads above it.

## Owed

- [x] fix/verdict-order: whichever of #5862 and this PR merges second makes `review_post.py` read review edits through `review_checks`' one rule and slack, and points `test_a_posted_verdict_is_the_one_review_checks_reads` at `standing_verdicts`
- [ ] live: the coordinator deletes the host's `land_pr2.sh`, `land_pr.sh` and `untangle/receipt_*.md` and the brief's receipt line (the land step `claude-ship.md`'s ticked lane line leaves to this line), once one PR's review is posted with main's `review_post.py` (after `process/ship-code-review` merges)
