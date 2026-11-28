# ADR-038: Every PR Is Reviewed by One Check

## Status

Accepted (2026-11-17, `untangle/p2-receipt-delete`, after the ruleset required
`review`; `docs/decisions/untangle-2026-11/p2-receipt-delete.md`). Drafted in
`untangle/p2-review-gate`. Supersedes ADR-015's review line, and ADR-015's Status line
says so.

## Date

2026-11-16

## Context

ADR-015 says adversarial review covers floor-class changes and gate files only. Since
2026-10-02 every PR has needed a review instead: auto-enroll armed #4247, a superseded
head with a known data-loss bug, because it touched no path that needed one. And before
2026-09-26 a verdict was only a PR comment, so #3989 auto-merged at the exact head its
reviewer had blocked.

So every PR is reviewed today, through two gates that each read the verdict:

- **the receipt:** the body's `Drain-Review-*` lines, citing a comment that opens with
  the same approval, read by the required `Diff scope declared` and by auto-enroll,
  enqueue, queue freshness and the floor hotfix;
- **the `review` check:** a PR review on the commit read, turned by `review.yml` into
  a check run that only the review App can post. It is not required yet.

Two gates for one verdict are two copies of it, and only the check can say BLOCK.

## Decision

1. **Every PR is reviewed, and the one gate is the required `review` check.** The
   ruleset requires it with the review App as its expected source, so no workflow
   job can satisfy it.
2. **A verdict is a PR review on the commit the reviewer read** (`commit_id`), event
   `COMMENT`, whose first non-blank line is exactly `APPROVE` or `BLOCK`, with the
   evidence after it. Verdicts by an account that can push to the repository (write,
   maintain or admin), after the PR's last base change, on a commit in the head's
   history, decide: a `BLOCK` stands until a verdict after it on the same commit or a
   descendant, and else the most descendant commit's latest verdict decides
   (`untangle-2026-11/verdict-order.md`); a permission GitHub cannot read is no
   verdict (`untangle-2026-11/p2-review-writers.md`).
   `COMMENT`, because the one writer account cannot approve its own PR.
3. **A verdict carries to a later head** in its history only across the same change
   or a delta of Markdown prose. Any other delta needs a review of that delta.
4. **A merge group decides its PR afresh** from the PR's reviews, never from its
   `review` runs.
5. **Each decision is one new check run**, never an edit of one. Readers take the
   App's newest run for their own PR.
6. **The receipt goes:** the body's `Drain-Review-*` lines, the receipt comment, and
   the scope guard's receipt block. `Diff scope declared` keeps what reads the PR's
   own diff and body: the release-critical count, test hygiene and the Untangle check.
7. **No builder approves its own work.** The verdict is an independent reviewer's.

## Consequences

- ADR-015's line "Adversarial review ... covers floor-class changes and gate files
  only" is superseded: every PR is reviewed.
- Auto-enroll arms every ready PR at open, and GitHub queues it once the required
  checks pass, `review` among them. It arms nothing while the rules GitHub applies to
  `main` differ from the ruleset file (`scripts/ruleset_drift.py`). `enqueue-merge`
  goes: auto-merge armed by a person queues the PR by itself.
- Queue freshness and the floor hotfix read the App's `review` check for the PR
  (`drain_review_gate.py` `review_passed_at`).
- `docs/reference/executable-gates.md` and the untangle skill name one way to land: the
  verdict posted as a PR review.
- The delivery contract is `openspec/specs/development-coordination-runtime/`
  ("A review verdict is a check run only the review App posts").
