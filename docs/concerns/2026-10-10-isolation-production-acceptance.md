---
severity: P2
title: Isolation production acceptance remains unrecorded
filed: '2026-10-10'
summary: Cutover is deployed and specs are synced, but second-account production refusals and the remaining credentialed acceptance are not claimed.
---

# Isolation production acceptance remains unrecorded

The per-role-uid-split implementation is archived at the founder's request after
cutover. `python scripts/deployed_sha.py --assert-contains 5acadd7a2bf` passed on
2026-10-10: production reported `6bd296c1be2f`. The archived task history retains
the local restored-backup, migration, cell and regression proofs.

The archive does not turn the old unchecked acceptance items into passes:
- 2.5: full credentialed Codex and Claude turns and every class on the restored clone.
- 2.6/3.2: production cross-owner refusal, founder app-agent acceptance and the
  maintenance-window evidence beyond the deployed-SHA assertion above.
- 3.1: Verify-conditioned concern deletions from design ?5 remain conditional;
  keep those concerns until production acceptance supports their deletion.

The second-account refusal probe is in `docs/host-actions.md`; supply tester B,
then follow `docs/ops/owner-split-cutover-runbook.md` ?6.5. Record the refusal
results and the remaining app/provider acceptance, then remove this concern.
Spec sync and archive are complete; production acceptance is not claimed.
