Adversarial review of branch conv/source-records at its current origin head (worktree
C:\Users\Jonathan\Projects\wf-source-records; read with `git diff 9ab39b4b886..origin/conv/source-records`). Read-only.

READ FIRST: openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md. Per that design, floor-class review covers the router/map, the
credential/egress broker and the waker. For each finding, state whether it can exist in the target shape; a finding
that is not a defect in those three components is an in-box issue and only matters if it breaks correctness for the
owner.

This range reshapes the branch to the target shape after an earlier review (result in
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review2.md):
- subscription CLI login state moves into broker custody, leased into a per-run tmpfs, refresh written back,
  legacy persistent files migrated into custody on first use, disconnect cancels pending logins and deletes custody
  (broker/subscription_custody.py, broker/vault.py, broker/server.py, providers/subscription_lease.py,
  providers/subscription_runtime.py, onboarding/subscription_connect.py);
- address discovery moves from the broker into the owner's box; the broker keeps the OAuth exchange, registration and
  deposit;
- runtime "dead member" pruning is deleted and replaced by a one-shot offline repair (cutover_assignment_repair.py);
- the box image installs CLIs only from committed lockfiles with digests;
- two deploy-credential commits folded from another PR.
Try to BREAK: one owner reading or overwriting another owner's custody entry or lease; a lease surviving the run or
landing in persistent storage; lost refresh tokens under concurrent runs; disconnect leaving anything; the broker now
running owner business logic or holding a host path to owner data; discovery egress bypassing the broker's public-
address and admitted-owner check (including redirects); the repair deleting a live member or running against a live
volume; image build trusting anything not committed. Also verify review2 findings 1-6 are gone or impossible now.
Report each finding with file:line, a concrete scenario and severity. End with exactly one line:
VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE.
HARD CONSTRAINTS: no sub-agents, no peer_agent.py, no new worktree; read-only; run at most the test files you need.
Budget about 30 minutes.
