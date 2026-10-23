Fix round on PR #5238 (worktree C:\Users\Jonathan\Projects\wf-deploy-fix, branch fix/deploy-box-bundle). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-deploy.md
Goal: "nothing reports success over a mixed tree" holds on EVERY path. Fix each finding with a test that fails first:
1 HIGH: every failure exit after install_bundle runs the restore (one trap or one exit function, not per-site calls).
2 HIGH: the host snapshot covers every path the host bundle installs, derived from the same install manifest
  (boxhostd_install_files.py / host_service_bundle.py), so a new installed directory can never be missed again;
  test that snapshot paths == install paths.
3: write the dirty marker BEFORE destructive restore steps, clear it only after a verified restore; never rm -rf a
  live tree before the copy-back is staged (stage then rename).
4: make the env restore match DEPLOY.md, or correct DEPLOY.md to what it does, and restore env with the bundle.
5-6: queue time must not consume the broker's request deadline; waiting for a slot must not hold the broker's shared
  thread pool (wait asynchronously, or bound concurrent waiters below the pool size with request wakes reserved).
7: the readiness signal reports ready only when no post-drain lifecycle work is pending or running (count pending
  work, not just active calls); bounded wait.
8: the box image selection (/var/lib/boxhostd/state/images.json) is part of the snapshot/restore.
9-10: fix tests that skip or mock away the behavior they claim to cover, and the small issues.
Push to the same branch.
