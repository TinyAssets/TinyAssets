# ADR-038: Every PR Is Reviewed by One Check

## Status

Proposed (draft, 2026-11-16, `untangle/p2-review-gate`). It is accepted in the PR that
deletes the receipt, P2's third step, once the ruleset requires `review`
(`docs/decisions/untangle-2026-11/p2-review-gate.md`). On acceptance it supersedes
ADR-015's review line, and ADR-015's Status line says so.

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
   evidence after it. The latest trusted verdict after the PR's last base change
   decides. `COMMENT`, because the one writer account cannot approve its own PR.
3. **A verdict carries to a later head** only across the same change or a delta of
   Markdown prose. Any other delta needs a review of that delta.
4. **A merge group decides its PR afresh** from the PR's reviews, never from its
   `review` runs.
5. **Each decision is one new check run**, never an edit of one. Readers take the
   App's newest run for their own PR.
6. **The receipt goes:** the body's `Drain-Review-*` lines, the receipt comment, and
   the scope guard's receipt block. `Diff scope declared` judges scope only.
7. **No builder approves its own work.** The verdict is an independent reviewer's.

## Consequences

- ADR-015's line "Adversarial review ... covers floor-class changes and gate files
  only" is superseded: every PR is reviewed.
- Auto-enroll, enqueue, queue freshness and the floor hotfix read the App's `review`
  check for the PR (`drain_review_gate.py` `review_passed_at`).
- `docs/reference/executable-gates.md` and the untangle skill name one way to land: the
  verdict posted as a PR review.
- The delivery contract is `openspec/specs/development-coordination-runtime/`
  ("A review verdict is a check run only the review App posts").
