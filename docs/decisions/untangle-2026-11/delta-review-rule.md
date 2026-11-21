# delta-review-rule as built (2026-11-21)

A rule held only in a host brief: after a review, a merge of main that changes the PR's code needs a delta review, or the PR waits. #5725 and #5727 each took a merge of main after their APPROVE (at 12:17 and 12:27) and sat until a re-confirm at 15:56. Their `review` runs read `in_progress`, "APPROVE at 8e47cebe5…; the change since needs a delta review": named, but a run in progress is no event, so nobody was told to ask for one.

## What the check enforces

`carries(A, head)` (`scripts/review_checks.py`) compares the PR's change at the reviewed commit with its change at the head, path by path (`git diff --raw` from each one's merge base with the base). The verdict carries only when no path differs, or every differing path is prose (Markdown under `docs/`, `openspec/`, `ideas/` or at the top level, not an agent's instructions or `docs/reference/`, mode 100644). A merge of main differs on a path when main changed a file the PR changes (its before blob moves) or the merge's resolution changed it (its after blob moves). So a merge of main carries only when main touched none of the PR's non-prose files; a conflict resolved in code never carries.

## As built

1. **A verdict that does not carry fails `review`**, its title naming the review owed: "APPROVE at A; the change since needs a delta review", or "BLOCK at A; … a fresh review" after a BLOCK. Before, both were `in_progress`. A completed failure is an event (a PR subscription delivers it; the PR reads red), and auto-merge stays armed: the re-confirm's APPROVE posts a newer run, which readers take as the decision (the highest id), as they already do after a BLOCK. A merge group already failed on anything but a pass (`group_check`), unchanged.
2. **No verdict stays in progress** ("waiting for review of <head>"): a PR's reviewer is asked at its open, so the wait has an owner. A retarget voids the reviews before it and reads the same; the coordinator who retargets asks for the review in the same act, and no wait of that kind has been seen. An unreadable permission stays in progress too: rerunning review.yml is its fix, named in its title.
3. **The rule's one line** is the untangle skill's reviewer sentence, reworded rather than added: a merge of main touching the PR's code fails `review` until its merger asks a re-confirm, which reads only its conflicts. The gates page already says a verdict carries only across the same change or a prose delta; it is at its pinned size and needs nothing more. The host brief's line can go.
