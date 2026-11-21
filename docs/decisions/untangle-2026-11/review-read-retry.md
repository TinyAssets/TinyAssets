# The review decision asks a read again after a 5xx (2026-11-21)

`review.yml`'s decision stopped on #5689 (run 44861493637) at a `gh: Server Error (HTTP 502)` from one of its reads, and the trap posted "could not decide". Nothing was wrong with the PR; a person had to rerun the job. Once the founder's ruleset makes `review` required, every such 502 holds a PR until someone reruns it.

1. **Every read the decision makes asks again on a 5xx:** twice more, 5 s then 10 s later. The shell's reads (the PR's head and base, its reviews, its timeline) go through `get` in `review.yml`; the Python reads (the permission read, the check-run listing) go through `_gh_read` in `scripts/drain_review_gate.py`. Each comment names the other, since the two run in different languages.
2. **Anything else fails at once.** A 404 on a permission read is a decision (fail closed), not a passing fault, so only `HTTP 5xx` in gh's error is asked again. A read still failing after three tries stops the decision, and the trap posts the failure as before.
3. **A paged read prints only a whole answer.** `get` holds the output until gh exits 0, so a page that dies mid-listing never reaches `jq -s` beside a retry's full listing.
4. **The POSTs are not retried here.** A verdict posts once; a failed post fails the decision, as `p2-review-gate.md` item 3 decided.
