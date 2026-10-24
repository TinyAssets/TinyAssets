SPEC LANE: C12 / S17, knowledge is files, one export (ADR-017, ADR-018; registry row S17 in docs/architecture.md:
"OKF bundle canonical, indexes derived; one export action"; second paths memory/, retrieval/, knowledge/,
daemon_brain.py canonical; bundle seed-only; okf_export.py unwired). Worktree C:\Users\Jonathan\Projects\wf-c12-spec,
branch spec/c12-knowledge-files (from main eb582d82d08). Closed PR #4903 attempted this pre-C6 and extended the
one-shot migration; do not reuse that approach (read it for scope only: gh pr diff 4903 --name-only). Write the spec
for the post-C6 shape: the owner's knowledge is files in the owner's box, indexes are derived and rebuildable inside
the box, one export action produces the bundle, and the second canonical stores are deleted in slices. Name each slice,
what it deletes, and the guard that makes S17 read converged. Spec only: proposal, design, spec delta, tasks; no
product code. Keep it short. Open a PR.
