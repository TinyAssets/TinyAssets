LANE: merge-queue shepherd (repo TinyAssets/TinyAssets, use gh). Get these approved PRs merged to main WITHOUT changing
their reviewed product behavior: #5226 (tracker, docs), #5228 (N1 spec), #5229 (N5/N6 specs), #5230 (API-base
discovery), #5231 (sign-in in context), #5232 (C3), #5233 (C10). Each already carries a lead Drain-Review receipt at
the top of its body for a specific head. Production deploys are disabled; merging does not deploy.
For each PR, until it merges:
- Required checks cancelled by overlapping runs: re-run them (gh run rerun / gh pr checks). Re-enable auto-merge if it
  dropped (gh pr merge N --auto). The "reconcile" check is an external condition (duplicate Shape reconciliation
  issues) and is not required: do not touch it.
- "Diff scope declared" failing: read .github/workflows/pr-scope-guard.yml; fix only the PR body's Lane line/verifies
  ids. If the receipt's head no longer matches (head moved), do NOT write a receipt yourself: list that PR for the lead.
- Real test failures (e.g. #5231 affected-tests 4/4, required-tests, real-browser-proof; #5233 runsc owner-wake job):
  reproduce with python scripts/linux_oracle.py -- <file>. If the failure is caused by the PR, fix it minimally in a
  worktree you create under C:\Users\Jonathan\Projects\wf-shep-<pr> on the PR branch, push, and list the PR for the lead
  to re-receipt (a new head needs a new receipt). If it is flaky or pre-broken on main, re-run once and report it.
- When main moves, update a PR branch only if the queue requires it (gh pr update-branch); that changes the head, so
  list it for the lead to re-receipt.
Report: each PR's final state, every push you made (head SHA and why), and which PRs need a new lead receipt.
You may create worktrees under C:\Users\Jonathan\Projects\wf-shep-*. Remove them when done.

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.
