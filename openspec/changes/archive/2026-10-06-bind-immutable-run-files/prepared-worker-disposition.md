# Prepared-worker checkpoint review disposition

2026-09-19: Fable session 38182, exit 0 after 574 seconds, ADAPT on
`28b1a4a3` against `f68a9db8`. Full substantive output is retained alongside.
This is a checkpoint review, not integrated release approval.

Lead accepted all required corrections before integration:

- Check compatible invocation and terminal seams before marker, pending events
  or provider binding; missing seams refuse without execution side effects.
- The local unstarted terminal predicate is exactly `status == queued`.
- Add real cancellation-intent-before-dispatch proof with null start marker.
- A current attempt holding the guard may terminalize after its own marker but
  before invocation. A durable marker found on restart proves neither no effects
  nor kernel emptiness and is never independent recovery authority.
- Merge cloud `af3156ba` non-destructively, then exercise actual hooks rather than
  relabel traced seams as graph proof. Cloud owns queued-only terminal CAS.

The callback consumes passed author/runs connections. The review's WAL read
observation does not authorize new schema initialization or writer helpers under
those transactions. Optional actor-read ordering and secondary-error logging
are bounded follow-ups; nested depth remains an adapter requirement, not new queue.

Separately approved integration addition: existing scoped-reset shared maintenance
barrier outermost (bounded 5 seconds), clean recovery-state check, held through
settlement before execution guard/family/author/runs locks. Never invoke service
writer preparation here: that may create/recover a root. Independent dispatch
versus reset proof is required. No new lock manager or private reentrancy bypass.
