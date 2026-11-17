# p2-review-writers as built (2026-11-17)

A security fix inside P2 (`tasks.md` §10 "Process first"), found in the review of
`untangle/p2-receipt-delete`, and landed before it.

1. **The finding.** `drain_review_gate.py` `latest_verdict` counted a review whose first line
   is a verdict when its `author_association` was OWNER, MEMBER or COLLABORATOR. While the
   receipt also gates, a pass needs a body edit, which takes write access. Once
   `untangle/p2-receipt-delete` deletes the receipt, the `review` check alone decides, so a
   verdict from someone who cannot push would pass a PR.
2. **Research: what `author_association` says.** GitHub's `CommentAuthorAssociation`
   (github/docs `src/graphql/data/fpt/schema-issues.json`): MEMBER is "a member of the
   organization that owns the repository", COLLABORATOR "has been invited to collaborate on
   the repository", and OWNER "the owner of the repository". None of them is a permission.
   MEMBER needs no access to this repository at all, and COLLABORATOR includes read and
   triage.
3. **Research: the two sources of push access.**
   - REST `GET /repos/{owner}/{repo}/collaborators/{username}/permission` (github/docs
     `src/rest/data/fpt-2022-11-28/collaborators.json`). `permission` is `admin`, `write`,
     `read` or `none`, with maintain mapped to `write` and triage to `read`. `role_name`
     names the role, custom roles included. "The calculated permissions are the highest role
     assigned to the collaborator after considering all sources of grants, including: repo,
     teams, organization, and enterprise." So an org member whose access comes through a
     team, or through the organization's base permission, reads as that access. The endpoint
     works for server-to-server tokens and needs "Metadata" repository permissions (read).
     Its documented answers are 200 and 404.
   - GraphQL `PullRequestReview.authorCanPushToRepository` (`schema-pulls.json`): "Indicates
     whether the author of this review has push access to the repository", `Boolean!`.
   - **Decided: the REST read.** Its failure is an HTTP error, so "unknown" stays distinct
     from "cannot push", and its docs name every grant it counts. **Rejected: GraphQL.** A
     non-null boolean cannot say "unknown", and a `false` on a writer's BLOCK would bring back
     the APPROVE before it. Its docs do not say which grants count. It would also move the
     reviews from the REST listing to a paginated GraphQL query: a second shape of the same
     list, to save one read per author.
4. **The token.** `review.yml` reads with `github.token`, the GitHub Actions App's
   installation token (github/docs `content/actions/concepts/security/github_token.md`).
   The keys `permissions:` can set (`data/reusables/actions/github-token-available-permissions.md`)
   have no `metadata`, so the workflow's `contents`, `pull-requests` and `actions` reads
   need no change. This session cannot call the endpoint (403), so the token's answer is
   unproved here. If it is refused, every decision says so by name and none passes (item 5),
   and the live proof below shows it.
5. **The decision fails closed.**
   - `latest_verdict` asks `can_push(login)` newest verdict first, once a login, and takes
     the first author who can push. A verdict by anyone else is an ordinary comment, for
     BLOCK as for APPROVE, as before for NONE and CONTRIBUTOR.
   - `push_reader` is the production `can_push`: `admin` and `write` can push, `read` and
     `none` cannot. A failed `gh` call, a timeout, an answer that is not JSON, or any other
     `permission` value raises `PermissionUnread` with its reason. A login that is not a
     GitHub login is refused before it reaches a path.
   - `PermissionUnread` stops the whole decision. It does not just skip that review: the
     unread author may be a writer whose BLOCK decides, and skipping it would bring back an
     older APPROVE. Only authors newer than the deciding writer are read, so an old
     unreadable account cannot hold a PR.
   - `review_check` turns it into an in-progress run titled `no verdict counts: could not
     read <login>'s permission: <gh's error>`. Its summary says a verdict counts only from an
     account that can push and to rerun `review.yml`. The job summary already prints the
     title. In progress, not red, because nobody blocked: it is no verdict. A merge group
     turns it red (`group_check`), so the queue never waits on it.
6. **What reading at decision time means.** A writer who loses push access stops counting at
   the next decision. A pass already posted stands until then, and a merge group decides
   afresh, so it reads the permission again when queued. An author who gains push access
   counts from then, older verdicts included: by then they are a writer.
7. **`author_association` goes from the review check.** As a filter before the read, it would
   save one read for each verdict by an outsider. It is not documented as covering every
   writer, so it could only drop a writer's BLOCK. The receipt's comment inventory still reads
   `_TRUSTED_ASSOCIATIONS`. `untangle/p2-receipt-delete` deletes that, and its copy of
   `latest_verdict` still reads the association. When it merges main, it keeps this
   `latest_verdict` and drops `_TRUSTED_ASSOCIATIONS`.
8. **Tests.** `scripts/tests/test_review_writers.py` fakes the permission read at `_gh_json`.
   `tests/test_review_check.py`'s fake `gh` answers the permission read from its data, and
   its CLI runs use it too. Mutations, each killed: read-only `APPROVE` passes (`read` counted
   as push); the author ignored; a failed read counted as cannot-push, which passes the older
   APPROVE; a failed read counted as push.
9. **Left:** `live:` the first real PR review after this merges, read in the `review` run's
   title and the job summary: `APPROVE: ...` from a writer, not `no verdict counts: ...`.
