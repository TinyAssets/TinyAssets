Implementation backlog only; no boxes completed by this docs-only proposal.

## 1. Delivery and verification

- [ ] 1.1 Reconcile historical process work with workspace-node/lease/scheduler owners and define durable process configuration and lifecycle.
- [ ] 1.2 Implement cloud resident supervision with lease generation, restart/backoff and health; test split-brain restart and no host fallback.
- [ ] 1.3 Integrate zero-LLM and owner-model heartbeats through durable scheduler occurrences; verify dedupe, budgets and quiet outcomes.
- [ ] 1.4 Expose owner ta/UI process start/stop/status and event-stream health; verify owner isolation, stop/revoke and account deletion.
- [ ] 1.5 Publish/copy a resident gateway and exercise T1/T3/T10 prerequisites with cloud host-off and restart evidence.
- [ ] 1.6 Run touched and affected-heavy tests, Ruff and strict validation; complete the applicable floor review and Linux oracle for sandbox/process changes (no skip as pass).
- [ ] 1.7 Assert deployed implementation SHA and real-user app proof for the acceptance criteria, then sync this capability; do not mark delivery complete from documentation alone.
