Draft PR [#5235](https://github.com/TinyAssets/TinyAssets/pull/5235) is open against `main` at head `6825f06fb34d2939f2922c4bc756a6e2793f4db8`. I did **not** deploy.

The draft adds bounded inbound verification, fixed Slack installation extraction after HMAC, broker-side named-secret lookup, installation authority, box-local receipt storage, proof-header stripping, tests, and the [founder host-action row](/C:/Users/Jonathan/Projects/wf-n1-impl/docs/host-actions.md).

The requested targeted Linux set passed **67/67**; the N1 and router rerun passed **50/50**. Ruff and commit hooks passed.

**N1 is not complete.** The route still uses the old direct dispatcher. Receipt-keyed durable `session.start`, route cutover, breaker, wake reconciliation, migration, and the remaining spec proofs are undone. The current session path lacks the required receipt-keyed durable admission, so acknowledging events through the new route now would risk loss or duplicate sessions. The OpenSpec checklist remains unchecked, and the PR is explicitly marked in progress.