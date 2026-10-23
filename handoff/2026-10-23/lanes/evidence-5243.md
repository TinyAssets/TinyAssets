Approval of S2 start-path work with an honest registry.
- Cross-family review: DISAGREE_EVIDENCE (HIGH: zero ceiling reached by replacing the probe while two executors with their own admission gates remain; the registry recorded the duplication nowhere).
- Fix 7b87f1d847e, checked by the lead: no false convergence. S2 row restored to drifted with the original probe and its true ceiling (5); S3 now tracks the duplicate executor; bypass checks strengthened; findings 4-5 clarified. The executor merge (one admission gate, yield journaling, error handling) is recorded as unfinished follow-up work in the PR body.
- Validation: failing-first (6 failed, 25 passed), then 31 passed on the Linux oracle; ruff; shape checker.
