Fix round on PR #5243 (worktree C:\Users\Jonathan\Projects\wf-s2-start, branch conv/s2-one-way-to-start). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-s2.md
The zero ceiling was reached by replacing the probe while two executors with their own admission gates still exist;
the registry now records that duplication nowhere (S3 says "none"). Never lower a ceiling by changing what it measures.
Do the real convergence: merge the two agent executors into the one S3 loop behind session.start, with one admission
gate, deleting the other executor in this PR. Then the honest probe (the old one, or a stricter one that also catches a
genuinely new second path, finding 3) reads zero and structural-guards passes. Route the tools-enabled model calls from
`ta` (finding 4) through session.start or document precisely why they are not agent starts. Tighten finding 5. If the
executor merge is too large for this PR, instead restore the S2 row to drifted with the old probe and its true ceiling,
add a tracked S3 probe for the duplicate executor, and say so in the PR body: honest registry over a converged label.
Failing-first tests; push to the same branch.
