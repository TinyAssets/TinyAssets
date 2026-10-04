# Checked jail reservation renewal

## Contract before implementation

Independent design reviewer `quota_split_design_review` approved this bounded
prerequisite on 2026-10-03. It leaves admission, quota, runtime exclusions,
launch bounds, and full-account recovery unchanged. No provider confinement or
process-supervision call sites change.

`storage_accounting.renew_checked` updates only an existing reserved row whose
ID, account and byte count match the handle, under the existing transaction.
Success requires one affected row. Missing/released/committed rows and database
errors return false; renewal never inserts a replacement. An old row that is
still present may renew because its capacity remains charged. Unattributed
handles (no row ID) retain their existing policy. The old `renew` API remains.

`DiskBudget` latches a failed checked renewal and reports `storage_limit` on
that and every later poll. Only successful renewals advance the renewal clock.
A settled budget cannot authorize continued execution.

## Scope and remaining work

This closes continued execution after detected lease loss. It does not provide
hard byte enforcement during supervisor suspension, change polling overshoot,
or fix reservation starvation. PR #4403 remains blocked on preserving recovery
with a hard-bounded private provider runtime and enforced durable write limits.

A later settlement change must publish measurements of both universe_files and
workspaces atomically with releasing the reservation, after complete process
family teardown. Marking dirty and releasing is insufficient because admission
can otherwise use old measurements. That change needs coordinated call sites
and separate design review.

Validation and exact-head review will be recorded before handoff.

## Local validation

- 55 focused jail-disk and storage-accounting tests passed; original tests are
  unchanged, including both grace tests and the long-launch TTL renewal test.
- Both new startup-recovery characterization tests from draft #4408 passed
  against explicitly pinned imports from this implementation worktree.
- Ruff and all pre-commit-scoped invariants passed, including packaged mirrors.
- Local Linux oracle initially could not write Docker's default configuration;
  rerun uses a temporary Docker configuration. No privileged workaround used.
- Independent review is same-family because no second model-family runner is
  available in this environment; it is not the repository's cross-family gate.
