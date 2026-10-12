# Active Patches goal: limits and five app capability gaps

Owner directive, September 8, 2026 PDT:

> add closing the 5 gaps to your goal and the webapp agent messaging that the gaps have been closed is the completion criteria

This extends, rather than replaces or narrows, the active objective:
**finish and deploy live the limit simplification**. The full resource-policy
end state remains defined in `consolidation-decision.md`; shipping one removed
cap is not completion. The five gaps reported at 21:39 PDT are now in scope:

- [x] Documented workflow node editing works through the app agent's exposed tools.
- [x] Explicit workspace discard works without the reported false ancestry refusal.
- [x] Completed runs expose ordinary returned values and generated text to the agent.
- [x] Failed runs report failed code-node state accurately, not still running.
- [x] The agent can cancel queued/running workflows through an exposed control.

September 8, 2026 23:30-23:31 PDT rendered retest on deployed 008269579327:
the app explicitly confirmed the first four fixes and working cancellation, but
still reports a cancelled run's code node as running. Keep cancellation closure
open until that follow-up is fixed and the app says the gaps are closed. Fresh
webhook delivery/cleanup also passed (HTTP 200/204); all nine older checklist
checks passed. Evidence: 2026-09-09-workflow-control-gaps-proof.md (archived in git).

September21 2026 update: the later cancelled-node fix has deployed and rendered
proof; after PR3900's model-edit follow-up deployed5394efd, the app20:56PDT
explicitly says All five are working / Nothing is blocked. This satisfies the
five-gap subgoal, not the full resource-policy goal. Receipt:
`2026-09-21-served-node-policy-deployment.md (archived in git)`.
Each checkbox requires deployed platform behavior plus the agent's rendered
retest confirmation, not source inspection or unit tests alone. Preserve
authorization, tenant isolation, real resource guards and cancellation lifecycle.

## Completion gate

After the required platform fixes are deployed live, continue the owner-requested
ordinary webapp conversation loop. The checklist retest prompt is exactly:

> Retest your workflow checklist

Use the agent's response to repair platform capabilities and redeploy as needed.
For user-owned workflow problems, ask the app agent to fix them or identify its
blocker; do not edit private workflows by operator hands. The owner's prior
permission to converse with the app and this new explicit retest/completion
direction govern this loop; the immediately preceding read-only request did not
itself authorize sending a prompt.

The goal is complete only when the full limit simplification is live and verified
and the webapp agent has explicitly messaged that all five gaps are closed.
Record the rendered message, timestamp, current deploy evidence and exact scope.
An older checklist PASS that simultaneously lists these gaps does not satisfy
this gate. Passing tests, merged PRs, canaries or claims by the coding agent do
not substitute for the app agent's confirmation.

The broader resource-policy storage-responsibility decision remains as recorded
in the parent design; this scope extension does not silently decide it or approve
a PLAN change, schema migration, upstream spending or broader credential access.
